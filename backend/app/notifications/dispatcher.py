"""
Notification dispatcher.
Queries the tenant's configured channels and sends to each one.
Failures in one channel never block others.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.incident import Incident
from app.models.notification import NotificationChannel
from app.notifications.slack import send_slack
from app.notifications.webhook import send_webhook

log = logging.getLogger(__name__)

SEVERITY_ORDER = ["low", "medium", "high", "critical"]


async def dispatch_incident_notification(incident: Incident, tenant_id: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotificationChannel).where(
                NotificationChannel.tenant_id == tenant_id,
                NotificationChannel.is_active == True,
            )
        )
        channels = result.scalars().all()

    for channel in channels:
        # Check minimum severity filter
        min_idx = SEVERITY_ORDER.index(channel.min_severity) if channel.min_severity in SEVERITY_ORDER else 0
        inc_idx = SEVERITY_ORDER.index(incident.severity) if incident.severity in SEVERITY_ORDER else 1
        if inc_idx < min_idx:
            continue

        try:
            if channel.kind == "slack":
                await send_slack(channel.config["webhook_url"], incident)
            elif channel.kind == "webhook":
                await send_webhook(
                    channel.config["url"],
                    channel.config.get("headers", {}),
                    incident,
                )
            log.info("Notification sent via %s channel %s for incident %s", channel.kind, channel.id, incident.id)
        except Exception as e:
            log.error("Notification failed for channel %s: %s", channel.id, e)
