"""
On-call assistant — plain-English answers to infra questions.

Claude gets full context: open incidents, service health, recent deploys,
topology, and recent anomalies. Perfect for 3am "what's wrong right now?"
"""
from datetime import datetime, timedelta, timezone

import anthropic
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.incident import Incident
from app.models.span import Span
from app.models.deploy_event import DeployEvent
from app.models.topology import Node
from app.models.tenant import Tenant

router = APIRouter()
settings = get_settings()
_anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class AssistantRequest(BaseModel):
    question: str
    history: list[ChatMessage] = []


class AssistantResponse(BaseModel):
    answer: str


@router.post("/chat", response_model=AssistantResponse)
async def chat(
    payload: AssistantRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    context = await _build_context(tenant.id, db)

    system = f"""You are Pyxis, an on-call infrastructure assistant for a DevOps/SRE team.
You have real-time visibility into the team's infrastructure. Answer questions clearly and concisely.
Be direct — the person asking you is likely dealing with an incident right now.
If you see something alarming in the context, proactively mention it even if not asked.
Use Markdown for formatting. Prefer bullet points over paragraphs.

## Current Infrastructure State
{context}"""

    messages = [{"role": m.role, "content": m.content} for m in payload.history]
    messages.append({"role": "user", "content": payload.question})

    response = await _anthropic.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    )

    return AssistantResponse(answer=response.content[0].text)


async def _build_context(tenant_id: str, db: AsyncSession) -> str:
    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)
    since_24h = now - timedelta(hours=24)

    # Open incidents
    inc_result = await db.execute(
        select(Incident)
        .where(Incident.tenant_id == tenant_id, Incident.status == "open")
        .order_by(desc(Incident.started_at))
        .limit(10)
    )
    open_incidents = inc_result.scalars().all()

    # Recent resolved
    resolved_result = await db.execute(
        select(Incident)
        .where(
            Incident.tenant_id == tenant_id,
            Incident.status == "resolved",
            Incident.resolved_at >= since_24h,
        )
        .order_by(desc(Incident.resolved_at))
        .limit(5)
    )
    recent_resolved = resolved_result.scalars().all()

    # Service health from spans
    span_result = await db.execute(
        select(Span.service, Span.status, Span.duration_ms)
        .where(Span.tenant_id == tenant_id, Span.started_at >= since_1h, Span.parent_span_id.is_(None))
        .order_by(desc(Span.started_at))
        .limit(500)
    )
    spans = span_result.all()

    # Aggregate per service
    service_stats: dict[str, dict] = {}
    for s in spans:
        if s.service not in service_stats:
            service_stats[s.service] = {"total": 0, "errors": 0, "durations": []}
        service_stats[s.service]["total"] += 1
        if s.status == "error":
            service_stats[s.service]["errors"] += 1
        service_stats[s.service]["durations"].append(s.duration_ms)

    # Recent deploys
    deploy_result = await db.execute(
        select(DeployEvent)
        .where(DeployEvent.tenant_id == tenant_id, DeployEvent.deployed_at >= since_24h)
        .order_by(desc(DeployEvent.deployed_at))
        .limit(10)
    )
    deploys = deploy_result.scalars().all()

    # Nodes
    node_result = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.deleted_at.is_(None))
    )
    nodes = node_result.scalars().all()
    down_nodes = [n for n in nodes if n.status == "down"]
    degraded_nodes = [n for n in nodes if n.status == "degraded"]

    # Build context string
    lines = []

    lines.append(f"**Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Total nodes:** {len(nodes)} ({len(down_nodes)} down, {len(degraded_nodes)} degraded)")

    lines.append(f"\n### Open Incidents ({len(open_incidents)})")
    if open_incidents:
        for inc in open_incidents:
            age = int((now - inc.started_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)
            storm = f" [STORM ×{inc.storm_size}]" if inc.storm_size > 1 else ""
            lines.append(f"- **{inc.severity.upper()}**{storm} {inc.title} _(open {age}m)_")
            if inc.rca_summary:
                lines.append(f"  → {inc.rca_summary[:120]}")
    else:
        lines.append("- None — all clear ✓")

    if recent_resolved:
        lines.append(f"\n### Recently Resolved (last 24h)")
        for inc in recent_resolved:
            lines.append(f"- {inc.title} — {inc.rca_summary[:80] if inc.rca_summary else 'no RCA'}")

    if service_stats:
        lines.append("\n### Service Health (last 1h)")
        for svc, stats in service_stats.items():
            err_rate = stats["errors"] / max(stats["total"], 1) * 100
            p99 = sorted(stats["durations"])[int(len(stats["durations"]) * 0.99)] if stats["durations"] else 0
            status = "🔴" if err_rate > 20 else "🟡" if err_rate > 5 else "🟢"
            lines.append(f"- {status} **{svc}** — {stats['total']} req, {err_rate:.1f}% err, p99={p99:.0f}ms")

    if deploys:
        lines.append("\n### Recent Deploys")
        for d in deploys:
            ago = int((now - d.deployed_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)
            lines.append(f"- {d.service} {d.version or ''} by {d.deployed_by or 'unknown'} ({ago}m ago, env={d.environment})")

    if down_nodes:
        lines.append("\n### Down Nodes ⚠️")
        for n in down_nodes:
            lines.append(f"- {n.name} ({n.kind})")

    return "\n".join(lines)
