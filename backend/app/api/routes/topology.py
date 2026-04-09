"""
Topology graph endpoints — consumed by the React Flow canvas.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.tenant import Tenant
from app.models.topology import Node, Edge
from app.models.event import LogEvent

router = APIRouter()


DEGRADED_THRESHOLD_SECONDS = 90
SILENT_THRESHOLD_SECONDS = 180


def _compute_status(node: Node) -> str:
    """
    Compute real-time effective status from last_heartbeat_at.
    Only overrides stored status for agent-managed nodes (those that have ever sent a heartbeat).
    Auto-discovered nodes without an agent retain their stored status.
    """
    if node.last_heartbeat_at is None:
        return node.status  # no agent — trust stored value
    lh = node.last_heartbeat_at
    if lh.tzinfo is None:
        lh = lh.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - lh).total_seconds()
    if age >= SILENT_THRESHOLD_SECONDS:
        return "down"
    if age >= DEGRADED_THRESHOLD_SECONDS:
        return "degraded"
    return "healthy"


class NodeOut(BaseModel):
    id: str
    external_id: str
    name: str
    kind: str
    namespace: str | None
    cluster: str | None
    status: str
    last_heartbeat_at: datetime | None = None
    labels: dict[str, Any]
    metadata: dict[str, Any]

    class Config:
        from_attributes = True


class EdgeOut(BaseModel):
    id: str
    source_id: str
    target_id: str
    kind: str
    confidence: float
    last_seen: datetime | None
    observation_count: int
    metadata: dict[str, Any] = {}

    class Config:
        from_attributes = True


class TopologyOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class DiscoverStats(BaseModel):
    edges_found: int
    nodes_found: int
    sources: list[str]
    last_run: str


class TopologyStats(BaseModel):
    node_count: int
    edge_count: int
    auto_discovered_nodes: int
    edge_kinds: dict[str, int]


@router.get("/", response_model=TopologyOut)
async def get_topology(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    nodes_result = await db.execute(
        select(Node).where(Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    edges_result = await db.execute(
        select(Edge).where(Edge.tenant_id == tenant.id)
    )

    nodes = nodes_result.scalars().all()
    # Filter out edges hidden by the pruner
    edges = [
        e for e in edges_result.scalars().all()
        if not (e.metadata_ or {}).get("hidden")
    ]

    return TopologyOut(
        nodes=[NodeOut(
            id=n.id,
            external_id=n.external_id,
            name=n.name,
            kind=n.kind,
            namespace=n.namespace,
            cluster=n.cluster,
            status=_compute_status(n),
            last_heartbeat_at=n.last_heartbeat_at,
            labels=n.labels,
            metadata=n.metadata_,
        ) for n in nodes],
        edges=[EdgeOut(
            id=e.id,
            source_id=e.source_id,
            target_id=e.target_id,
            kind=e.kind,
            confidence=e.confidence if e.confidence is not None else 0.7,
            last_seen=e.last_seen,
            observation_count=e.observation_count if e.observation_count is not None else 1,
            metadata=e.metadata_ or {},
        ) for e in edges],
    )


@router.post("/discover", response_model=DiscoverStats)
async def trigger_discovery(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger topology discovery for this tenant."""
    from app.tasks.topology_discovery import _discover_for_tenant
    stats = await _discover_for_tenant(tenant.id, db)
    return DiscoverStats(
        edges_found=stats["edges_found"],
        nodes_found=stats["nodes_found"],
        sources=stats["sources"],
        last_run=stats["last_run"],
    )


@router.get("/stats", response_model=TopologyStats)
async def get_topology_stats(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    node_count_r = await db.execute(
        select(func.count(Node.id)).where(Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    node_count = node_count_r.scalar_one_or_none() or 0

    auto_count_r = await db.execute(
        select(func.count(Node.id)).where(
            Node.tenant_id == tenant.id,
            Node.deleted_at.is_(None),
            Node.kind == "service",
        )
    )
    auto_count = auto_count_r.scalar_one_or_none() or 0

    edges_r = await db.execute(
        select(Edge.kind, func.count(Edge.id).label("cnt"))
        .where(Edge.tenant_id == tenant.id)
        .group_by(Edge.kind)
    )
    edge_kinds = {row.kind: row.cnt for row in edges_r}
    edge_count = sum(edge_kinds.values())

    return TopologyStats(
        node_count=node_count,
        edge_count=edge_count,
        auto_discovered_nodes=auto_count,
        edge_kinds=edge_kinds,
    )


class NodeLogEntry(BaseModel):
    id: str
    ts: datetime
    source: str
    level: str
    message: str

    class Config:
        from_attributes = True


class NodeLogsOut(BaseModel):
    node_id: str
    node_name: str
    by_source: dict[str, list[NodeLogEntry]]
    has_older: bool


@router.get("/nodes/{node_id}/logs", response_model=NodeLogsOut)
async def get_node_logs(
    node_id: str,
    limit: int = Query(100, le=1000),
    before: datetime | None = Query(None, description="Fetch logs older than this ISO timestamp"),
    source: str | None = Query(None, description="Filter by source (e.g. syslog)"),
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    # Verify node belongs to tenant
    node_r = await db.execute(
        select(Node).where(Node.id == node_id, Node.tenant_id == tenant.id)
    )
    node = node_r.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # Match by node_id OR by node_name (fallback for logs where node_id FK is NULL)
    node_filter = or_(
        LogEvent.node_id == node_id,
        LogEvent.node_name == node.external_id,
    )

    q = (
        select(LogEvent)
        .where(LogEvent.tenant_id == tenant.id, node_filter)
    )
    if before:
        q = q.where(LogEvent.event_ts < before)
    if source:
        q = q.where(LogEvent.source == source)

    # Fetch limit+1 to detect if older logs exist
    logs_r = await db.execute(q.order_by(desc(LogEvent.event_ts)).limit(limit + 1))
    all_logs = logs_r.scalars().all()
    has_older = len(all_logs) > limit
    logs = all_logs[:limit]

    by_source: dict[str, list[NodeLogEntry]] = {}
    for ev in reversed(logs):  # chronological within each source
        entry = NodeLogEntry(
            id=ev.id,
            ts=ev.event_ts,
            source=ev.source,
            level=ev.level,
            message=ev.message or "",
        )
        by_source.setdefault(ev.source, []).append(entry)

    return NodeLogsOut(node_id=node_id, node_name=node.name, by_source=by_source, has_older=has_older)


class NodeConfigIn(BaseModel):
    sources: list[str] = []
    custom_log_paths: list[str] = []


@router.patch("/nodes/{node_id}/config")
async def update_node_config(
    node_id: str,
    config: NodeConfigIn,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Save agent config (sources, custom paths) into node metadata.
    The agent picks this up on the next heartbeat and restarts to apply it."""
    node_r = await db.execute(
        select(Node).where(Node.id == node_id, Node.tenant_id == tenant.id)
    )
    node = node_r.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    meta = dict(node.metadata_ or {})
    meta["agent_config"] = {
        "sources": config.sources,
        "custom_log_paths": config.custom_log_paths,
        # Convenience field the agent reads directly as a comma-separated string
        "sources_str": ",".join(s.strip() for s in config.sources if s.strip()),
    }
    node.metadata_ = meta
    await db.commit()
    return {"ok": True, "config": meta["agent_config"]}


@router.delete("/nodes/{node_id}")
async def delete_node(
    node_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a node (hide from topology, mark as deleted)."""
    node_r = await db.execute(
        select(Node).where(Node.id == node_id, Node.tenant_id == tenant.id)
    )
    node = node_r.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    node.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    return {"ok": True, "node_id": node_id, "node_name": node.name}


# ── Flow tracing ───────────────────────────────────────────────────────────────

class FlowHop(BaseModel):
    node: str
    avg_ms: float

class FlowChain(BaseModel):
    hops: list[FlowHop]
    count: int
    confidence: float
    source: str
    sources: list[str] = []

@router.get("/flows", response_model=list[FlowChain])
async def get_flows(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Reconstruct request flow chains from log signals (request_id, upstream_addr, XFF, CF-Ray)."""
    from app.tasks.flow_analysis import reconstruct_flows
    return await reconstruct_flows(tenant.id, db)


# ── Log verbosity analysis ─────────────────────────────────────────────────────

class VerbosityDimensions(BaseModel):
    has_ips: bool
    has_request_ids: bool
    has_timing: bool
    has_upstream: bool
    has_status_codes: bool
    has_cf_ray: bool
    has_error_context: bool

class VerbosityRecommendation(BaseModel):
    title: str
    priority: str
    config: str

class VerbosityReport(BaseModel):
    score: int
    log_count: int
    detected_service: str
    dimensions: VerbosityDimensions
    missing: list[str]
    recommendations: list[VerbosityRecommendation]
    analyzed_at: str

@router.get("/nodes/{node_id}/verbosity", response_model=VerbosityReport)
async def get_node_verbosity(
    node_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Score log verbosity and return actionable config recommendations per service."""
    from app.tasks.log_verbosity import analyze_node_verbosity
    report = await analyze_node_verbosity(node_id, tenant.id, db)
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
    return report
