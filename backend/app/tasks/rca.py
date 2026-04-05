"""
RCA task — runs inside the ARQ worker process.
Retried automatically on failure with exponential backoff.
"""
from arq import ArqRedis

from app.core.database import AsyncSessionLocal
from app.ai.engine import _run_rca
from app.models.incident import Incident
from app.models.event import LogEvent
from sqlalchemy import select
import logging

log = logging.getLogger(__name__)


async def run_rca_task(ctx: dict, incident_id: str, event_id: str, tenant_id: str) -> None:
    """
    Fetch the incident and trigger event from DB, then run RCA.
    If this task fails, ARQ will retry it (see WorkerSettings.retry_jobs).
    """
    async with AsyncSessionLocal() as db:
        inc_result = await db.execute(select(Incident).where(Incident.id == incident_id))
        incident = inc_result.scalar_one_or_none()

        evt_result = await db.execute(select(LogEvent).where(LogEvent.id == event_id))
        event = evt_result.scalar_one_or_none()

        if not incident or not event:
            log.warning("run_rca_task: incident or event not found (id=%s, event=%s)", incident_id, event_id)
            return

        if incident.rca_full:
            log.info("run_rca_task: RCA already done for incident %s, skipping", incident_id)
            return

        log.info("run_rca_task: starting RCA for incident %s", incident_id)
        await _run_rca(incident, event, tenant_id, db)
        log.info("run_rca_task: completed RCA for incident %s", incident_id)


async def check_silent_nodes_task(ctx: dict) -> None:
    """
    Periodic task: fires incidents for nodes that haven't sent a heartbeat
    in SILENT_THRESHOLD_SECONDS. Runs every 2 minutes via cron.
    """
    from app.tasks.heartbeat import check_silent_nodes
    await check_silent_nodes()
