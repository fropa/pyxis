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

SILENT_THRESHOLD_SECONDS = 180   # 3 minutes without heartbeat = node silent
HEARTBEAT_KEY_PREFIX = "heartbeat"


async def record_heartbeat(tenant_id: str, node_id: str) -> None:
    r = await get_redis()
    key = f"{HEARTBEAT_KEY_PREFIX}:{tenant_id}:{node_id}"
    await r.setex(key, SILENT_THRESHOLD_SECONDS * 2, str(time.time()))


async def check_silent_nodes() -> None:
    """
    Scan all nodes and fire incidents for those that have gone silent.
    Called by ARQ every 2 minutes.
    """
    r = await get_redis()
    async with AsyncSessionLocal() as db:
        # Get all active nodes across all tenants
        result = await db.execute(
            select(Node).where(Node.deleted_at.is_(None))
        )
        nodes = result.scalars().all()

        now = time.time()
        for node in nodes:
            key = f"{HEARTBEAT_KEY_PREFIX}:{node.tenant_id}:{node.id}"
            last_seen_raw = await r.get(key)

            if last_seen_raw is None:
                # Node never sent a heartbeat — could be new or never configured
                # Only flag nodes that have been seen before (last_seen is set)
                age = (datetime.now(timezone.utc) - node.last_seen).total_seconds()
                if age < SILENT_THRESHOLD_SECONDS * 2:
                    continue  # recently added, give it time
                # Fall through to check if it's been too long

            elif now - float(last_seen_raw) < SILENT_THRESHOLD_SECONDS:
                continue  # still alive

            # Node is silent — check if we already have an open incident for it
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
                continue  # already open

            # Open a silent node incident
            incident = Incident(
                id=str(uuid.uuid4()),
                tenant_id=node.tenant_id,
                title=f"[node_silent] {node.name} ({node.kind}) has stopped sending logs",
                severity="high",
                status="open",
                started_at=datetime.now(timezone.utc),
                rca_summary=(
                    f"Node '{node.name}' has not sent any logs or heartbeat for "
                    f"over {SILENT_THRESHOLD_SECONDS // 60} minutes. "
                    "Possible causes: host crashed, OOM, network partition, agent stopped."
                ),
            )
            db.add(incident)
            await db.flush()
            db.add(IncidentNode(incident_id=incident.id, node_id=node.id, role="root_cause"))
            await db.commit()

            # Update node status
            node.status = "down"
            await db.commit()

            await publish_event(node.tenant_id, {
                "type": "incident_opened",
                "incident_id": incident.id,
                "title": incident.title,
                "severity": incident.severity,
                "node_id": node.id,
                "node_name": node.name,
            })

            log.warning("Opened node_silent incident for node %s (%s)", node.name, node.id)
