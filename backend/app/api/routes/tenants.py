"""
Tenant management — used internally / by admin UI.
In production this should be behind admin auth, not just API key.
"""
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.incident import Incident

router = APIRouter()


class TenantCreate(BaseModel):
    name: str
    contact_email: str | None = None
    plan: str = "starter"


class TenantOut(BaseModel):
    id: str
    name: str
    api_key: str
    plan: str
    contact_email: str | None

    class Config:
        from_attributes = True


@router.post("/", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(payload: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=payload.name,
        api_key=secrets.token_urlsafe(32),
        contact_email=payload.contact_email,
        plan=payload.plan,
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.get("/", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tenant).where(Tenant.is_active == True))
    return result.scalars().all()


class TenantStats(BaseModel):
    id: str
    name: str
    plan: str
    total_incidents: int
    open_incidents: int
    resolved_last_7d: int
    health_score: float  # 0-100


@router.get("/stats", response_model=list[TenantStats])
async def tenant_stats(db: AsyncSession = Depends(get_db)):
    tenants_result = await db.execute(select(Tenant).where(Tenant.is_active == True))
    tenants = tenants_result.scalars().all()

    since_7d = datetime.utcnow() - timedelta(days=7)
    stats = []
    for t in tenants:
        total = (await db.execute(
            select(func.count(Incident.id)).where(Incident.tenant_id == t.id)
        )).scalar() or 0

        open_count = (await db.execute(
            select(func.count(Incident.id)).where(
                Incident.tenant_id == t.id, Incident.status == "open"
            )
        )).scalar() or 0

        resolved_7d = (await db.execute(
            select(func.count(Incident.id)).where(
                Incident.tenant_id == t.id,
                Incident.status == "resolved",
                Incident.resolved_at >= since_7d,
            )
        )).scalar() or 0

        # Health score: starts at 100, -10 per open incident, min 0
        health = max(0.0, 100.0 - (open_count * 10))

        stats.append(TenantStats(
            id=t.id,
            name=t.name,
            plan=t.plan,
            total_incidents=total,
            open_incidents=open_count,
            resolved_last_7d=resolved_7d,
            health_score=health,
        ))

    return stats
