"""
Auto-resolve task — closes incidents that have been quiet for AUTO_RESOLVE_MINUTES.
An incident is "quiet" when no new anomaly events have been attached to it
within the resolve window.

Called by ARQ every 5 minutes.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.redis import publish_event, get_redis
from app.ai.detector import clear_open_incident
from app.models.incident import Incident
from app.models.event import LogEvent

log = logging.getLogger(__name__)

AUTO_RESOLVE_MINUTES = 30


async def auto_resolve_incidents() -> None:
    """Close open incidents with no new events for AUTO_RESOLVE_MINUTES."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=AUTO_RESOLVE_MINUTES)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Incident).where(
                Incident.status == "open",
                Incident.started_at <= cutoff,
                # node_silent incidents close when the node comes back, not by timeout
                ~Incident.title.like("%node_silent%"),
            )
        )
        candidates = result.scalars().all()

        r = await get_redis()

        for incident in candidates:
            # Check if any new events attached after the cutoff
            latest_event = await db.execute(
                select(LogEvent)
                .where(
                    LogEvent.incident_id == incident.id,
                    LogEvent.event_ts > cutoff,
                )
                .limit(1)
            )
            if latest_event.scalar_one_or_none():
                continue  # still active

            # Auto-resolve
            incident.status = "resolved"
            incident.resolved_at = datetime.now(timezone.utc)
            if not incident.rca_summary:
                incident.rca_summary = f"Auto-resolved: no new events for {AUTO_RESOLVE_MINUTES} minutes."

            await db.commit()

            # Clear the dedup key so new occurrences open fresh incidents
            if incident.title:
                # Extract fingerprint hint from title if present
                pass
            # Clear by incident_id scan (best-effort)
            pattern = f"open_incident:{incident.tenant_id}:*"
            async for key in r.scan_iter(pattern):
                val = await r.get(key)
                if val == incident.id:
                    await r.delete(key)

            await publish_event(incident.tenant_id, {
                "type": "incident_resolved",
                "incident_id": incident.id,
                "title": incident.title,
                "auto": True,
            })

            log.info("Auto-resolved incident %s (%s)", incident.id, incident.title[:60])
