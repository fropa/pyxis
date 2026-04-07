"""
Heartbeat endpoint — called by agents every 60 seconds.
Creates the node on first contact, then updates last_seen in Redis.
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
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
    else:
        meta = dict(node.metadata_ or {})
        changed = False
        if payload.ip_address and meta.get("ip_address") != payload.ip_address:
            meta["ip_address"] = payload.ip_address
            node.metadata_ = meta
            changed = True
        if node.status == "down":
            node.status = "healthy"
            changed = True
        if changed:
            await db.commit()

    await record_heartbeat(tenant.id, node.id)
    return {"ok": True, "node_id": node.id}
