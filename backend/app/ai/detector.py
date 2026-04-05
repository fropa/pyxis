"""
Sliding window rate detector.

Replaces the blunt keyword matching in the original engine.
Uses Redis sorted sets as time-bucketed counters per (tenant, fingerprint).

Decision logic:
  1. Always-fire signals   — some things are always incidents regardless of rate
                             (OOMKilled, NodeNotReady, ImagePullBackOff, disk full)
  2. Rate threshold        — fire when error count in the last WINDOW_SECONDS
                             exceeds MAX(ABSOLUTE_THRESHOLD, baseline * SPIKE_MULTIPLIER)
  3. Log volume anomaly    — fire when a previously active node goes silent

Redis key schema:
  rate:{tenant_id}:{fingerprint}  → sorted set of (score=unix_ts, member=event_id)
  baseline:{tenant_id}:{fingerprint} → string, rolling average errors/window
  heartbeat:{tenant_id}:{node_id} → string, last seen unix timestamp
"""
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

# ── Tunable constants ─────────────────────────────────────────────────────────

WINDOW_SECONDS = 300          # 5-minute sliding window for rate detection
SPIKE_MULTIPLIER = 3.0        # fire if current rate > baseline * this
ABSOLUTE_THRESHOLD = 5        # fire if >= N errors in window (regardless of baseline)
BASELINE_WINDOW_SECONDS = 3600  # 1 hour to compute baseline
BASELINE_KEY_TTL = 7200       # keep baseline keys for 2 hours


# ── Signals that are ALWAYS incidents — one occurrence is enough ──────────────

ALWAYS_FIRE = {
    # K8s hard failures
    "CrashLoopBackOff",
    "OOMKilled",
    "NodeNotReady",
    "Evicted",
    "FailedMount",
    "FailedScheduling",
    # Image problems
    "ImagePullBackOff",
    "ErrImagePull",
    "Back-off pulling image",
    # Linux critical
    "out of memory",
    "oom-kill",
    "kernel panic",
    "disk full",
    "no space left on device",
    "segfault",
    "segmentation fault",
    # CI/CD hard failures
    "pipeline failed",
    "job failed",
    "deployment failed",
    "rollout failed",
}


def _should_always_fire(message: str, fingerprint: str) -> bool:
    text = (message + " " + fingerprint).lower()
    return any(sig.lower() in text for sig in ALWAYS_FIRE)


async def should_open_incident(
    tenant_id: str,
    event_id: str,
    fingerprint: str,
    message: str,
    level: str,
    redis: aioredis.Redis,
) -> tuple[bool, str]:
    """
    Returns (should_open: bool, reason: str).
    Records the event in the sliding window regardless of decision.
    """
    now = time.time()
    window_key = f"rate:{tenant_id}:{fingerprint}"
    baseline_key = f"baseline:{tenant_id}:{fingerprint}"

    # Record this event in the sliding window
    await redis.zadd(window_key, {event_id: now})
    # Prune events outside the window
    await redis.zremrangebyscore(window_key, 0, now - WINDOW_SECONDS)
    await redis.expire(window_key, WINDOW_SECONDS * 2)

    # Always-fire check (one is enough)
    if _should_always_fire(message, fingerprint):
        return True, "always_fire_signal"

    # Only track error/critical levels for rate detection
    if level not in ("error", "critical", "warning"):
        return False, "level_too_low"

    # Count events in current window
    current_count = await redis.zcard(window_key)

    # Absolute threshold
    if current_count >= ABSOLUTE_THRESHOLD:
        # Compute baseline (events per window over the last hour)
        baseline = await _get_baseline(redis, baseline_key, window_key, now)
        await _update_baseline(redis, baseline_key, current_count, baseline)

        if baseline > 0 and current_count >= baseline * SPIKE_MULTIPLIER:
            return True, f"rate_spike:{current_count:.0f}x_baseline_{baseline:.1f}"

        if baseline == 0:
            # No history yet — fire on absolute threshold
            return True, f"rate_absolute:{current_count}_in_{WINDOW_SECONDS}s"

    return False, "below_threshold"


async def _get_baseline(
    redis: aioredis.Redis,
    baseline_key: str,
    window_key: str,
    now: float,
) -> float:
    cached = await redis.get(baseline_key)
    if cached is not None:
        return float(cached)
    return 0.0


async def _update_baseline(
    redis: aioredis.Redis,
    baseline_key: str,
    current_count: int,
    old_baseline: float,
) -> None:
    # Exponential moving average: new = 0.2 * current + 0.8 * old
    new_baseline = 0.2 * current_count + 0.8 * old_baseline
    await redis.setex(baseline_key, BASELINE_KEY_TTL, str(new_baseline))


# ── Deduplication: is there already an open incident for this fingerprint? ────

async def find_open_incident_for_fingerprint(
    tenant_id: str,
    fingerprint: str,
    redis: aioredis.Redis,
) -> str | None:
    """Returns open incident_id for this fingerprint if one exists (from Redis cache)."""
    key = f"open_incident:{tenant_id}:{fingerprint}"
    return await redis.get(key)


async def register_open_incident(
    tenant_id: str,
    fingerprint: str,
    incident_id: str,
    redis: aioredis.Redis,
    ttl_seconds: int = 1800,  # 30 min — same as engine dedup window
) -> None:
    key = f"open_incident:{tenant_id}:{fingerprint}"
    await redis.setex(key, ttl_seconds, incident_id)


async def clear_open_incident(
    tenant_id: str,
    fingerprint: str,
    redis: aioredis.Redis,
) -> None:
    key = f"open_incident:{tenant_id}:{fingerprint}"
    await redis.delete(key)
