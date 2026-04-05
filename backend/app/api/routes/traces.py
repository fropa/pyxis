"""
Trace / span ingestion and latency analytics.

POST /api/v1/traces/        — ingest a batch of spans (OTEL-style simplified)
GET  /api/v1/traces/services — list services with p50/p99/error-rate summary
GET  /api/v1/traces/services/{service}/timeseries — p99 + error rate over time (5-min buckets)
GET  /api/v1/traces/recent  — recent traces (grouped by trace_id)
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel
from sqlalchemy import select, func, Float, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.core.redis import get_redis
from app.ingestion.latency_detector import check_span
from app.models.span import Span
from app.models.tenant import Tenant

router = APIRouter()


# ── Ingest ────────────────────────────────────────────────────────────────────

class SpanIn(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service: str
    operation: str
    duration_ms: float
    status: str = "ok"           # ok | error | unset
    status_code: int | None = None
    started_at: datetime | None = None
    attributes: dict[str, Any] = {}


class TraceBatch(BaseModel):
    spans: list[SpanIn]


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def ingest_traces(
    batch: TraceBatch,
    background: BackgroundTasks,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    background.add_task(_process_batch, batch.spans, tenant.id, db)
    return {"accepted": len(batch.spans)}


async def _process_batch(spans: list[SpanIn], tenant_id: str, db: AsyncSession) -> None:
    redis = await get_redis()
    for s in spans:
        span = Span(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            trace_id=s.trace_id,
            span_id=s.span_id,
            parent_span_id=s.parent_span_id,
            service=s.service,
            operation=s.operation,
            duration_ms=s.duration_ms,
            status=s.status,
            status_code=s.status_code,
            attributes=s.attributes,
            started_at=s.started_at or datetime.now(timezone.utc),
        )
        db.add(span)
        await db.flush()
        await check_span(span, tenant_id, redis, db)
    await db.commit()


# ── Analytics ─────────────────────────────────────────────────────────────────

class ServiceSummary(BaseModel):
    service: str
    request_count: int
    error_count: int
    error_rate: float
    avg_ms: float
    p99_ms: float
    p50_ms: float


@router.get("/services", response_model=list[ServiceSummary])
async def list_services(
    hours: int = 1,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await db.execute(
        select(
            Span.service,
            func.count(Span.id).label("request_count"),
            func.sum(
                func.cast(Span.status == "error", Integer) +
                func.cast((Span.status_code >= 500), Integer)  # type: ignore[operator]
            ).label("error_count"),
            func.avg(Span.duration_ms).label("avg_ms"),
            func.percentile_cont(0.99).within_group(Span.duration_ms).label("p99_ms"),
            func.percentile_cont(0.50).within_group(Span.duration_ms).label("p50_ms"),
        )
        .where(
            Span.tenant_id == tenant.id,
            Span.started_at >= since,
            Span.parent_span_id.is_(None),  # root spans only
        )
        .group_by(Span.service)
        .order_by(func.count(Span.id).desc())
    )

    rows = result.all()
    return [
        ServiceSummary(
            service=r.service,
            request_count=r.request_count,
            error_count=int(r.error_count or 0),
            error_rate=round(int(r.error_count or 0) / max(r.request_count, 1), 4),
            avg_ms=round(float(r.avg_ms or 0), 2),
            p99_ms=round(float(r.p99_ms or 0), 2),
            p50_ms=round(float(r.p50_ms or 0), 2),
        )
        for r in rows
    ]


class TimeseriesPoint(BaseModel):
    bucket: str   # ISO datetime string
    p99_ms: float
    p50_ms: float
    avg_ms: float
    request_count: int
    error_count: int


@router.get("/services/{service}/timeseries", response_model=list[TimeseriesPoint])
async def service_timeseries(
    service: str,
    hours: int = 1,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 5-minute buckets
    bucket_expr = func.date_trunc("hour", Span.started_at) + func.cast(
        func.floor(func.extract("minute", Span.started_at) / 5) * 5,
        func.cast("text", None),  # type: ignore[call-overload]
    )

    # Simpler approach: use generate_series-like bucketing via raw func
    result = await db.execute(
        select(
            func.date_trunc(
                "minute",
                Span.started_at - func.cast(
                    func.mod(func.cast(func.extract("minute", Span.started_at), Integer), 5),
                    func.cast("interval", None),  # type: ignore[call-overload]
                )
            ).label("bucket"),
            func.percentile_cont(0.99).within_group(Span.duration_ms).label("p99_ms"),
            func.percentile_cont(0.50).within_group(Span.duration_ms).label("p50_ms"),
            func.avg(Span.duration_ms).label("avg_ms"),
            func.count(Span.id).label("request_count"),
            func.sum(
                func.cast(Span.status == "error", Integer)
            ).label("error_count"),
        )
        .where(
            Span.tenant_id == tenant.id,
            Span.service == service,
            Span.started_at >= since,
            Span.parent_span_id.is_(None),
        )
        .group_by("bucket")
        .order_by("bucket")
    )

    return [
        TimeseriesPoint(
            bucket=r.bucket.isoformat() if r.bucket else "",
            p99_ms=round(float(r.p99_ms or 0), 2),
            p50_ms=round(float(r.p50_ms or 0), 2),
            avg_ms=round(float(r.avg_ms or 0), 2),
            request_count=r.request_count,
            error_count=int(r.error_count or 0),
        )
        for r in result
    ]


class TraceOut(BaseModel):
    trace_id: str
    service: str
    operation: str
    duration_ms: float
    status: str
    status_code: int | None
    span_count: int
    started_at: datetime


@router.get("/recent", response_model=list[TraceOut])
async def recent_traces(
    hours: int = 1,
    service: str | None = None,
    limit: int = 50,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Get root spans (they represent full traces)
    q = (
        select(
            Span.trace_id,
            Span.service,
            Span.operation,
            Span.duration_ms,
            Span.status,
            Span.status_code,
            Span.started_at,
            func.count(Span.id).over(partition_by=Span.trace_id).label("span_count"),
        )
        .where(
            Span.tenant_id == tenant.id,
            Span.started_at >= since,
            Span.parent_span_id.is_(None),
        )
        .order_by(Span.started_at.desc())
        .limit(limit)
    )
    if service:
        q = q.where(Span.service == service)

    result = await db.execute(q)
    rows = result.all()

    return [
        TraceOut(
            trace_id=r.trace_id,
            service=r.service,
            operation=r.operation,
            duration_ms=r.duration_ms,
            status=r.status,
            status_code=r.status_code,
            span_count=r.span_count,
            started_at=r.started_at,
        )
        for r in rows
    ]
