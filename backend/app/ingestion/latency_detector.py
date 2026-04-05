"""
Latency anomaly detector.

For each root span (no parent) we:
1. Record duration in a Redis sliding window (last 200 samples per service+operation).
2. Compute baseline p99 from the stored window.
3. Fire a latency incident if:
   - duration > SPIKE_MULTIPLIER × baseline p99   (latency spike), OR
   - error rate in the last 5 min > ERROR_RATE_THRESHOLD (error storm)

This mirrors how the log-based detector works — same incident + RCA pipeline,
but triggered by trace data instead of log anomaly patterns.
"""
import json
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.span import Span

# Tune these to taste
WINDOW_SIZE = 200          # keep last N samples per service+operation
MIN_SAMPLES = 10           # don't fire until we have a baseline
SPIKE_MULTIPLIER = 3.0     # fire if duration > 3× p99 baseline
ERROR_RATE_THRESHOLD = 0.2  # fire if >20% errors in last 5 min
ERROR_WINDOW_SECONDS = 300  # 5-minute error rate window

_LATENCY_KEY = "latency:{tenant}:{service}:{op}:samples"
_ERROR_KEY   = "latency:{tenant}:{service}:{op}:errors"
_TOTAL_KEY   = "latency:{tenant}:{service}:{op}:total"


async def check_span(
    span: Span,
    tenant_id: str,
    redis: Redis,
    db: AsyncSession,
) -> None:
    """
    Called for every ingested span. Only root spans (no parent) are
    used for latency baselining — they represent full end-to-end requests.
    """
    if span.parent_span_id:
        return  # only root spans for baseline

    op_key = _safe_key(span.operation)
    samples_key = _LATENCY_KEY.format(tenant=tenant_id, service=span.service, op=op_key)
    error_key   = _ERROR_KEY.format(tenant=tenant_id, service=span.service, op=op_key)
    total_key   = _TOTAL_KEY.format(tenant=tenant_id, service=span.service, op=op_key)

    # Push duration into sliding window
    pipe = redis.pipeline()
    pipe.rpush(samples_key, span.duration_ms)
    pipe.ltrim(samples_key, -WINDOW_SIZE, -1)

    # Track error rate using a time-bucketed counter (5-min bucket)
    bucket = int(span.started_at.timestamp()) // ERROR_WINDOW_SECONDS
    if span.status == "error" or (span.status_code and span.status_code >= 500):
        pipe.incr(f"{error_key}:{bucket}")
        pipe.expire(f"{error_key}:{bucket}", ERROR_WINDOW_SECONDS * 3)
    pipe.incr(f"{total_key}:{bucket}")
    pipe.expire(f"{total_key}:{bucket}", ERROR_WINDOW_SECONDS * 3)
    await pipe.execute()

    # Get current window for analysis
    raw_samples = await redis.lrange(samples_key, 0, -1)
    if len(raw_samples) < MIN_SAMPLES:
        return  # not enough data yet

    samples = sorted(float(v) for v in raw_samples)

    # Compute baseline p99 from all-but-last-5 samples
    baseline_samples = samples[:-5] if len(samples) > 5 else samples
    p99 = _percentile(baseline_samples, 99)
    current = span.duration_ms

    # --- Latency spike check ---
    if p99 > 0 and current > SPIKE_MULTIPLIER * p99:
        dedup_key = f"latency_incident:{tenant_id}:{span.service}:{op_key}"
        already_open = await redis.get(dedup_key)
        if not already_open:
            await redis.setex(dedup_key, 300, "1")  # suppress for 5 min
            await _fire_latency_incident(
                span=span,
                tenant_id=tenant_id,
                reason=f"p99 latency spike: {current:.0f}ms vs baseline {p99:.0f}ms ({SPIKE_MULTIPLIER}× threshold)",
                db=db,
                redis=redis,
            )
        return

    # --- Error rate check ---
    errors = int(await redis.get(f"{error_key}:{bucket}") or 0)
    total  = int(await redis.get(f"{total_key}:{bucket}") or 1)
    error_rate = errors / max(total, 1)

    if error_rate >= ERROR_RATE_THRESHOLD and total >= 5:
        dedup_key = f"error_rate_incident:{tenant_id}:{span.service}:{op_key}"
        already_open = await redis.get(dedup_key)
        if not already_open:
            await redis.setex(dedup_key, 300, "1")
            await _fire_latency_incident(
                span=span,
                tenant_id=tenant_id,
                reason=f"error rate spike: {error_rate*100:.1f}% of requests failed ({errors}/{total} in last 5 min)",
                db=db,
                redis=redis,
            )


async def _fire_latency_incident(
    span: Span,
    tenant_id: str,
    reason: str,
    db: AsyncSession,
    redis: Redis,
) -> None:
    """Create an incident and enqueue RCA — same pipeline as log-based incidents."""
    from app.models.incident import Incident
    from app.core.redis import publish_event
    from app.ai.engine import _get_arq_pool

    incident = Incident(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        title=f"[{span.service}] {span.operation} — {reason}",
        severity=_severity_from_reason(reason),
        status="open",
        started_at=span.started_at,
    )
    db.add(incident)
    await db.commit()
    await db.refresh(incident)

    await publish_event(tenant_id, {
        "type": "incident_opened",
        "incident_id": incident.id,
        "title": incident.title,
        "severity": incident.severity,
        "source": "apm",
        "service": span.service,
        "operation": span.operation,
        "detection_reason": reason,
    })

    # Enqueue RCA with span context attached as attributes
    pool = await _get_arq_pool()
    await pool.enqueue_job(
        "run_rca_task",
        incident.id,
        None,          # no log event — RCA task handles None trigger
        tenant_id,
    )


def _percentile(sorted_data: list[float], p: int) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)


def _safe_key(s: str) -> str:
    """Strip characters that would break Redis key syntax."""
    return s.replace(":", "_").replace(" ", "_").replace("/", "_")[:80]


def _severity_from_reason(reason: str) -> str:
    if "error rate" in reason:
        return "high"
    if "spike" in reason:
        return "medium"
    return "medium"
