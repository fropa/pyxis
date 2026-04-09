"""
Heartbeat endpoint — called by agents every 60 seconds.
Creates the node on first contact, then updates last_heartbeat_at in DB and Redis.
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
from app.models.topology import Node
from app.tasks.heartbeat import record_heartbeat

router = APIRouter()


class HeartbeatPayload(BaseModel):
    node_name: str
    node_kind: str = "linux_host"
    ip_address: str | None = None


@router.post("/")
async def heartbeat(
    payload: HeartbeatPayload,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Node).where(
            Node.tenant_id == tenant.id,
            Node.external_id == payload.node_name,
        )
    )
    node = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if node is None:
        # First contact — register the node automatically
        meta = {}
        if payload.ip_address:
            meta["ip_address"] = payload.ip_address
        node = Node(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            external_id=payload.node_name,
            name=payload.node_name,
            kind=payload.node_kind,
            status="healthy",
            labels={},
            metadata_=meta,
            last_heartbeat_at=now,
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
    else:
        meta = dict(node.metadata_ or {})
        # Restore if previously deleted or down — agent coming back online
        if node.deleted_at is not None:
            node.deleted_at = None
            node.status = "healthy"
        if payload.ip_address and meta.get("ip_address") != payload.ip_address:
            meta["ip_address"] = payload.ip_address
            node.metadata_ = meta
        if node.status in ("down", "degraded"):
            node.status = "healthy"
        # Always stamp last_heartbeat_at — this is the liveness source of truth
        node.last_heartbeat_at = now
        await db.commit()

    await record_heartbeat(tenant.id, node.id)
    agent_config = (node.metadata_ or {}).get("agent_config", {})

    # Send all known node IPs so the agent can scan logs for them
    all_nodes_r = await db.execute(
        select(Node).where(Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    known_ips: dict[str, str] = {}  # ip → node_name
    for n in all_nodes_r.scalars().all():
        ip = (n.metadata_ or {}).get("ip_address")
        if ip and n.id != node.id:  # exclude self
            known_ips[ip] = n.external_id

    return {"ok": True, "node_id": node.id, "config": agent_config, "known_ips": known_ips}
