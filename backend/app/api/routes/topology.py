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


class NodeOut(BaseModel):
    id: str
    external_id: str
    name: str
    kind: str
    namespace: str | None
    cluster: str | None
    status: str
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
            status=n.status,
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
