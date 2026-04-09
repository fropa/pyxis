"""
Heartbeat checker — runs as an ARQ periodic task.
Fires a node_silent incident when a node stops sending heartbeats.
"""
import logging
import time
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis, publish_event
from app.models.incident import Incident, IncidentNode
from app.models.topology import Node
from sqlalchemy import select

log = logging.getLogger(__name__)
settings = get_settings()

SILENT_THRESHOLD_SECONDS = 180   # 3 min without heartbeat → down
DEGRADED_THRESHOLD_SECONDS = 90  # 90 s without heartbeat → degraded (missed 1-2 beats)
HEARTBEAT_KEY_PREFIX = "heartbeat"


async def record_heartbeat(tenant_id: str, node_id: str) -> None:
    """Cache the heartbeat timestamp in Redis for fast liveness checks."""
    r = await get_redis()
    key = f"{HEARTBEAT_KEY_PREFIX}:{tenant_id}:{node_id}"
    await r.setex(key, SILENT_THRESHOLD_SECONDS * 3, str(time.time()))


def _effective_age_seconds(node: Node) -> float | None:
    """
    Return seconds since the last heartbeat.
    Uses Redis-cached value if available (fast path), falls back to DB column.
    Returns None when this node has never sent a heartbeat (no agent).
    """
    if node.last_heartbeat_at is None:
        return None  # auto-discovered node — no agent, no heartbeat expected
    lh = node.last_heartbeat_at
    if lh.tzinfo is None:
        lh = lh.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - lh).total_seconds()


async def check_silent_nodes() -> None:
    """
    Scan all agent-monitored nodes and:
    - mark degraded after DEGRADED_THRESHOLD_SECONDS
    - mark down + open incident after SILENT_THRESHOLD_SECONDS
    Called by ARQ every 2 minutes.
    """
    r = await get_redis()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Node).where(
                Node.deleted_at.is_(None),
                Node.last_heartbeat_at.isnot(None),  # only agent-managed nodes
            )
        )
        nodes = result.scalars().all()

        for node in nodes:
            # Fast path: check Redis cache first
            key = f"{HEARTBEAT_KEY_PREFIX}:{node.tenant_id}:{node.id}"
            cached = await r.get(key)
            if cached is not None:
                age = time.time() - float(cached)
            else:
                # Redis cache miss (Redis restart?) — fall back to DB timestamp
                age = _effective_age_seconds(node)
                if age is None:
                    continue

            if age < DEGRADED_THRESHOLD_SECONDS:
                # Fully alive — if it was degraded/down, restore it
                if node.status in ("degraded", "down"):
                    node.status = "healthy"
                    await db.commit()
                continue

            if age < SILENT_THRESHOLD_SECONDS:
                # Missed 1-2 beats — mark degraded
                if node.status not in ("degraded", "down"):
                    node.status = "degraded"
                    await db.commit()
                    await publish_event(node.tenant_id, {
                        "type": "node_degraded",
                        "node_id": node.id,
                        "node_name": node.name,
                        "age_seconds": int(age),
                    })
                continue

            # === Silent: no heartbeat for >= SILENT_THRESHOLD_SECONDS ===

            # Idempotent: already marked down with open incident — skip
            if node.status == "down":
                continue

            node.status = "down"
            await db.commit()

            # Check if we already have an open node_silent incident
            existing = await db.execute(
                select(Incident)
                .join(IncidentNode, IncidentNode.incident_id == Incident.id)
                .where(
                    Incident.tenant_id == node.tenant_id,
                    Incident.status == "open",
                    Incident.title.like(f"%node_silent%{node.name}%"),
                    IncidentNode.node_id == node.id,
                )
                .limit(1)
            )
            if existing.scalar_one_or_none():
                continue  # incident already open

            minutes_silent = int(age // 60)
            incident = Incident(
                id=str(uuid.uuid4()),
                tenant_id=node.tenant_id,
                title=f"[node_silent] {node.name} ({node.kind}) has stopped sending heartbeats",
                severity="high",
                status="open",
                started_at=datetime.now(timezone.utc),
                rca_summary=(
                    f"Node '{node.name}' has not sent a heartbeat for {minutes_silent} minutes. "
                    "Possible causes: host crashed, OOM kill, network partition, agent process stopped. "
                    f"Last heartbeat: {node.last_heartbeat_at.isoformat() if node.last_heartbeat_at else 'never'}."
                ),
            )
            db.add(incident)
            await db.flush()
            db.add(IncidentNode(incident_id=incident.id, node_id=node.id, role="root_cause"))
            await db.commit()

            await publish_event(node.tenant_id, {
                "type": "incident_opened",
                "incident_id": incident.id,
                "title": incident.title,
                "severity": incident.severity,
                "node_id": node.id,
                "node_name": node.name,
            })

            log.warning(
                "Node %s (%s) is silent for %d min — opened incident %s",
                node.name, node.id, minutes_silent, incident.id,
            )
