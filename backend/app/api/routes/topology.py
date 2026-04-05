"""
Topology graph read endpoints — consumed by the frontend React Flow canvas.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.models.tenant import Tenant
from app.models.topology import Node, Edge

router = APIRouter()


class NodeOut(BaseModel):
    id: str
    external_id: str
    name: str
    kind: str
    namespace: str | None
    cluster: str | None
    status: str
    labels: dict[str, Any]
    metadata: dict[str, Any]

    class Config:
        from_attributes = True


class EdgeOut(BaseModel):
    id: str
    source_id: str
    target_id: str
    kind: str

    class Config:
        from_attributes = True


class TopologyOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


@router.get("/", response_model=TopologyOut)
async def get_topology(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    nodes_result = await db.execute(
        select(Node).where(Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    edges_result = await db.execute(
        select(Edge).where(Edge.tenant_id == tenant.id)
    )

    nodes = nodes_result.scalars().all()
    edges = edges_result.scalars().all()

    return TopologyOut(
        nodes=[NodeOut(
            id=n.id,
            external_id=n.external_id,
            name=n.name,
            kind=n.kind,
            namespace=n.namespace,
            cluster=n.cluster,
            status=n.status,
            labels=n.labels,
            metadata=n.metadata_,
        ) for n in nodes],
        edges=[EdgeOut(
            id=e.id,
            source_id=e.source_id,
            target_id=e.target_id,
            kind=e.kind,
        ) for e in edges],
    )
