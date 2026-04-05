import json
from typing import AsyncGenerator

import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def publish_event(tenant_id: str, event: dict) -> None:
    r = await get_redis()
    channel = f"tenant:{tenant_id}:events"
    await r.publish(channel, json.dumps(event))


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
