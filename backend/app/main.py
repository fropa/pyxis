from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine, Base
from app.core.redis import close_redis
from app.api.routes import ingest, topology, incidents, knowledge, ws, tenants, heartbeat, notifications, install, runbooks, deploy_events, analyze, traces

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enable pgvector extension and create all tables on startup
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    yield

    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Pyxis API",
    version="0.1.0",
    description="AI-powered infrastructure observability platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tenants.router,  prefix="/api/v1/tenants",   tags=["tenants"])
app.include_router(ingest.router,   prefix="/api/v1/ingest",    tags=["ingest"])
app.include_router(topology.router, prefix="/api/v1/topology",  tags=["topology"])
app.include_router(incidents.router,prefix="/api/v1/incidents", tags=["incidents"])
app.include_router(knowledge.router,prefix="/api/v1/knowledge", tags=["knowledge"])
app.include_router(ws.router,           prefix="/ws",                   tags=["websocket"])
app.include_router(heartbeat.router,     prefix="/api/v1/heartbeat",      tags=["heartbeat"])
app.include_router(notifications.router, prefix="/api/v1/notifications",  tags=["notifications"])
app.include_router(install.router,       prefix="/install",               tags=["install"])
app.include_router(runbooks.router,      prefix="/api/v1/runbooks",       tags=["runbooks"])
app.include_router(deploy_events.router, prefix="/api/v1/deploy-events",  tags=["deploy-events"])
app.include_router(analyze.router,       prefix="/api/v1/analyze",        tags=["analyze"])
app.include_router(traces.router,        prefix="/api/v1/traces",         tags=["traces"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
