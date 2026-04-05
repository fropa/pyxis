"""
Heartbeat endpoint — called by agents every 60 seconds.
Updates node last_seen in Redis for silent-death detection.
"""
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

    if node:
        await record_heartbeat(tenant.id, node.id)
        # Mark node healthy if it was previously down
        if node.status == "down":
            node.status = "healthy"
            await db.commit()

    return {"ok": True}
