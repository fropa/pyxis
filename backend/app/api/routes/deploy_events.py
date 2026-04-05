import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.deploy_event import DeployEvent
from app.models.tenant import Tenant

router = APIRouter()


class DeployEventCreate(BaseModel):
    service: str
    version: str | None = None
    deployed_by: str | None = None
    environment: str = "production"
    deployed_at: datetime | None = None
    meta: dict[str, Any] = {}


class DeployEventOut(BaseModel):
    id: str
    service: str
    version: str | None
    deployed_by: str | None
    environment: str
    deployed_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("/", response_model=DeployEventOut, status_code=201)
async def create_deploy_event(
    payload: DeployEventCreate,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    event = DeployEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        service=payload.service,
        version=payload.version,
        deployed_by=payload.deployed_by,
        environment=payload.environment,
        deployed_at=payload.deployed_at or datetime.utcnow(),
        meta=payload.meta,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/", response_model=list[DeployEventOut])
async def list_deploy_events(
    limit: int = 50,
    environment: str | None = None,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(DeployEvent)
        .where(DeployEvent.tenant_id == tenant.id)
        .order_by(desc(DeployEvent.deployed_at))
        .limit(limit)
    )
    if environment:
        q = q.where(DeployEvent.environment == environment)
    result = await db.execute(q)
    return result.scalars().all()
