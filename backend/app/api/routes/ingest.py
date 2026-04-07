"""
Log ingestion endpoint.
Agents (Linux hosts, K8s event watchers, CI pipelines) POST here.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, AsyncSessionLocal
from app.core.deps import get_current_tenant
from app.ingestion.normalizer import normalize_event
from app.ai.engine import analyze_event
from app.models.tenant import Tenant

router = APIRouter()
log = logging.getLogger(__name__)


class RawEvent(BaseModel):
    source: str                         # "syslog" | "k8s_event" | "ci_pipeline" | "app_log"
    timestamp: datetime | None = None   # event time; defaults to now if missing
    level: str = "info"
    node_name: str | None = None        # hostname / pod name / node name
    node_kind: str | None = None        # "linux_host" | "k8s_pod" | ...
    raw: str                            # original raw log line
    parsed: dict[str, Any] = {}         # pre-parsed fields from agent (optional)
    labels: dict[str, str] = {}         # k8s labels or host tags


class IngestBatch(BaseModel):
    events: list[RawEvent]


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def ingest_events(
    batch: IngestBatch,
    background: BackgroundTasks,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    log.debug("ingest: accepted %d events from node=%s",
              len(batch.events), batch.events[0].node_name if batch.events else "?")
    # Use a fresh session in the background task — the request session may be
    # closed before the background task completes.
    background.add_task(_process_batch, batch.events, tenant.id)
    return {"accepted": len(batch.events)}


async def _process_batch(events: list[RawEvent], tenant_id: str):
    async with AsyncSessionLocal() as db:
        for raw_event in events:
            try:
                log_event = await normalize_event(raw_event, tenant_id, db)
                await analyze_event(log_event, tenant_id, db)
            except Exception as e:
                log.error("ingest: failed to process event from node=%s source=%s: %s",
                          raw_event.node_name, raw_event.source, e)
