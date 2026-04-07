"""
Kubernetes cluster state endpoint.

Agent pushes a full snapshot every 30 s (nodes/pods/deployments/namespaces).
Backend stores it in Redis with a 5-min TTL — stale data expires automatically
when the agent stops or the cluster becomes unreachable.
Frontend polls GET /state to render the cluster browser.
"""
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import get_current_tenant
from app.core.redis import get_redis
from app.models.tenant import Tenant

router = APIRouter()

_TTL = 300  # 5 minutes


class K8sState(BaseModel):
    nodes: list[dict[str, Any]] = []
    pods: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    namespaces: list[dict[str, Any]] = []
    updated_at: str = ""


@router.post("/state")
async def push_state(
    state: K8sState,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Agent pushes a cluster snapshot."""
    r = await get_redis()
    state.updated_at = datetime.now(timezone.utc).isoformat()
    await r.setex(f"k8s:state:{tenant.id}", _TTL, state.model_dump_json())
    return {"ok": True}


@router.get("/state", response_model=K8sState)
async def get_state(
    tenant: Tenant = Depends(get_current_tenant),
):
    """Frontend fetches the latest cluster state."""
    r = await get_redis()
    raw = await r.get(f"k8s:state:{tenant.id}")
    if not raw:
        return K8sState()
    return K8sState.model_validate_json(raw)
