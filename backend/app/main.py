import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine, Base
from app.core.redis import close_redis

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# Quieten noisy libs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
from app.api.routes import ingest, topology, incidents, knowledge, ws, tenants, heartbeat, notifications, install, runbooks, deploy_events, analyze, traces, assistant, exec, k8s, connections

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = settings.ANTHROPIC_API_KEY or ""
    if key.startswith("sk-ant-"):
        log.info("Startup: Anthropic API key configured (%s…%s)", key[:18], key[-4:])
    else:
        log.warning("Startup: ANTHROPIC_API_KEY is not set or invalid — AI features will fail")

    log.info("Startup: running DB migrations...")
    # Enable pgvector extension and create all tables on startup
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Schema migrations (idempotent — safe to run on every startup)
        await conn.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS postmortem TEXT"))
        await conn.execute(text(
            "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS parent_incident_id VARCHAR "
            "REFERENCES incidents(id) ON DELETE SET NULL"
        ))
        await conn.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS storm_size INTEGER DEFAULT 1"))
        await conn.execute(text("ALTER TABLE edges ADD COLUMN IF NOT EXISTS confidence FLOAT DEFAULT 0.7"))
        await conn.execute(text("ALTER TABLE edges ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ DEFAULT NOW()"))
        await conn.execute(text("ALTER TABLE edges ADD COLUMN IF NOT EXISTS observation_count INTEGER DEFAULT 1"))
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS node_name VARCHAR(255)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_log_events_node_name ON log_events (node_name)"))
        # Flow signal columns
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS request_id VARCHAR(255)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_log_events_request_id ON log_events (request_id) WHERE request_id IS NOT NULL"))
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS trace_id VARCHAR(255)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_log_events_trace_id ON log_events (trace_id) WHERE trace_id IS NOT NULL"))
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS client_ip VARCHAR(45)"))
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS upstream_addr VARCHAR(512)"))
        await conn.execute(text("ALTER TABLE log_events ADD COLUMN IF NOT EXISTS response_time_ms FLOAT"))
        await conn.execute(text("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS evidence_logs JSONB"))
        await conn.execute(text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ"))

    log.info("Startup: DB ready. Pyxis backend is up.")
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
app.include_router(assistant.router,     prefix="/api/v1/assistant",      tags=["assistant"])
app.include_router(exec.router,          prefix="/api/v1/exec",           tags=["exec"])
app.include_router(k8s.router,           prefix="/api/v1/k8s",            tags=["k8s"])
app.include_router(connections.router,   prefix="/api/v1/connections",    tags=["connections"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
