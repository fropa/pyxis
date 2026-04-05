"""
AI Engine — the brain of Pyxis.

Entry point: analyze_event()
  1. Run fingerprint-aware rate detection (detector.py) — no more keyword soup
  2. Check for known failure patterns (patterns.py) for severity + context hints
  3. Deduplicate against open incidents via Redis
  4. Open incident if needed
  5. Enqueue RCA job to ARQ worker (retried on failure)

_run_rca() is also called directly from the ARQ task (tasks/rca.py).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import anthropic
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import publish_event, get_redis
from app.models.event import LogEvent
from app.models.incident import Incident, IncidentNode
from app.ai.rag import retrieve_relevant_chunks
from app.ai.correlator import gather_correlated_context
from app.ai.detector import (
    should_open_incident,
    find_open_incident_for_fingerprint,
    register_open_incident,
)
from app.ai.patterns import build_pattern_context, highest_severity

settings = get_settings()
_anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ── Worker pool (lazy singleton) ─────────────────────────────────────────────

_arq_pool = None

async def _get_arq_pool():
    global _arq_pool
    if _arq_pool is None:
        url = settings.REDIS_URL.replace("redis://", "")
        if "@" in url:
            _, url = url.split("@", 1)
        host, port = url.split(":") if ":" in url else (url, "6379")
        _arq_pool = await create_pool(RedisSettings(host=host, port=int(port)))
    return _arq_pool


# ── Main entry point ──────────────────────────────────────────────────────────

async def analyze_event(event: LogEvent, tenant_id: str, db: AsyncSession) -> None:
    if not event.fingerprint:
        return

    r = await get_redis()

    fire, reason = await should_open_incident(
        tenant_id=tenant_id,
        event_id=event.id,
        fingerprint=event.fingerprint,
        message=event.message or "",
        level=event.level,
        redis=r,
    )

    if not fire:
        return

    event.is_anomaly = True
    await db.commit()

    # Publish raw anomaly signal immediately (before RCA — fast feedback)
    await publish_event(tenant_id, {
        "type": "anomaly_detected",
        "event_id": event.id,
        "node_id": event.node_id,
        "message": event.message,
        "level": event.level,
        "source": event.source,
        "fingerprint": event.fingerprint,
        "detection_reason": reason,
        "timestamp": event.event_ts.isoformat(),
    })

    # Deduplication: reuse open incident for same fingerprint
    existing_incident_id = await find_open_incident_for_fingerprint(
        tenant_id, event.fingerprint, r
    )

    if existing_incident_id:
        event.incident_id = existing_incident_id
        await db.commit()
        return  # RCA already running or done for this incident

    # Open new incident
    incident = await _open_incident(event, tenant_id, db)
    await register_open_incident(tenant_id, event.fingerprint, incident.id, r)

    # Enqueue RCA to ARQ worker (not BackgroundTasks — retried on failure)
    pool = await _get_arq_pool()
    await pool.enqueue_job("run_rca_task", incident.id, event.id, tenant_id)


# ── Incident management ───────────────────────────────────────────────────────

async def _open_incident(event: LogEvent, tenant_id: str, db: AsyncSession) -> Incident:
    severity = highest_severity(event.message or "", event.fingerprint or "")

    incident = Incident(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        title=_incident_title(event),
        severity=severity,
        status="open",
        started_at=event.event_ts,
    )
    db.add(incident)
    await db.flush()

    if event.node_id:
        db.add(IncidentNode(incident_id=incident.id, node_id=event.node_id, role="root_cause"))

    event.incident_id = incident.id
    await db.commit()
    await db.refresh(incident)

    await publish_event(tenant_id, {
        "type": "incident_opened",
        "incident_id": incident.id,
        "title": incident.title,
        "severity": incident.severity,
        "node_id": event.node_id,
        "fingerprint": event.fingerprint,
    })

    return incident


# ── RCA via Claude + RAG + patterns ──────────────────────────────────────────

async def _run_rca(
    incident: Incident, trigger_event: LogEvent, tenant_id: str, db: AsyncSession
) -> None:
    # 1. Recent events attached to this incident
    result = await db.execute(
        select(LogEvent)
        .where(LogEvent.tenant_id == tenant_id, LogEvent.incident_id == incident.id)
        .order_by(desc(LogEvent.event_ts))
        .limit(50)
    )
    related_events = result.scalars().all()
    log_context = "\n".join(
        f"[{e.event_ts.isoformat()}] [{e.level.upper()}] [{e.source}] {e.message}"
        for e in reversed(related_events)
    )

    # 2. Cross-source correlation (pipeline ↔ K8s ↔ syslog)
    correlation = await gather_correlated_context(trigger_event, tenant_id, db)
    cross_source_context = _format_correlation(correlation)

    # 3. Known pattern hints (no IaC needed)
    pattern_context = build_pattern_context(
        trigger_event.message or "", trigger_event.fingerprint or ""
    )

    # 4. RAG: customer IaC chunks (empty for starter tier — gracefully absent)
    rag_query = f"{incident.title}\n{trigger_event.message}"
    chunks = await retrieve_relevant_chunks(rag_query, tenant_id, db)
    iac_context = ""
    if chunks:
        iac_context = "\n\n---\n".join(
            f"# {c['source_type']}: {c['file_path']}\n{c['content']}"
            for c in chunks
        )

    # 5. Past incidents for pattern memory
    past_result = await db.execute(
        select(Incident)
        .where(
            Incident.tenant_id == tenant_id,
            Incident.id != incident.id,
            Incident.status == "resolved",
            Incident.rca_summary.isnot(None),
        )
        .order_by(desc(Incident.started_at))
        .limit(5)
    )
    past_incidents = past_result.scalars().all()
    past_context = "\n".join(
        f"- [{p.started_at.date()}] {p.title}: {p.rca_summary}"
        for p in past_incidents
    ) or "(no prior incidents)"

    # 6. Call Claude
    system_prompt = (
        "You are an expert SRE and DevOps engineer inside an infrastructure observability platform. "
        "Perform root cause analysis for the incident below. Be precise and specific. "
        "If the diagnostic context includes specific checks, work through them. "
        "If IaC configuration is present, cite file paths. "
        "If it's not, focus on the log evidence and known patterns. "
        "Format your response as Markdown with clear sections."
    )

    user_prompt = f"""## Incident
**Title:** {incident.title}
**Severity:** {incident.severity}
**Started:** {incident.started_at.isoformat()}
**Fingerprint:** {trigger_event.fingerprint}

## Log Events
```
{log_context or "(no logs collected yet)"}
```

## Cross-Source Correlation (pipeline ↔ K8s ↔ syslog)
{cross_source_context}

## Known Failure Pattern Diagnostics
{pattern_context or "(no specific pattern matched — use general SRE reasoning)"}

## IaC / Pipeline Configuration (Pro tier — empty if not indexed)
{iac_context or "_Not available in starter tier. Connect repos in Knowledge Base for file-level analysis._"}

## Past Similar Incidents
{past_context}

---
Please provide:
1. **Root Cause** — what specifically caused this, based on available evidence
2. **Affected Components** — which services/nodes are impacted
3. **Immediate Fix** — specific commands or steps to resolve now
4. **Prevention** — what to change to prevent recurrence
5. **Confidence** — your confidence level (0–100%) and what would increase it"""

    message = await _anthropic.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    rca_text = message.content[0].text
    confidence = _extract_confidence(rca_text)

    # Find similar past incident
    similar_id = _find_similar_incident(incident, past_incidents)

    incident.rca_full = rca_text
    incident.rca_summary = _extract_summary(rca_text)
    incident.rca_confidence = confidence
    incident.cited_knowledge = [c["file_path"] for c in chunks]
    incident.similar_incident_id = similar_id
    await db.commit()

    await publish_event(tenant_id, {
        "type": "rca_ready",
        "incident_id": incident.id,
        "rca_summary": incident.rca_summary,
        "confidence": confidence,
        "cited_files": incident.cited_knowledge,
        "similar_incident_id": similar_id,
    })

    # Send notifications (non-blocking, best-effort)
    try:
        from app.notifications.dispatcher import dispatch_incident_notification
        await dispatch_incident_notification(incident, tenant_id)
    except Exception:
        pass  # notifications should never break RCA


# ── Helpers ───────────────────────────────────────────────────────────────────

def _incident_title(event: LogEvent) -> str:
    msg = (event.message or "Unknown error")[:120]
    return f"[{event.source}] {msg}"


def _extract_summary(rca_text: str) -> str:
    """Pull the Root Cause section as the summary."""
    import re
    m = re.search(r"##\s*\d*\.?\s*Root Cause\s*\n+(.*?)(?=\n##|\Z)", rca_text, re.S | re.I)
    if m:
        return m.group(1).strip()[:500]
    return rca_text.split("\n")[0][:500]


def _extract_confidence(rca_text: str) -> float:
    import re
    m = re.search(r"confidence[:\s]+(\d+)%?", rca_text, re.IGNORECASE)
    if m:
        return min(int(m.group(1)), 100) / 100.0
    return 0.7


def _find_similar_incident(incident: Incident, past: list[Incident]) -> str | None:
    if not past:
        return None
    # Simple title similarity — good enough for starter
    words = set(incident.title.lower().split())
    for p in past:
        p_words = set(p.title.lower().split())
        overlap = len(words & p_words) / max(len(words), 1)
        if overlap > 0.5:
            return p.id
    return None


def _format_correlation(correlation: dict) -> str:
    lines = []
    if correlation.get("correlation_notes"):
        lines.append(correlation["correlation_notes"])
        lines.append("")
    k8s_rc = correlation.get("k8s_root_cause_events", [])
    if k8s_rc:
        lines.append("### K8s events likely causing this failure:")
        for e in k8s_rc:
            lines.append(f"  [{e['ts']}] [{e['source']}] {e['message']}")
    rel_pipe = correlation.get("related_pipeline_events", [])
    if rel_pipe:
        lines.append("### Related pipeline events:")
        for e in rel_pipe:
            lines.append(f"  [{e['ts']}] [{e['source']}] {e['message']}")
    return "\n".join(lines) if lines else "(no cross-source correlation data)"
