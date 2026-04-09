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
import logging
import uuid
from datetime import datetime, timedelta, timezone

import anthropic
from arq import create_pool

log = logging.getLogger(__name__)
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
from app.ai.storm_detector import check_and_group_storm

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

    log.debug("analyze_event: fp=%s fire=%s reason=%s", event.fingerprint[:16], fire, reason)

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
    log.info("Opened incident %s: %s", incident.id, incident.title)

    # Storm detection: group related incidents under one parent
    is_storm_child = await check_and_group_storm(incident, tenant_id, r, db)
    if is_storm_child:
        log.info("Incident %s is a storm child — skipping RCA", incident.id)
        return

    # Enqueue RCA to ARQ worker (not BackgroundTasks — retried on failure)
    pool = await _get_arq_pool()
    await pool.enqueue_job("run_rca_task", incident.id, event.id, tenant_id)
    log.info("Enqueued RCA job for incident %s", incident.id)


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

async def _collect_evidence_logs(
    incident: Incident, tenant_id: str, db: AsyncSession
) -> dict[str, list[str]]:
    """
    Collect error/warning log lines from ALL services in a ±30-minute window
    around the incident start. Returns {service_label: [raw log lines]}.
    """
    window_start = incident.started_at - timedelta(minutes=30)
    window_end   = incident.started_at + timedelta(minutes=30)

    from sqlalchemy import and_, or_
    result = await db.execute(
        select(LogEvent)
        .where(
            and_(
                LogEvent.tenant_id == tenant_id,
                LogEvent.event_ts >= window_start,
                LogEvent.event_ts <= window_end,
                or_(
                    LogEvent.level.in_(["error", "critical", "fatal", "warn", "warning"]),
                    LogEvent.is_anomaly.is_(True),
                ),
            )
        )
        .order_by(LogEvent.event_ts)
        .limit(300)
    )
    events = result.scalars().all()

    # Group by service: prefer node_name, fall back to source
    evidence: dict[str, list[str]] = {}
    for e in events:
        label = e.node_name or e.source or "unknown"
        line = f"[{e.event_ts.isoformat()}] [{e.level.upper()}] {e.message}"
        evidence.setdefault(label, []).append(line)

    # Cap each service to 30 lines (most recent = most relevant)
    for svc in evidence:
        evidence[svc] = evidence[svc][-30:]

    return evidence


def _format_evidence_for_prompt(evidence: dict[str, list[str]]) -> str:
    if not evidence:
        return "(no error/warning logs found in the ±30-minute window)"
    parts = []
    for svc, lines in sorted(evidence.items()):
        parts.append(f"### {svc} ({len(lines)} error/warn entries)")
        parts.append("```")
        parts.extend(lines)
        parts.append("```")
    return "\n".join(parts)


async def _run_rca(
    incident: Incident, trigger_event: LogEvent, tenant_id: str, db: AsyncSession
) -> None:
    # 1. Collect raw evidence: error/warn logs from ALL services in ±30min window
    evidence = await _collect_evidence_logs(incident, tenant_id, db)
    evidence_prompt = _format_evidence_for_prompt(evidence)

    # 2. Also include events directly linked to this incident (may have info outside ±30min)
    result = await db.execute(
        select(LogEvent)
        .where(LogEvent.tenant_id == tenant_id, LogEvent.incident_id == incident.id)
        .order_by(desc(LogEvent.event_ts))
        .limit(20)
    )
    linked_events = result.scalars().all()
    linked_context = "\n".join(
        f"[{e.event_ts.isoformat()}] [{e.level.upper()}] [{e.source}] {e.message}"
        for e in reversed(linked_events)
    )
    log_context = linked_context  # kept for compatibility below

    # 3. Cross-source correlation (pipeline ↔ K8s ↔ syslog)
    correlation = await gather_correlated_context(trigger_event, tenant_id, db)
    cross_source_context = _format_correlation(correlation)

    # 4. Known pattern hints (no IaC needed)
    pattern_context = build_pattern_context(
        trigger_event.message or "", trigger_event.fingerprint or ""
    )

    # 5. RAG: customer IaC chunks (empty for starter tier — gracefully absent)
    rag_query = f"{incident.title}\n{trigger_event.message}"
    chunks = await retrieve_relevant_chunks(rag_query, tenant_id, db)
    iac_context = ""
    if chunks:
        iac_context = "\n\n---\n".join(
            f"# {c['source_type']}: {c['file_path']}\n{c['content']}"
            for c in chunks
        )

    # 6. Past incidents for pattern memory
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

    log.info("_run_rca: calling Claude for incident %s (model=%s)", incident.id, settings.CLAUDE_MODEL)

    # 7. Call Claude
    system_prompt = (
        "You are an expert SRE and DevOps engineer inside an infrastructure observability platform. "
        "Perform root cause analysis for the incident below. Be precise and specific. "
        "You have been given RAW LOG LINES from every affected service — use them as your primary evidence. "
        "Quote specific log lines when making claims (wrap in backticks). Do not speculate beyond what the logs show. "
        "If the diagnostic context includes specific checks, work through them. "
        "If IaC configuration is present, cite file paths. "
        "ALWAYS include a Diagnostic Commands section with real, runnable shell commands "
        "(kubectl, systemctl, journalctl, docker, curl, etc.) the on-call engineer should run first. "
        "Format your response as Markdown with clear sections."
    )

    user_prompt = f"""## Incident
**Title:** {incident.title}
**Severity:** {incident.severity}
**Started:** {incident.started_at.isoformat()}
**Fingerprint:** {trigger_event.fingerprint}

## Raw Log Evidence (±30 min window, errors/warnings from ALL services)
{evidence_prompt}

## Trigger Event (what opened this incident)
```
[{trigger_event.event_ts.isoformat()}] [{trigger_event.level.upper()}] [{trigger_event.source}] {trigger_event.message}
```

## Other Linked Log Events
```
{log_context or "(none)"}
```

## Cross-Source Correlation (pipeline ↔ K8s ↔ syslog)
{cross_source_context}

## Known Failure Pattern Diagnostics
{pattern_context or "(no specific pattern matched — use general SRE reasoning)"}

## IaC / Pipeline Configuration
{iac_context or "_Not available. Connect repos in Knowledge Base for file-level analysis._"}

## Past Similar Incidents
{past_context}

---
Instructions:
- Base ALL claims on specific log lines from the evidence above. Quote them.
- If a service has no errors in the window, say so explicitly.
- Do NOT fabricate log content or invent service states.

Please provide:
1. **Root Cause** — what specifically caused this, citing exact log lines as evidence
2. **Affected Components** — which services show errors in the logs above
3. **Diagnostic Commands** — exact shell commands to run RIGHT NOW to confirm and investigate (use code blocks)
4. **Immediate Fix** — specific commands or steps to resolve
5. **Prevention** — what to change to prevent recurrence
6. **Confidence** — your confidence level (0–100%) and what evidence would increase it"""

    try:
        message = await _anthropic.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        rca_text = message.content[0].text
        log.info("_run_rca: Claude responded (%d chars) for incident %s", len(rca_text), incident.id)
    except anthropic.AuthenticationError as e:
        log.error("_run_rca: Anthropic auth error — check ANTHROPIC_API_KEY: %s", e)
        incident.rca_full = "**RCA failed: Anthropic API key is invalid or missing.**\n\nFix: update `ANTHROPIC_API_KEY` in `backend/.env` and restart the backend."
        incident.rca_summary = "RCA failed: invalid Anthropic API key"
        await db.commit()
        return
    except anthropic.PermissionDeniedError as e:
        log.error("_run_rca: Anthropic permission denied (check credit balance): %s", e)
        incident.rca_full = "**RCA failed: Anthropic API credit balance is too low.**\n\nFix: top up at https://console.anthropic.com"
        incident.rca_summary = "RCA failed: insufficient Anthropic credits"
        await db.commit()
        return
    except Exception as e:
        log.error("_run_rca: Claude API call failed for incident %s: %s", incident.id, e)
        raise  # let ARQ retry

    confidence = _extract_confidence(rca_text)

    # Find similar past incident
    similar_id = _find_similar_incident(incident, past_incidents)

    incident.rca_full = rca_text
    incident.rca_summary = _extract_summary(rca_text)
    incident.rca_confidence = confidence
    incident.cited_knowledge = [c["file_path"] for c in chunks]
    incident.similar_incident_id = similar_id
    incident.evidence_logs = evidence  # raw log lines grouped by service
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
