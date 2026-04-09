"""
Remote command execution via the Pyxis agent.

Flow:
  1. Browser  → POST /api/v1/exec/{node_id}       submit command, waits up to 30s
  2. Agent    → GET  /api/v1/exec/poll             polls every 3s for pending command
  3. Agent    → POST /api/v1/exec/result/{cmd_id}  posts stdout/stderr + exit code
  4. Backend  → returns result to waiting browser request

Security:
- API key authentication on every request
- Server-side command allowlist (defense-in-depth — agent also validates)
- Agent runs as 'pyxis' system user with no capabilities
- Commands are read-only diagnostics only
"""
import asyncio
import re
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

PENDING_TTL  = 60     # agent must pick up command within 60s
RESULT_TTL   = 120    # result stored for 2 minutes
POLL_TIMEOUT = 30     # browser waits up to 30s for result
POLL_INTERVAL = 0.3   # check Redis every 300ms while waiting
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX    = 20  # max 20 exec commands per node per minute


# ── Server-side command validation ───────────────────────────────────────────

_BLOCK_PATTERNS = [
    re.compile(p, re.I) for p in [
        r'\brm\s+-',
        r'\brmdir\b', r'\bdd\s+', r'\bmkfs\b',
        r'\bfdisk\b', r'\bparted\b', r'\bshred\b',
        r'\bchmod\b', r'\bchown\b', r'\bchattr\b',
        r'\buseradd\b', r'\buserdel\b', r'\busermod\b',
        r'\bpasswd\b', r'\bchpasswd\b', r'\bvisudo\b',
        r'\bsu\s', r'\bsudo\s',
        r'\biptables\b', r'\bnftables\b', r'\bufw\b',
        r'\bshutdown\b', r'\breboot\b', r'\bhalt\b', r'\bpoweroff\b',
        r'\beval\b',
        r'>\s*/',                     # redirect to absolute path
        r'\|\s*(sh|bash|zsh|python|perl|ruby|node)\b',
        r'curl\s+.*\|\s*(sh|bash)',
        r'\bbase64\s+-d\b.*\|',
        r'\$\([^)]+\)',               # command substitution
        r'`[^`]+`',
        r'\bsystemctl\s+(start|stop|restart|disable|enable|mask)\b',
    ]
]

_ALLOW_PREFIXES = (
    "free", "df ", "du ", "top ", "htop", "ps ", "pgrep", "uptime",
    "uname", "hostname", "hostnamectl", "lscpu", "lsmem", "lsblk",
    "lsof ", "ss ", "netstat", "ip addr", "ip route", "ip link",
    "ping ", "traceroute ", "mtr ", "dig ", "nslookup ", "host ",
    "curl ", "wget ", "journalctl ", "dmesg",
    "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "systemctl show ", "systemctl list-units",
    "tail ", "head ", "grep ", "egrep ", "fgrep ",
    "find /var/log", "find /tmp",
    "ls ", "echo ", "date", "timedatectl", "who", "w ", "last ",
    "id", "whoami", "env", "printenv", "mount",
    "cat /proc/", "cat /sys/", "cat /etc/os-release",
    "vmstat", "iostat", "mpstat", "sar ", "openssl ",
)


def _validate_cmd(cmd: str) -> str | None:
    """Return an error message if the command is not allowed, else None."""
    cmd_s = cmd.strip()
    if len(cmd_s) > 512:
        return "Command too long (max 512 chars)"
    cmd_lower = cmd_s.lower()
    for pat in _BLOCK_PATTERNS:
        if pat.search(cmd_lower):
            return f"Command blocked by security policy"
    for prefix in _ALLOW_PREFIXES:
        if cmd_lower.startswith(prefix.lower()):
            return None  # allowed
    return "Command not in diagnostic allowlist"


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

    # Validate command before dispatching
    err = _validate_cmd(body.cmd)
    if err:
        log.warning("exec: rejected cmd for node=%s: %s — cmd=%r", node.name, err, body.cmd[:120])
        raise HTTPException(status_code=400, detail=err)

    r = await get_redis()

    # Rate limiting: max RATE_LIMIT_MAX commands per node per minute
    rate_key = f"exec:rate:{tenant.id}:{node.id}"
    count = await r.incr(rate_key)
    if count == 1:
        await r.expire(rate_key, RATE_LIMIT_WINDOW)
    if count > RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail=f"Rate limit: max {RATE_LIMIT_MAX} commands per minute per node")

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
