"""
Alert storm detection.

If N+ incidents with similar fingerprints open within STORM_WINDOW_SECONDS,
they are grouped under a single parent "storm" incident. Child incidents are
marked with parent_incident_id so the UI can collapse them.

This prevents alert fatigue when one root cause fans out into dozens of
identical incidents (e.g. a database goes down → every service dependent
on it opens a separate incident).
"""
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.core.redis import publish_event

STORM_THRESHOLD = 5          # N incidents with same fingerprint prefix
STORM_WINDOW_SECONDS = 300   # within 5 minutes
STORM_KEY = "storm:{tenant}:{prefix}"


def _fingerprint_prefix(fingerprint: str) -> str:
    """Use first 2 segments of fingerprint as the grouping key."""
    parts = fingerprint.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else fingerprint[:32]


async def check_and_group_storm(
    incident: Incident,
    tenant_id: str,
    redis: Redis,
    db: AsyncSession,
) -> bool:
    """
    Returns True if this incident was attached to an existing storm.
    Caller should still save the incident but skip further RCA if True.
    """
    if not incident.rca_full and not incident.title:
        return False

    prefix = _fingerprint_prefix(
        # try to get fingerprint from related event, fall back to title words
        f"title:{incident.title[:40]}"
    )
    storm_key = STORM_KEY.format(tenant=tenant_id, prefix=prefix)

    # Increment the storm counter
    count = await redis.incr(storm_key)
    await redis.expire(storm_key, STORM_WINDOW_SECONDS)

    if count < STORM_THRESHOLD:
        return False

    # Find or create storm parent
    parent_key = f"storm_parent:{tenant_id}:{prefix}"
    parent_id = await redis.get(parent_key)

    if parent_id:
        parent_id = parent_id.decode() if isinstance(parent_id, bytes) else parent_id
        # Attach this incident to the existing storm
        incident.parent_incident_id = parent_id
        await db.commit()

        # Update storm_size on parent
        parent_result = await db.execute(
            select(Incident).where(Incident.id == parent_id)
        )
        parent = parent_result.scalar_one_or_none()
        if parent:
            parent.storm_size = int(count)
            await db.commit()
            await publish_event(tenant_id, {
                "type": "storm_updated",
                "parent_incident_id": parent_id,
                "storm_size": int(count),
                "child_incident_id": incident.id,
            })
        return True

    # This incident becomes the storm parent
    await redis.setex(parent_key, STORM_WINDOW_SECONDS * 4, incident.id)
    incident.storm_size = int(count)

    await publish_event(tenant_id, {
        "type": "storm_detected",
        "parent_incident_id": incident.id,
        "storm_size": int(count),
        "title": incident.title,
    })
    return False  # parent still gets RCA
