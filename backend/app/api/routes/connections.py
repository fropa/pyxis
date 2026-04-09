"""
Network connection reporter.

Agents collect established TCP connections from multiple OS sources and POST them here.
We match remote IPs against known nodes and upsert topology edges with kind="network".

Detection sources (and their confidence):
  ss_established     0.95 — active TCP ESTAB right now
  proc_net_estab     0.90 — /proc/net/tcp ESTABLISHED state
  proc_net_timewait  0.75 — /proc/net/tcp TIME_WAIT (closed in last ~2min)
  log_pattern        0.70 — remote IP found in a log message
  arp                0.60 — IP in ARP cache (recently communicated on LAN)
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

# Confidence per detection source
_SOURCE_CONFIDENCE: dict[str, float] = {
    "ss_established":    0.95,
    "proc_net_estab":    0.90,
    "proc_net_timewait": 0.75,
    "log_pattern":       0.70,
    "arp":               0.60,
}
_DEFAULT_CONFIDENCE = 0.80


class ConnectionEntry(BaseModel):
    remote_ip: str
    remote_port: int
    local_port: int
    process: str = ""
    source: str = "ss_established"  # detection source tag


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

        base_confidence = _SOURCE_CONFIDENCE.get(conn.source, _DEFAULT_CONFIDENCE)

        # Look for existing network edge in this direction
        edge_r = await db.execute(
            select(Edge).where(
                Edge.tenant_id == tenant.id,
                Edge.source_id == source.id,
                Edge.target_id == target.id,
                Edge.kind == "network",
            )
        )
        edge = edge_r.scalar_one_or_none()

        # Build process tag e.g. "nginx:443" or just ":443" for arp (no process)
        if conn.process:
            proc_tag = f"{conn.process}:{conn.remote_port}"
        elif conn.remote_port:
            proc_tag = f":{conn.remote_port}"
        else:
            proc_tag = conn.source  # use source name as label for arp/log entries

        if edge:
            edge.observation_count = (edge.observation_count or 0) + 1
            edge.last_seen = now
            # Nudge confidence up toward base (weighted toward best observed source)
            edge.confidence = min(0.99, max(edge.confidence or base_confidence,
                                            (edge.confidence or base_confidence) * 0.9 + base_confidence * 0.1))
            # Track detection sources and processes
            meta = dict(edge.metadata_ or {})
            procs: list[str] = meta.get("processes", [])
            if proc_tag not in procs:
                procs.insert(0, proc_tag)
            meta["processes"] = procs[:8]  # keep top 8
            # Track which sources have observed this edge
            sources: list[str] = meta.get("sources", [])
            if conn.source not in sources:
                sources.append(conn.source)
            meta["sources"] = sources
            edge.metadata_ = meta
            edges_updated += 1
        else:
            edge = Edge(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                source_id=source.id,
                target_id=target.id,
                kind="network",
                confidence=base_confidence,
                last_seen=now,
                observation_count=1,
                metadata_={
                    "processes": [proc_tag],
                    "sources": [conn.source],
                },
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
