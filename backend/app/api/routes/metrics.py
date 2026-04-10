"""
System health metrics — reported by agents every 60s.

Each report contains raw /proc metrics + pre-computed health score (0-100).
The backend stores the latest snapshot in node.metadata_["health"] and
keeps a 24-hour rolling history in Redis for sparklines.

Health → node status mapping:
  80-100  healthy
  50-79   degraded
  20-49   critical   (new status: server is alive but severely overloaded)
  0-19    critical
"""
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.core.redis import get_redis, publish_event
from app.models.tenant import Tenant
from app.models.topology import Node

router = APIRouter()
log = logging.getLogger(__name__)

# Redis key prefix for 24h history (list of {ts, score} JSON objects)
_HISTORY_PREFIX = "metrics:history"
_HISTORY_TTL    = 86400   # 24 hours
_HISTORY_MAX    = 1440    # one point per minute × 24h


class DiskMount(BaseModel):
    mount: str
    device: str = ""
    used_pct: float
    free_gb: float
    inode_used_pct: float = 0.0


class MetricsReport(BaseModel):
    node_name: str
    metrics: dict
    health_score: int
    health_components: dict[str, int]


def _score_to_status(score: int) -> str:
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "degraded"
    return "critical"


@router.post("/report")
async def report_metrics(
    body: MetricsReport,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Node).where(
            Node.tenant_id == tenant.id,
            Node.external_id == body.node_name,
            Node.deleted_at.is_(None),
        )
    )
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not registered — send a heartbeat first")

    score = max(0, min(100, body.health_score))
    new_status = _score_to_status(score)

    # Merge health snapshot into node metadata
    meta = dict(node.metadata_ or {})
    prev_score = (meta.get("health") or {}).get("score", 100)
    meta["health"] = {
        "score":      score,
        "components": body.health_components,
        "metrics":    _sanitise_metrics(body.metrics),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    node.metadata_ = meta

    # Only update DB status if the node is currently alive (not heartbeat-timeout down)
    # Never override "down" set by the heartbeat checker — that means the node is offline
    if node.status != "down":
        node.status = new_status

    await db.commit()

    # Push to Redis history (list of JSON strings, capped at HISTORY_MAX)
    r = await get_redis()
    hkey = f"{_HISTORY_PREFIX}:{tenant.id}:{node.id}"
    point = json.dumps({"ts": int(time.time()), "score": score})
    await r.lpush(hkey, point)
    await r.ltrim(hkey, 0, _HISTORY_MAX - 1)
    await r.expire(hkey, _HISTORY_TTL)

    # Publish WS event when score crosses a threshold
    if prev_score >= 80 > score or prev_score >= 50 > score:
        await publish_event(tenant.id, {
            "type": "node_health_degraded",
            "node_id": node.id,
            "node_name": node.name,
            "score": score,
            "status": new_status,
            "components": body.health_components,
        })
        log.warning("Node %s health score dropped: %d → %d (%s)", node.name, prev_score, score, new_status)

    return {"ok": True, "score": score, "status": new_status}


@router.get("/history/{node_id}")
async def get_history(
    node_id: str,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Return last 24h of health score history as [{ts, score}]."""
    r = await get_redis()
    hkey = f"{_HISTORY_PREFIX}:{tenant.id}:{node_id}"
    raw = await r.lrange(hkey, 0, -1)
    points = []
    for item in reversed(raw):   # stored newest-first, return oldest-first
        try:
            points.append(json.loads(item))
        except Exception:
            pass
    return points


def _sanitise_metrics(m: dict) -> dict:
    """Keep only the fields we want to store (drop internal _prefixed keys)."""
    keep = {
        "cpu_count", "cpu_used_pct", "load_avg_1m", "load_avg_5m", "load_avg_15m",
        "iowait_pct",
        "mem_total_mb", "mem_available_mb", "mem_used_mb", "mem_used_pct",
        "swap_total_mb", "swap_used_mb", "swap_used_pct",
        "disk_mounts",
        "fd_open", "fd_max", "fd_used_pct",
        "tcp_established", "tcp_time_wait", "tcp_somaxconn",
        "process_count", "process_max", "process_used_pct",
        "uptime_seconds",
    }
    return {k: v for k, v in m.items() if k in keep}
