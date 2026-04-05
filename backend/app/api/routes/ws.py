"""
WebSocket endpoint. Frontend connects here to receive real-time events.
Each tenant has its own Redis pub/sub channel.
"""
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.tenant import Tenant

router = APIRouter()
settings = get_settings()


@router.websocket("/events")
async def websocket_events(
    websocket: WebSocket,
    api_key: str = Query(...),
):
    # Authenticate tenant by API key
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Tenant).where(Tenant.api_key == api_key, Tenant.is_active == True)
        )
        tenant = result.scalar_one_or_none()

    if not tenant:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    await websocket.accept()

    # Subscribe to this tenant's Redis channel
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    channel = f"tenant:{tenant.id}:events"
    await pubsub.subscribe(channel)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await redis.aclose()
