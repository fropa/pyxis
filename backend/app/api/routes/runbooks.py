from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.tenant import Tenant
from app.models.runbook import Runbook

router = APIRouter()


class RunbookOut(BaseModel):
    id: str
    incident_id: str
    title: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=list[RunbookOut])
async def list_runbooks(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Runbook)
        .where(Runbook.tenant_id == tenant.id)
        .order_by(desc(Runbook.created_at))
        .limit(50)
    )
    return result.scalars().all()


@router.get("/incident/{incident_id}", response_model=RunbookOut | None)
async def get_runbook_for_incident(
    incident_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Runbook).where(
            Runbook.tenant_id == tenant.id,
            Runbook.incident_id == incident_id,
        )
    )
    return result.scalar_one_or_none()


@router.delete("/{runbook_id}", status_code=204)
async def delete_runbook(
    runbook_id: str,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Runbook).where(Runbook.id == runbook_id, Runbook.tenant_id == tenant.id)
    )
    rb = result.scalar_one_or_none()
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
    await db.delete(rb)
    await db.commit()
