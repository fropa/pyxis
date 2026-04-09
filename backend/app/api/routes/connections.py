"""
Network connection reporter.

Agents run 'ss -tnp' every 30 s and POST established TCP connections here.
We match remote IPs against known nodes and upsert topology edges with
kind="network" so real connections appear in the graph automatically.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.tenant import Tenant
from app.models.topology import Node, Edge

router = APIRouter()


class ConnectionEntry(BaseModel):
    remote_ip: str
    remote_port: int
    local_port: int
    process: str = ""


class ConnectionReport(BaseModel):
    node_name: str
    connections: list[ConnectionEntry]


@router.post("/report")
async def report_connections(
    report: ConnectionReport,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    # Find the reporting node
    src_r = await db.execute(
        select(Node).where(
            Node.tenant_id == tenant.id,
            Node.external_id == report.node_name,
            Node.deleted_at.is_(None),
        )
    )
    source = src_r.scalar_one_or_none()
    if source is None:
        return {"ok": False, "reason": "source node not found"}

    # Build IP → node map for this tenant
    all_r = await db.execute(
        select(Node).where(Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    ip_to_node: dict[str, Node] = {}
    for node in all_r.scalars().all():
        ip = (node.metadata_ or {}).get("ip_address")
        if ip:
            ip_to_node[ip] = node

    now = datetime.now(timezone.utc)
    edges_created = 0
    edges_updated = 0

    for conn in report.connections:
        target = ip_to_node.get(conn.remote_ip)
        if target is None or target.id == source.id:
            continue  # unknown host or self-loop

        # Look for existing network edge in either direction (treat as undirected for display)
        edge_r = await db.execute(
            select(Edge).where(
                Edge.tenant_id == tenant.id,
                Edge.source_id == source.id,
                Edge.target_id == target.id,
                Edge.kind == "network",
            )
        )
        edge = edge_r.scalar_one_or_none()

        # Build process tag e.g. "nginx:443"
        proc_tag = f"{conn.process}:{conn.remote_port}" if conn.process else f":{conn.remote_port}"

        if edge:
            edge.observation_count = (edge.observation_count or 0) + 1
            edge.last_seen = now
            edge.confidence = min(0.99, (edge.confidence or 0.7) + 0.01)
            # Track which processes/ports are seen on this connection
            meta = dict(edge.metadata_ or {})
            procs: list[str] = meta.get("processes", [])
            if proc_tag not in procs:
                procs.insert(0, proc_tag)
            meta["processes"] = procs[:8]  # keep top 8
            edge.metadata_ = meta
            edges_updated += 1
        else:
            edge = Edge(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                source_id=source.id,
                target_id=target.id,
                kind="network",
                confidence=0.95,  # high — directly observed TCP connection
                last_seen=now,
                observation_count=1,
                metadata_={"processes": [proc_tag]},
            )
            db.add(edge)
            edges_created += 1

    await db.commit()
    return {
        "ok": True,
        "edges_created": edges_created,
        "edges_updated": edges_updated,
        "connections_checked": len(report.connections),
    }
