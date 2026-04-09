"""
Request flow reconstruction.

Rebuilds the actual path a request takes through infrastructure
(Cloudflare → proxy → nginx → app → DB) from log signals.

Sources (in priority / confidence order):
  1. request_id correlation (0.95) — exact chain from matching IDs across nodes
  2. nginx/haproxy upstream_addr (0.90) — direct: load_balancer → backend
  3. X-Forwarded-For chain (0.75) — cloudflare/proxy IP chain in nginx logs
  4. HAProxy frontend→backend (0.85) — LB routing recorded in haproxy logs
  5. CF-Ray header presence (0.70) — confirms Cloudflare is entry point
"""
import re
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.event import LogEvent
from app.models.topology import Node

_IP_RE = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')


async def reconstruct_flows(tenant_id: str, db: AsyncSession) -> list[dict]:
    """
    Returns list of flow chains sorted by observation count.
    Each chain has hops (ordered list of nodes), count, confidence, source.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=6)

    # Build IP → node name map
    node_r = await db.execute(
        select(Node).where(Node.tenant_id == tenant_id, Node.deleted_at.is_(None))
    )
    nodes = node_r.scalars().all()
    ip_to_name: dict[str, str] = {}
    for n in nodes:
        ip = (n.metadata_ or {}).get("ip_address")
        if ip:
            ip_to_name[ip] = n.external_id

    all_flows: list[dict] = []

    all_flows.extend(await _flows_from_request_ids(tenant_id, since, db))
    all_flows.extend(await _flows_from_upstream_addr(tenant_id, since, db, ip_to_name))
    all_flows.extend(await _flows_from_haproxy(tenant_id, since, db, ip_to_name))
    all_flows.extend(await _flows_from_xff(tenant_id, since, db, ip_to_name))
    all_flows.extend(await _flows_from_cf_ray(tenant_id, since, db, ip_to_name))

    # Merge duplicate chains (same hop sequence) — keep highest confidence, sum counts
    merged: dict[str, dict] = {}
    for f in all_flows:
        key = "→".join(h["node"] for h in f["hops"])
        if key in merged:
            merged[key]["count"] += f["count"]
            merged[key]["confidence"] = max(merged[key]["confidence"], f["confidence"])
            # Combine sources
            existing_sources = merged[key].get("sources", [merged[key]["source"]])
            if f["source"] not in existing_sources:
                existing_sources.append(f["source"])
            merged[key]["sources"] = existing_sources
        else:
            f["sources"] = [f["source"]]
            merged[key] = f

    return sorted(merged.values(), key=lambda x: x["count"], reverse=True)[:25]


async def _flows_from_request_ids(tenant_id: str, since: datetime, db: AsyncSession) -> list[dict]:
    """Group log events by request_id, reconstruct ordered node chains."""
    log_r = await db.execute(
        select(LogEvent.request_id, LogEvent.node_name, LogEvent.event_ts,
               LogEvent.response_time_ms, LogEvent.source)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.request_id.isnot(None),
            LogEvent.node_name.isnot(None),
        )
        .order_by(LogEvent.request_id, LogEvent.event_ts)
        .limit(10000)
    )
    rows = log_r.all()

    by_req: dict[str, list] = defaultdict(list)
    for r in rows:
        by_req[r.request_id].append(r)

    chain_counts: Counter = Counter()
    chain_timings: dict[tuple, list] = defaultdict(list)

    for req_id, events in by_req.items():
        if len(events) < 2:
            continue
        # Build ordered unique node sequence
        hops: list[dict] = []
        for ev in events:
            if not hops or hops[-1]["node"] != ev.node_name:
                hops.append({"node": ev.node_name, "rt": ev.response_time_ms or 0})
        if len(hops) >= 2:
            key = tuple(h["node"] for h in hops)
            chain_counts[key] += 1
            chain_timings[key].append([h["rt"] for h in hops])

    flows = []
    for chain, count in chain_counts.most_common(15):
        all_timings = chain_timings[chain]
        avg_timing = [
            round(sum(t[i] for t in all_timings if i < len(t)) / max(len(all_timings), 1), 1)
            for i in range(len(chain))
        ]
        flows.append({
            "hops": [{"node": node, "avg_ms": avg_timing[i] if i < len(avg_timing) else 0}
                     for i, node in enumerate(chain)],
            "count": count,
            "confidence": 0.95,
            "source": "request_id",
        })
    return flows


async def _flows_from_upstream_addr(
    tenant_id: str, since: datetime, db: AsyncSession, ip_to_name: dict
) -> list[dict]:
    """
    nginx/apache/traefik/envoy/caddy/gunicorn upstream_addr field.
    Source node → upstream_addr = a directly observed routing decision.
    """
    log_r = await db.execute(
        select(LogEvent.node_name, LogEvent.upstream_addr, LogEvent.response_time_ms,
               LogEvent.source, LogEvent.parsed)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.upstream_addr.isnot(None),
            LogEvent.node_name.isnot(None),
        )
        .limit(5000)
    )
    rows = log_r.all()

    pair_counts: Counter = Counter()
    pair_timings: dict = defaultdict(list)

    for r in rows:
        # upstream_addr may be "ip:port" or "hostname:port" or just IP
        upstream_raw = r.upstream_addr.split(",")[0].strip()  # handle multiple upstreams
        upstream_ip = upstream_raw.rsplit(":", 1)[0].strip() if ":" in upstream_raw else upstream_raw
        upstream_name = ip_to_name.get(upstream_ip, upstream_ip)

        if r.node_name and upstream_name and r.node_name != upstream_name:
            pair = (r.node_name, upstream_name)
            pair_counts[pair] += 1
            if r.response_time_ms:
                pair_timings[pair].append(r.response_time_ms)

    flows = []
    for (src, dst), count in pair_counts.most_common(15):
        tl = pair_timings.get((src, dst), [])
        avg_ms = round(sum(tl) / len(tl), 1) if tl else 0
        flows.append({
            "hops": [{"node": src, "avg_ms": 0}, {"node": dst, "avg_ms": avg_ms}],
            "count": count,
            "confidence": 0.90,
            "source": "upstream_addr",
        })
    return flows


async def _flows_from_haproxy(
    tenant_id: str, since: datetime, db: AsyncSession, ip_to_name: dict
) -> list[dict]:
    """
    HAProxy logs contain frontend → backend/server, which gives us LB routing.
    parsed.frontend → parsed.backend/parsed.server = flow hop.
    """
    log_r = await db.execute(
        select(LogEvent.node_name, LogEvent.parsed, LogEvent.response_time_ms)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.source == "haproxy",
        )
        .limit(3000)
    )
    rows = log_r.all()

    pair_counts: Counter = Counter()
    pair_timings: dict = defaultdict(list)

    for r in rows:
        parsed = r.parsed or {}
        backend = parsed.get("backend") or parsed.get("server")
        if backend and r.node_name and backend != r.node_name:
            pair = (r.node_name, backend)
            pair_counts[pair] += 1
            if r.response_time_ms:
                pair_timings[pair].append(r.response_time_ms)

    flows = []
    for (src, dst), count in pair_counts.most_common(10):
        tl = pair_timings.get((src, dst), [])
        avg_ms = round(sum(tl) / len(tl), 1) if tl else 0
        flows.append({
            "hops": [{"node": src, "avg_ms": 0}, {"node": dst, "avg_ms": avg_ms}],
            "count": count,
            "confidence": 0.85,
            "source": "haproxy_routing",
        })
    return flows


async def _flows_from_xff(
    tenant_id: str, since: datetime, db: AsyncSession, ip_to_name: dict
) -> list[dict]:
    """
    X-Forwarded-For header tells us the upstream chain.
    XFF = "client_ip, proxy1, proxy2" means: client→proxy1→proxy2→(this node).
    """
    log_r = await db.execute(
        select(LogEvent.node_name, LogEvent.parsed)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.node_name.isnot(None),
        )
        .limit(3000)
    )
    rows = log_r.all()

    chain_counts: Counter = Counter()

    for r in rows:
        parsed = r.parsed or {}
        xff = parsed.get("x_forwarded_for", "")
        if not xff or xff in ("-", ""):
            continue
        ips = [ip.strip() for ip in xff.split(",") if ip.strip() and ip.strip() != "-"]
        if not ips:
            continue

        chain: list[str] = []
        for ip in ips:
            name = ip_to_name.get(ip, ip)
            if not chain or chain[-1] != name:
                chain.append(name)
        if r.node_name not in chain:
            chain.append(r.node_name)
        if len(chain) >= 2:
            chain_counts[tuple(chain)] += 1

    flows = []
    for chain, count in chain_counts.most_common(10):
        flows.append({
            "hops": [{"node": n, "avg_ms": 0} for n in chain],
            "count": count,
            "confidence": 0.75,
            "source": "x_forwarded_for",
        })
    return flows


async def _flows_from_cf_ray(
    tenant_id: str, since: datetime, db: AsyncSession, ip_to_name: dict
) -> list[dict]:
    """
    If a node's logs contain CF-Ray, Cloudflare is the entry point.
    Build: [cloudflare] → [this_node] chains.
    """
    log_r = await db.execute(
        select(LogEvent.node_name, LogEvent.parsed, LogEvent.response_time_ms)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= since,
            LogEvent.node_name.isnot(None),
        )
        .limit(2000)
    )
    rows = log_r.all()

    node_counts: Counter = Counter()
    node_timings: dict = defaultdict(list)

    for r in rows:
        parsed = r.parsed or {}
        if parsed.get("cf_ray") and r.node_name:
            node_counts[r.node_name] += 1
            if r.response_time_ms:
                node_timings[r.node_name].append(r.response_time_ms)

    flows = []
    for node, count in node_counts.most_common(5):
        tl = node_timings.get(node, [])
        avg_ms = round(sum(tl) / len(tl), 1) if tl else 0
        flows.append({
            "hops": [{"node": "cloudflare", "avg_ms": 0}, {"node": node, "avg_ms": avg_ms}],
            "count": count,
            "confidence": 0.70,
            "source": "cf_ray",
        })
    return flows
