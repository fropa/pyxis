"""
Knowledge base management — connect repos, trigger indexing, list sources.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.knowledge import KnowledgeSource
from app.models.tenant import Tenant
from app.knowledge.indexer import index_repository

router = APIRouter()


class SourceCreate(BaseModel):
    repo_url: str
    repo_type: str = "github"   # github | gitlab | gitea
    access_token: str | None = None


class SourceOut(BaseModel):
    id: str
    repo_url: str
    repo_type: str
    index_status: str
    last_indexed_at: str | None
    error_message: str | None

    class Config:
        from_attributes = True


@router.post("/sources", response_model=SourceOut, status_code=201)
async def add_source(
    payload: SourceCreate,
    background: BackgroundTasks,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    source = KnowledgeSource(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        repo_url=payload.repo_url,
        repo_type=payload.repo_type,
        access_token=payload.access_token,  # TODO: encrypt at rest
        index_status="pending",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # Kick off indexing in background
    background.add_task(index_repository, source.id, tenant.id)

    return source


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.tenant_id == tenant.id)
    )
    return result.scalars().all()


@router.post("/sources/{source_id}/reindex", status_code=202)
async def reindex_source(
    source_id: str,
    background: BackgroundTasks,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id,
            KnowledgeSource.tenant_id == tenant.id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    source.index_status = "pending"
    await db.commit()

    background.add_task(index_repository, source.id, tenant.id)
    return {"message": "Reindex queued"}
