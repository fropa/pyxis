"""
Remote command execution via the Pyxis agent.

Flow:
  1. Browser  → POST /api/v1/exec/{node_id}       submit command, waits up to 30s
  2. Agent    → GET  /api/v1/exec/poll             polls every 3s for pending command
  3. Agent    → POST /api/v1/exec/result/{cmd_id}  posts stdout/stderr + exit code
  4. Backend  → returns result to waiting browser request

Security: only tenants with a valid API key can submit commands.
The agent authenticates with the same key. Commands run as whatever user
the agent service runs as (typically root on linux_host).
"""
import asyncio
import uuid
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_tenant
from app.core.redis import get_redis
from app.models.tenant import Tenant
from app.models.topology import Node

router = APIRouter()
log = logging.getLogger(__name__)

PENDING_TTL = 60      # agent must pick up command within 60s
RESULT_TTL  = 120     # result stored for 2 minutes
POLL_TIMEOUT = 30     # browser waits up to 30s for result
POLL_INTERVAL = 0.3   # check Redis every 300ms while waiting


class ExecRequest(BaseModel):
    cmd: str


class ExecResult(BaseModel):
    cmd_id: str
    output: str
    exit_code: int
    duration_ms: int


class AgentResult(BaseModel):
    output: str
    exit_code: int
    duration_ms: int


# ── Browser: submit a command ─────────────────────────────────────────────────

@router.post("/nodes/{node_id}", response_model=ExecResult)
async def exec_command(
    node_id: str,
    body: ExecRequest,
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    node_r = await db.execute(
        select(Node).where(Node.id == node_id, Node.tenant_id == tenant.id, Node.deleted_at.is_(None))
    )
    node = node_r.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    r = await get_redis()
    cmd_id = str(uuid.uuid4())
    pending_key = f"exec:pending:{tenant.id}:{node.external_id}"
    result_key  = f"exec:result:{cmd_id}"

    # Store pending command for agent to pick up
    await r.setex(pending_key, PENDING_TTL, json.dumps({"cmd_id": cmd_id, "cmd": body.cmd}))
    log.info("exec: queued cmd_id=%s node=%s cmd=%r", cmd_id, node.name, body.cmd[:80])

    # Wait for agent to post result
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        raw = await r.get(result_key)
        if raw:
            result = json.loads(raw)
            log.info("exec: result cmd_id=%s exit=%s in %.1fs", cmd_id, result.get("exit_code"), elapsed)
            return ExecResult(
                cmd_id=cmd_id,
                output=result.get("output", ""),
                exit_code=result.get("exit_code", -1),
                duration_ms=result.get("duration_ms", 0),
            )

    # Timed out — clean up pending command
    await r.delete(pending_key)
    raise HTTPException(status_code=408, detail="Agent did not respond in time. Is it running?")


# ── Agent: poll for a pending command ─────────────────────────────────────────

@router.get("/poll")
async def poll_command(
    node_name: str,
    tenant: Tenant = Depends(get_current_tenant),
):
    r = await get_redis()
    pending_key = f"exec:pending:{tenant.id}:{node_name}"
    raw = await r.get(pending_key)
    if raw:
        data = json.loads(raw)
        await r.delete(pending_key)  # consume it — one command at a time
        return {"cmd_id": data["cmd_id"], "cmd": data["cmd"]}
    return {"cmd_id": None, "cmd": None}


# ── Agent: post command result ─────────────────────────────────────────────────

@router.post("/result/{cmd_id}")
async def post_result(
    cmd_id: str,
    body: AgentResult,
    tenant: Tenant = Depends(get_current_tenant),
):
    r = await get_redis()
    result_key = f"exec:result:{cmd_id}"
    await r.setex(result_key, RESULT_TTL, json.dumps({
        "output": body.output,
        "exit_code": body.exit_code,
        "duration_ms": body.duration_ms,
    }))
    return {"ok": True}
