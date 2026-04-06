import uuid
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, desc, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.incident import Incident
from app.models.runbook import Runbook
from app.models.tenant import Tenant

router = APIRouter()
settings = get_settings()
_anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class IncidentOut(BaseModel):
    id: str
    title: str
    severity: str
    status: str
    started_at: datetime
    resolved_at: datetime | None
    rca_summary: str | None
    rca_full: str | None
    rca_confidence: float | None
    cited_knowledge: list[Any]
    similar_incident_id: str | None
    postmortem: str | None = None
    parent_incident_id: str | None = None
    storm_size: int = 1

    class Config:
        from_attributes = True


class IncidentUpdate(BaseModel):
    status: str | None = None
    rca_summary: str | None = None


@router.get("/heatmap")
async def incident_heatmap(
    days: int = 90,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(
            cast(Incident.started_at, Date).label("date"),
            func.count(Incident.id).label("count"),
        )
        .where(Incident.tenant_id == tenant.id, Incident.started_at >= since)
        .group_by(cast(Incident.started_at, Date))
        .order_by(cast(Incident.started_at, Date))
    )
    return [{"date": str(row.date), "count": row.count} for row in result]


@router.get("/", response_model=list[IncidentOut])
async def list_incidents(
    limit: int = 50,
    offset: int = 0,
    status_filter: str | None = None,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    q = select(Incident).where(Incident.tenant_id == tenant.id).order_by(desc(Incident.started_at))
    if status_filter:
        q = q.where(Incident.status == status_filter)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant.id)
    )
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.patch("/{incident_id}", response_model=IncidentOut)
async def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    background_tasks: BackgroundTasks,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant.id)
    )
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    resolving = payload.status == "resolved" and incident.status != "resolved"

    if payload.status:
        incident.status = payload.status
        if payload.status == "resolved":
            incident.resolved_at = datetime.utcnow()
    if payload.rca_summary:
        incident.rca_summary = payload.rca_summary

    await db.commit()
    await db.refresh(incident)

    # Auto-generate runbook when incident is resolved and has RCA
    if resolving and incident.rca_full:
        background_tasks.add_task(
            _generate_runbook, incident.id, incident.tenant_id, incident.title, incident.rca_full
        )

    return incident


async def _generate_runbook(incident_id: str, tenant_id: str, title: str, rca_full: str) -> None:
    """Background task: ask Claude to write a runbook from the RCA."""
    from app.core.database import AsyncSessionLocal

    prompt = f"""Based on this incident RCA, write a concise operational runbook.

## Incident: {title}

## RCA Analysis
{rca_full}

Write a runbook with these sections:
1. **Overview** — what this incident is and when it typically occurs
2. **Detection** — how to confirm this is happening (commands, metrics, logs to check)
3. **Immediate Response** — step-by-step fix commands
4. **Verification** — how to confirm the fix worked
5. **Prevention** — configuration changes or monitoring to prevent recurrence

Be specific with commands. Use code blocks for all CLI commands."""

    try:
        message = await _anthropic.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2048,
            system="You are an expert SRE writing operational runbooks. Be precise, actionable, and use real commands.",
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.content[0].text

        # Extract a clean title from the first heading or use incident title
        m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        rb_title = m.group(1) if m else f"Runbook: {title[:100]}"

        async with AsyncSessionLocal() as db:
            # Upsert: if runbook already exists for this incident, replace it
            existing = await db.execute(
                select(Runbook).where(Runbook.incident_id == incident_id)
            )
            rb = existing.scalar_one_or_none()
            if rb:
                rb.title = rb_title
                rb.content = content
            else:
                db.add(Runbook(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    incident_id=incident_id,
                    title=rb_title,
                    content=content,
                ))
            await db.commit()
    except Exception:
        pass  # runbook generation should never break the resolve flow


class PostmortemResponse(BaseModel):
    postmortem: str


@router.post("/{incident_id}/postmortem", response_model=PostmortemResponse)
async def generate_postmortem(
    incident_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant.id)
    )
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Return cached post-mortem if already generated
    if incident.postmortem:
        return PostmortemResponse(postmortem=incident.postmortem)

    duration = ""
    if incident.resolved_at and incident.started_at:
        secs = int((incident.resolved_at - incident.started_at.replace(tzinfo=timezone.utc)
                    if incident.started_at.tzinfo is None
                    else incident.resolved_at - incident.started_at).total_seconds())
        duration = f"{secs // 60}m {secs % 60}s"

    prompt = f"""Write a professional incident post-mortem document.

## Incident Data
- **Title:** {incident.title}
- **Severity:** {incident.severity}
- **Status:** {incident.status}
- **Started:** {incident.started_at.isoformat()}
- **Resolved:** {incident.resolved_at.isoformat() if incident.resolved_at else "still open"}
- **Duration:** {duration or "unknown"}

## Root Cause Analysis
{incident.rca_full or incident.rca_summary or "No RCA available"}

Write a post-mortem with these sections:
1. **Executive Summary** — 2-3 sentences for non-technical stakeholders
2. **Timeline** — key events with timestamps (infer from the data above)
3. **Root Cause** — technical explanation
4. **Impact** — what was affected and for how long
5. **What Went Well** — detection, response, communication
6. **What Could Be Improved** — gaps identified
7. **Action Items** — concrete follow-up tasks with suggested owners (use checkboxes)

Be professional, blameless, and factual."""

    message = await _anthropic.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2048,
        system="You are writing a blameless post-mortem for an engineering team. Be factual, professional, and constructive.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text

    incident.postmortem = text
    await db.commit()

    return PostmortemResponse(postmortem=text)
