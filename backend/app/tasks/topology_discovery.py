"""
Auto-discovers service topology from all available data sources.

Sources (ranked by confidence):
  1. Spans       (0.95) — parent span in A called child span in B → A calls B
  2. Claude LLM  (0.80) — Claude reads sampled error logs and infers relationships
  3. Logs regex  (0.70) — "connecting to X", "upstream X", "calling X" patterns
  4. Co-incident (0.60) — services that appear together in incident titles/sources
  5. Co-deploy   (0.50) — services deployed within a 5-minute window

Edge lifecycle:
  - First seen: created with confidence from primary source
  - Subsequent observations: observation_count++, last_seen updated, confidence nudged higher
  - Unseen for 7 days: flagged stale in metadata
  - Unseen for 14 days: soft-deleted (metadata stale=true, won't show in topology)

After edges are updated, a health propagation pass runs:
  - Span error rates → node status (healthy / degraded / down)
  - Downstream effects: if a node is "down", its callers become "degraded"

Runs every 10 minutes via ARQ cron. Also callable directly via POST /api/v1/topology/discover.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import anthropic
import time
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.span import Span
from app.models.topology import Node, Edge
from app.models.event import LogEvent
from app.models.tenant import Tenant
from app.models.deploy_event import DeployEvent
from app.models.incident import Incident
from app.tasks.heartbeat import HEARTBEAT_KEY_PREFIX, SILENT_THRESHOLD_SECONDS

settings = get_settings()
_anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

# ── Confidence constants per source ───────────────────────────────────────────
CONF_SPAN     = 0.95
CONF_CLAUDE   = 0.80
CONF_LOG      = 0.70
CONF_INCIDENT = 0.60
CONF_DEPLOY   = 0.50

STALE_DAYS   = 7
PRUNE_DAYS   = 14


class DiscoveredEdge(NamedTuple):
    src: str   # service/node name
    dst: str
    kind: str  # calls | dependency | co-deployed | co-occurrence
    confidence: float


# ── ARQ cron entry point ─────────────────────────────────────────────────────

async def discover_topology_task(ctx: dict) -> None:
    async with AsyncSessionLocal() as db:
        tenants_result = await db.execute(select(Tenant).where(Tenant.is_active == True))
        tenants = tenants_result.scalars().all()
        for tenant in tenants:
            try:
                await _discover_for_tenant(tenant.id, db)
            except Exception:
                pass  # never crash the worker over one tenant


# ── Per-tenant discovery ─────────────────────────────────────────────────────

async def _discover_for_tenant(tenant_id: str, db: AsyncSession) -> dict:
    """
    Run full discovery for one tenant. Returns stats dict.
    Called from the ARQ cron task and from the REST endpoint.
    """
    now = datetime.now(timezone.utc)
    since_1h  = now - timedelta(hours=1)
    since_24h = now - timedelta(hours=24)

    # ── 1. Span-based discovery ───────────────────────────────────────────────
    span_edges = await _discover_from_spans(tenant_id, since_1h, db)

    # ── 2. Log-regex discovery (service name patterns) ────────────────────────
    raw_logs, log_edges = await _discover_from_logs(tenant_id, since_1h, db)

    # ── 2b. Log IP-scan (known node IPs in log messages) ─────────────────────
    ip_log_edges = await _discover_from_log_ips(tenant_id, since_1h, db)

    # ── 3. Co-deploy discovery ────────────────────────────────────────────────
    deploy_edges = await _discover_from_deploys(tenant_id, since_24h, db)

    # ── 4. Incident co-occurrence ─────────────────────────────────────────────
    incident_edges = await _discover_from_incidents(tenant_id, since_24h, db)

    # ── 5. Claude-assisted log analysis ──────────────────────────────────────
    claude_edges: list[DiscoveredEdge] = []
    if raw_logs:
        claude_edges = await _discover_from_claude(raw_logs, tenant_id)

    # ── Merge all edges (highest confidence wins per pair) ────────────────────
    all_edges: dict[tuple[str, str], DiscoveredEdge] = {}
    for edge_list in [deploy_edges, incident_edges, log_edges, ip_log_edges, claude_edges, span_edges]:
        for e in edge_list:
            key = (e.src, e.dst)
            if key not in all_edges or e.confidence > all_edges[key].confidence:
                all_edges[key] = e

    edges_written = 0
    node_map: dict[str, str] = {}

    if all_edges:
        # ── Upsert nodes and edges ────────────────────────────────────────────
        service_names = {s for e in all_edges.values() for s in (e.src, e.dst)}
        node_map = await _upsert_service_nodes(tenant_id, service_names, db)
        edges_written = await _upsert_edges(tenant_id, all_edges, node_map, db)
        # ── Prune stale edges ─────────────────────────────────────────────────
        await _prune_stale_edges(tenant_id, now, db)

    # ── Update node statuses from heartbeats (always runs) ───────────────────
    await _update_node_statuses_from_heartbeats(tenant_id, db)

    # ── Health propagation from span error rates ──────────────────────────────
    await _propagate_health(tenant_id, since_1h, db)

    sources_used = []
    if span_edges:     sources_used.append("spans")
    if log_edges:      sources_used.append("logs")
    if ip_log_edges:   sources_used.append("log_ips")
    if deploy_edges:   sources_used.append("deploys")
    if incident_edges: sources_used.append("incidents")
    if claude_edges:   sources_used.append("claude")

    return {
        "edges_found": edges_written,
        "nodes_found": len(node_map),
        "sources": sources_used,
        "last_run": now.isoformat(),
    }


# ── Discovery sources ─────────────────────────────────────────────────────────

async def _discover_from_spans(
    tenant_id: str, since: datetime, db: AsyncSession
) -> list[DiscoveredEdge]:
    """Parent span in service A → child span in service B means A calls B."""
    child_result = await db.execute(
        select(Span.service, Span.span_id, Span.parent_span_id)
        .where(Span.tenant_id == tenant_id, Span.started_at >= since, Span.parent_span_id.isnot(None))
    )
    children = child_result.all()

    all_result = await db.execute(
        select(Span.span_id, Span.service)
        .where(Span.tenant_id == tenant_id, Span.started_at >= since)
    )
    span_service = {row.span_id: row.service for row in all_result}

    seen: set[tuple[str, str]] = set()
    edges = []
    for child in children:
        caller = span_service.get(child.parent_span_id)
        if caller and caller != child.service:
            pair = (caller, child.service)
            if pair not in seen:
                seen.add(pair)
                edges.append(DiscoveredEdge(caller, child.service, "calls", CONF_SPAN))
    return edges


async def _discover_from_logs(
    tenant_id: str, since: datetime, db: AsyncSession
) -> tuple[list[str], list[DiscoveredEdge]]:
    """
    Returns (raw_log_messages_for_claude, discovered_edges_from_regex).
    """
    log_result = await db.execute(
        select(LogEvent.source, LogEvent.message)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.message.isnot(None),
        )
        .limit(500)
    )
    rows = log_result.all()

    patterns = [
        r"connecting to\s+([a-zA-Z0-9_-]+)",
        r"calling\s+([a-zA-Z0-9_-]+)",
        r"request to\s+([a-zA-Z0-9_-]+)",
        r"upstream\s+([a-zA-Z0-9_-]+)",
        r"backend\s+([a-zA-Z0-9_-]+)",
        r"depends on\s+([a-zA-Z0-9_-]+)",
        r"via\s+([a-zA-Z0-9_-]+)\s+service",
        r"host[:\s]+([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)",
        r"grpc://([a-zA-Z0-9_-]+)",
        r"http[s]?://([a-zA-Z0-9_-]+)",
    ]

    seen: set[tuple[str, str]] = set()
    edges = []
    raw_messages = []

    for row in rows:
        if not row.message or not row.source:
            continue
        raw_messages.append(f"[{row.source}] {row.message[:200]}")
        for pat in patterns:
            m = re.search(pat, row.message, re.IGNORECASE)
            if m:
                target = m.group(1).rstrip(".").lower()
                src = row.source.lower()
                if src and target and src != target and len(target) > 2:
                    pair = (src, target)
                    if pair not in seen:
                        seen.add(pair)
                        edges.append(DiscoveredEdge(src, target, "dependency", CONF_LOG))

    return raw_messages[:100], edges  # cap raw messages sent to Claude


_IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


async def _discover_from_log_ips(
    tenant_id: str, since: datetime, db: AsyncSession
) -> list[DiscoveredEdge]:
    """
    Scan stored log messages for known node IP addresses.
    If node A's logs contain node B's IP, node A communicates with node B.
    This catches connections that ss/proc/arp all missed (e.g. very brief sessions,
    connections that happened before the agent started, or custom app logs).
    """
    # Get all nodes with known IPs for this tenant
    node_r = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.deleted_at.is_(None))
    )
    nodes = node_r.scalars().all()

    # Build ip → node_name map (excluding nodes without IPs)
    ip_to_name: dict[str, str] = {}
    for n in nodes:
        ip = (n.metadata_ or {}).get("ip_address")
        if ip:
            ip_to_name[ip] = n.external_id

    if not ip_to_name:
        return []

    # Fetch recent logs that contain IP-like strings
    log_r = await db.execute(
        select(LogEvent.node_name, LogEvent.message)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.message.isnot(None),
            LogEvent.node_name.isnot(None),
        )
        .limit(2000)
    )
    rows = log_r.all()

    seen: set[tuple[str, str]] = set()
    edges = []
    for row in rows:
        if not row.message or not row.node_name:
            continue
        # Quick pre-filter: skip lines without any digit patterns to avoid regex on every line
        if "." not in row.message:
            continue
        for ip in _IP_RE.findall(row.message):
            peer_name = ip_to_name.get(ip)
            if peer_name and peer_name != row.node_name:
                pair = (row.node_name, peer_name)
                if pair not in seen:
                    seen.add(pair)
                    edges.append(DiscoveredEdge(row.node_name, peer_name, "network", CONF_LOG))

    return edges


async def _discover_from_deploys(
    tenant_id: str, since: datetime, db: AsyncSession
) -> list[DiscoveredEdge]:
    """Services deployed within 5 minutes of each other are likely related."""
    result = await db.execute(
        select(DeployEvent.service, DeployEvent.deployed_at)
        .where(DeployEvent.tenant_id == tenant_id, DeployEvent.deployed_at >= since)
        .order_by(DeployEvent.deployed_at)
    )
    deploys = result.all()

    seen: set[tuple[str, str]] = set()
    edges = []
    for i, d1 in enumerate(deploys):
        for d2 in deploys[i + 1:]:
            delta = abs((d2.deployed_at - d1.deployed_at).total_seconds())
            if delta > 300:
                break
            if d1.service != d2.service:
                pair = tuple(sorted([d1.service, d2.service]))
                if pair not in seen:
                    seen.add(pair)
                    edges.append(DiscoveredEdge(pair[0], pair[1], "co-deployed", CONF_DEPLOY))
    return edges


async def _discover_from_incidents(
    tenant_id: str, since: datetime, db: AsyncSession
) -> list[DiscoveredEdge]:
    """
    Services that co-appear in incident titles/sources within 10 minutes
    of each other are likely dependent. We parse the incident title for
    service names extracted via source field on related log events.
    """
    result = await db.execute(
        select(LogEvent.source, LogEvent.incident_id, LogEvent.event_ts)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.incident_id.isnot(None),
            LogEvent.event_ts >= since,
            LogEvent.source.isnot(None),
        )
        .order_by(LogEvent.incident_id, LogEvent.event_ts)
    )
    rows = result.all()

    # Group services by incident
    by_incident: dict[str, set[str]] = {}
    for row in rows:
        if row.incident_id not in by_incident:
            by_incident[row.incident_id] = set()
        by_incident[row.incident_id].add(row.source)

    seen: set[tuple[str, str]] = set()
    edges = []
    for services in by_incident.values():
        svc_list = list(services)
        for i in range(len(svc_list)):
            for j in range(i + 1, len(svc_list)):
                pair = tuple(sorted([svc_list[i], svc_list[j]]))
                if pair not in seen:
                    seen.add(pair)
                    edges.append(DiscoveredEdge(pair[0], pair[1], "co-occurrence", CONF_INCIDENT))
    return edges


async def _discover_from_claude(
    log_lines: list[str], tenant_id: str
) -> list[DiscoveredEdge]:
    """
    Ask Claude to read a sample of log messages and infer service dependencies
    that regex patterns miss (e.g. custom log formats, implicit references).
    Returns structured edge list.
    """
    log_sample = "\n".join(log_lines[:80])

    prompt = f"""Analyze these infrastructure log lines and identify service dependency relationships.

Log lines (format: [service_name] log_message):
```
{log_sample}
```

Task: Find pairs of services where one clearly calls or depends on another, based on the log evidence.
Only include relationships you're confident about from the logs — do NOT guess.

Respond with ONLY a JSON array, no explanation. Each item:
{{"from": "service_a", "to": "service_b", "reason": "brief evidence from logs"}}

If no relationships are found, return an empty array: []"""

    try:
        message = await _anthropic.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=512,
            system="You are analyzing infrastructure logs to discover service dependencies. Be precise and conservative — only report relationships clearly evidenced in the logs.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Extract JSON array from response
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return []

        items = json.loads(m.group(0))
        edges = []
        for item in items:
            src = str(item.get("from", "")).strip().lower()
            dst = str(item.get("to", "")).strip().lower()
            if src and dst and src != dst:
                edges.append(DiscoveredEdge(src, dst, "calls", CONF_CLAUDE))
        return edges
    except Exception:
        return []


# ── Node / Edge persistence ────────────────────────────────────────────────────

async def _upsert_service_nodes(
    tenant_id: str, names: set[str], db: AsyncSession
) -> dict[str, str]:
    """Ensure a Node exists for each service name. Returns name→id map."""
    result = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.external_id.in_(names))
    )
    existing: dict[str, Node] = {n.external_id: n for n in result.scalars().all()}
    node_map: dict[str, str] = {name: node.id for name, node in existing.items()}

    now = datetime.now(timezone.utc)
    for name in names:
        if name in existing:
            # Update last_seen
            existing[name].last_seen = now
        else:
            node = Node(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                external_id=name,
                name=name,
                kind="service",
                status="unknown",
                labels={"discovered": "auto"},
                metadata_={"source": "auto_discovery"},
            )
            db.add(node)
            node_map[name] = node.id

    await db.flush()
    return node_map


async def _upsert_edges(
    tenant_id: str,
    all_edges: dict[tuple[str, str], DiscoveredEdge],
    node_map: dict[str, str],
    db: AsyncSession,
) -> int:
    """Create or update edges. Returns number of edges written."""
    now = datetime.now(timezone.utc)
    written = 0

    # Fetch all existing edges for this tenant in one query
    src_ids = {node_map[e.src] for e in all_edges.values() if e.src in node_map}
    dst_ids = {node_map[e.dst] for e in all_edges.values() if e.dst in node_map}
    if not src_ids or not dst_ids:
        return 0

    existing_result = await db.execute(
        select(Edge).where(
            Edge.tenant_id == tenant_id,
            Edge.source_id.in_(src_ids),
            Edge.target_id.in_(dst_ids),
        )
    )
    existing_edges: dict[tuple[str, str], Edge] = {
        (e.source_id, e.target_id): e for e in existing_result.scalars().all()
    }

    for (src_name, dst_name), discovered in all_edges.items():
        src_id = node_map.get(src_name)
        dst_id = node_map.get(dst_name)
        if not src_id or not dst_id:
            continue

        existing = existing_edges.get((src_id, dst_id))
        if existing:
            # Update: nudge confidence toward new value (exponential moving average)
            existing.confidence = min(
                0.99,
                existing.confidence * 0.8 + discovered.confidence * 0.2
            )
            existing.last_seen = now
            existing.observation_count = (existing.observation_count or 1) + 1
            # Upgrade kind if higher confidence source wins
            if discovered.confidence > CONF_LOG:
                existing.kind = discovered.kind
            # Clear stale flag if re-observed
            meta = dict(existing.metadata_ or {})
            meta.pop("stale", None)
            existing.metadata_ = meta
        else:
            db.add(Edge(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                source_id=src_id,
                target_id=dst_id,
                kind=discovered.kind,
                confidence=discovered.confidence,
                last_seen=now,
                observation_count=1,
                metadata_={"discovered": "auto"},
            ))
        written += 1

    await db.flush()
    return written


async def _update_node_statuses_from_heartbeats(tenant_id: str, db: AsyncSession) -> None:
    """
    Check Redis heartbeat keys for all nodes in this tenant.
    Mark nodes healthy/down based on whether their heartbeat is recent.
    This is the primary way linux_host / k8s_node agents appear as up or down.
    """
    r = await get_redis()
    result = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.deleted_at.is_(None))
    )
    nodes = result.scalars().all()
    if not nodes:
        return

    now_ts = time.time()
    changed = False
    for node in nodes:
        key = f"{HEARTBEAT_KEY_PREFIX}:{tenant_id}:{node.id}"
        raw = await r.get(key)
        if raw is not None:
            age = now_ts - float(raw)
            new_status = "healthy" if age < SILENT_THRESHOLD_SECONDS else "down"
        else:
            # No heartbeat key at all — if node was recently created keep unknown,
            # otherwise mark down
            age_since_creation = (datetime.now(timezone.utc) - node.first_seen).total_seconds()
            if age_since_creation < SILENT_THRESHOLD_SECONDS * 2:
                continue  # brand new node, give it time to send first heartbeat
            new_status = "down"

        if node.status != new_status:
            node.status = new_status
            changed = True

    if changed:
        await db.flush()


async def _prune_stale_edges(tenant_id: str, now: datetime, db: AsyncSession) -> None:
    """Flag edges not seen in 7+ days as stale. Hide edges not seen in 14+ days."""
    stale_cutoff = now - timedelta(days=STALE_DAYS)
    prune_cutoff = now - timedelta(days=PRUNE_DAYS)

    result = await db.execute(
        select(Edge).where(
            Edge.tenant_id == tenant_id,
            Edge.last_seen <= stale_cutoff,
        )
    )
    for edge in result.scalars().all():
        meta = dict(edge.metadata_ or {})
        if edge.last_seen and edge.last_seen.replace(tzinfo=timezone.utc) <= prune_cutoff:
            meta["stale"] = True
            meta["hidden"] = True
        else:
            meta["stale"] = True
        edge.metadata_ = meta

    await db.flush()


# ── Health propagation ────────────────────────────────────────────────────────

async def _propagate_health(tenant_id: str, since: datetime, db: AsyncSession) -> None:
    """
    1. Compute per-service health from span error rates.
    2. Propagate: if a service is "down", its callers become "degraded".
    """
    # Get span stats per service
    span_result = await db.execute(
        select(
            Span.service,
            func.count(Span.id).label("total"),
            func.sum(
                func.cast(Span.status == "error", Integer) +
                func.cast(Span.status_code >= 500, Integer)  # type: ignore[operator]
            ).label("errors"),
        )
        .where(Span.tenant_id == tenant_id, Span.started_at >= since, Span.parent_span_id.is_(None))
        .group_by(Span.service)
    )
    service_health: dict[str, str] = {}
    for row in span_result:
        if not row.total:
            continue
        err_rate = (row.errors or 0) / row.total
        if err_rate > 0.30:
            service_health[row.service] = "down"
        elif err_rate > 0.10:
            service_health[row.service] = "degraded"
        else:
            service_health[row.service] = "healthy"

    if not service_health:
        return

    # Fetch all service nodes for this tenant
    node_result = await db.execute(
        select(Node).where(
            Node.tenant_id == tenant_id,
            Node.kind == "service",
            Node.deleted_at.is_(None),
        )
    )
    nodes_by_name: dict[str, Node] = {n.name: n for n in node_result.scalars().all()}
    nodes_by_id: dict[str, Node] = {n.id: n for n in nodes_by_name.values()}

    # Apply direct health from span data
    for name, health in service_health.items():
        if name in nodes_by_name:
            nodes_by_name[name].status = health

    # Fetch edges for propagation (target = dependency being called)
    if nodes_by_id:
        edge_result = await db.execute(
            select(Edge).where(
                Edge.tenant_id == tenant_id,
                Edge.kind.in_(["calls", "dependency"]),
                Edge.source_id.in_(nodes_by_id.keys()),
                Edge.target_id.in_(nodes_by_id.keys()),
            )
        )
        edges = edge_result.scalars().all()

        # Build caller→callees map
        # If target (callee) is down, caller (source) becomes at least degraded
        down_targets = {
            n.id for n in nodes_by_id.values() if n.status == "down"
        }
        for edge in edges:
            if edge.target_id in down_targets:
                caller = nodes_by_id.get(edge.source_id)
                if caller and caller.status not in ("down",):
                    caller.status = "degraded"

    await db.commit()
