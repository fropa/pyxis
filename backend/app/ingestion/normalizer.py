"""
Normalizer: turns a RawEvent (from API) into a persisted LogEvent + upserts Node.
Also detects topology changes (K8s node added/removed, pod created/deleted).
"""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.parser import parse
from app.ingestion.fingerprinter import fingerprint
from app.models.event import LogEvent
from app.models.topology import Node
from app.core.redis import publish_event

if TYPE_CHECKING:
    from app.api.routes.ingest import RawEvent


# K8s reasons that mean a node/pod appeared or disappeared
_TOPOLOGY_ADDED = {"NodeReady", "Starting", "Scheduled", "Pulled", "Created", "Started"}
_TOPOLOGY_REMOVED = {"NodeNotReady", "Killing", "Evicted", "Preempted", "OOMKilling", "Failed"}


async def normalize_event(raw: "RawEvent", tenant_id: str, db: AsyncSession) -> LogEvent:
    ts = raw.timestamp or datetime.now(timezone.utc)
    parsed = parse(raw.source, raw.raw, raw.parsed)

    # Upsert node
    node = await _upsert_node(raw, tenant_id, db)

    message = parsed.get("message", raw.raw)
    fp = fingerprint(raw.source, message, parsed)

    log_event = LogEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        node_id=node.id if node else None,
        node_name=raw.node_name or None,
        event_ts=ts,
        source=raw.source,
        level=raw.level,
        raw=raw.raw,
        message=message,
        parsed=parsed,
        fingerprint=fp,
        # Flow-signal fields extracted by the parser
        request_id=parsed.get("request_id") or parsed.get("trace_id") or None,
        trace_id=parsed.get("trace_id") or parsed.get("cf_ray") or None,
        client_ip=parsed.get("client_ip") or None,
        upstream_addr=parsed.get("upstream_addr") or None,
        response_time_ms=parsed.get("response_time_ms") or parsed.get("request_time_ms") or parsed.get("duration_ms") or None,
    )
    db.add(log_event)
    await db.commit()
    await db.refresh(log_event)

    # Publish topology-change events for real-time frontend updates
    if raw.source == "k8s_event" and node:
        reason = raw.parsed.get("reason", "")
        if reason in _TOPOLOGY_ADDED:
            await publish_event(tenant_id, {
                "type": "topology_change",
                "action": "node_added",
                "node_id": node.id,
                "node_name": node.name,
                "node_kind": node.kind,
            })
        elif reason in _TOPOLOGY_REMOVED:
            await publish_event(tenant_id, {
                "type": "topology_change",
                "action": "node_removed",
                "node_id": node.id,
                "node_name": node.name,
                "node_kind": node.kind,
            })

    return log_event


async def _upsert_node(raw: "RawEvent", tenant_id: str, db: AsyncSession) -> Node | None:
    if not raw.node_name:
        return None

    external_id = raw.node_name
    kind = raw.node_kind or _infer_kind(raw.source)

    result = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.external_id == external_id)
    )
    node = result.scalar_one_or_none()

    if node:
        node.last_seen = datetime.now(timezone.utc)
        node.labels = raw.labels or node.labels
        # Restore if soft-deleted — agent is sending logs again
        if node.deleted_at is not None:
            node.deleted_at = None
            node.status = "healthy"
    else:
        node = Node(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            external_id=external_id,
            name=raw.node_name,
            kind=kind,
            labels=raw.labels or {},
        )
        db.add(node)

    await db.commit()
    await db.refresh(node)
    return node


def _infer_kind(source: str) -> str:
    return {
        "syslog": "linux_host",
        "k8s_event": "k8s_node",
        "ci_pipeline": "ci_runner",
    }.get(source, "unknown")
