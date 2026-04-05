"""CRUD for notification channels."""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.notification import NotificationChannel
from app.models.tenant import Tenant

router = APIRouter()


class ChannelCreate(BaseModel):
    name: str
    kind: str                          # slack | webhook | email
    config: dict[str, Any]
    min_severity: str = "medium"
    event_types: list[str] = ["incident_opened", "rca_ready"]


class ChannelOut(BaseModel):
    id: str
    name: str
    kind: str
    min_severity: str
    event_types: list[str]
    is_active: bool

    class Config:
        from_attributes = True


@router.get("/", response_model=list[ChannelOut])
async def list_channels(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.tenant_id == tenant.id)
    )
    return result.scalars().all()


@router.post("/", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    channel = NotificationChannel(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        name=payload.name,
        kind=payload.kind,
        config=payload.config,
        min_severity=payload.min_severity,
        event_types=payload.event_types,
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.tenant_id == tenant.id,
        )
    )
    ch = result.scalar_one_or_none()
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(ch)
    await db.commit()
