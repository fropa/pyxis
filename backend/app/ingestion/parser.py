"""
Raw log line → structured fields.

Supports all popular service log formats. Every parser returns a dict with
at minimum a 'message' key. Flow-signal fields (client_ip, request_id,
upstream_addr, response_time_ms, etc.) are extracted wherever possible.

A generic signal extractor runs as a final pass on EVERY log type so that
even unknown formats contribute IPs, UUIDs and timing to flow tracing.
"""
import json
import re
from typing import Any


# ── Generic signal extractors (run on ALL log types) ─────────────────────────

_GEN_IP_RE       = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')
_GEN_UUID_RE     = re.compile(r'\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b', re.I)
_GEN_REQID_RE    = re.compile(
    r'(?:request[_-]?id|req[_-]?id|x[_-]request[_-]id|traceid|trace[_-]?id'
    r'|x[_-]trace[_-]id|correlation[_-]?id|rid)\s*[=:\s"\']+([a-zA-Z0-9_\-]{8,})',
    re.I,
)
_GEN_UPSTREAM_RE = re.compile(
    r'(?:upstream|backend|proxy[_-]?to|forward[_-]?to|connecting[_-]?to|server)'
    r'\s*[=:\s"\']+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?|[a-zA-Z0-9._-]+:\d+)',
    re.I,
)
_GEN_MS_RE       = re.compile(r'(?:^|\s|[=:])(\d+(?:\.\d+)?)\s*ms\b', re.I)
_GEN_SEC_TIMING  = re.compile(r'(?:duration|took|elapsed|time|latency)\s*[=:\s]+(\d+(?:\.\d+)?)\s*s\b', re.I)
_GEN_STATUS_RE   = re.compile(r'\b([1-5]\d{2})\b')


def _extract_generic_signals(raw: str, result: dict) -> dict:
    """Overlay generic signal extraction onto any parsed result."""
    if not result.get("client_ip"):
        ips = _GEN_IP_RE.findall(raw)
        # Skip localhost/docker addresses
        public = [ip for ip in ips if not ip.startswith(("127.", "0.", "172.1", "::1"))]
        if public:
            result.setdefault("client_ip", public[0])

    if not result.get("request_id"):
        m = _GEN_UUID_RE.search(raw) or _GEN_REQID_RE.search(raw)
        if m:
            result.setdefault("request_id", m.group(1) if _GEN_REQID_RE.search(raw) else m.group(0))

    if not result.get("upstream_addr"):
        m = _GEN_UPSTREAM_RE.search(raw)
        if m:
            result.setdefault("upstream_addr", m.group(1))

    if not result.get("response_time_ms"):
        m = _GEN_MS_RE.search(raw)
        if m:
            try:
                result.setdefault("response_time_ms", float(m.group(1)))
            except ValueError:
                pass
        if not result.get("response_time_ms"):
            m = _GEN_SEC_TIMING.search(raw)
            if m:
                try:
                    result.setdefault("response_time_ms", float(m.group(1)) * 1000)
                except ValueError:
                    pass

    if not result.get("status_code"):
        m = _GEN_STATUS_RE.search(raw)
        if m:
            result.setdefault("status_code", int(m.group(1)))

    return result


def _parse_float_time(val: str | None) -> float | None:
    """Parse time value that could be '-', float seconds, or 'X.XXX'."""
    if not val or val in ("-", ""):
        return None
    try:
        return float(val) * 1000.0  # seconds → ms
    except ValueError:
        return None


# ── Nginx / Apache combined access log ───────────────────────────────────────
# These two use the same Combined Log Format so one parser handles both.

# Extended pyxis format (with upstream info appended):
# remote_addr - user [time] "request" status bytes "referer" "ua" "xff" upstream=X upstream_status=X rt=X upsrt=X reqid=X cfray=X
_NGINX_EXT_RE = re.compile(
    r'(?P<remote_addr>\S+)\s+-\s+\S+\s+'
    r'\[[^\]]+\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<body_bytes>\d+|-)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)"\s+'
    r'"(?P<xff>[^"]*)"\s+'
    r'upstream=(?P<upstream_addr>\S+)\s+'
    r'upstream_status=(?P<upstream_status>\S+)\s+'
    r'rt=(?P<request_time>\S+)\s+'
    r'upsrt=(?P<upsrt>\S+)'
    r'(?:\s+reqid=(?P<request_id>\S+))?'
    r'(?:\s+cfray=(?P<cf_ray>\S+))?',
    re.I,
)

# Standard combined format (nginx / apache):
# remote_addr - user [time] "request" status bytes "referer" "ua" ["xff"]
_COMBINED_RE = re.compile(
    r'(?P<remote_addr>\S+)\s+-\s+\S+\s+'
    r'\[[^\]]+\]\s+'
    r'"(?P<request>[^"]*?)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<body_bytes>\d+|-)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)"'
    r'(?:\s+"(?P<xff>[^"]*)")?',
    re.I,
)

_NGINX_DETECT_RE = re.compile(
    r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\s+-\s+\S+\s+\[[^\]]+\]\s+"[^"]*"\s+\d{3}\s+\d+'
)


def _parse_combined_access(raw: str, source: str) -> dict[str, Any]:
    m = _NGINX_EXT_RE.match(raw) or _COMBINED_RE.match(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})

    g = m.groupdict()
    request = g.get("request", "")
    method = path = None
    if request:
        parts = request.split(" ", 2)
        if len(parts) >= 2:
            method, path = parts[0], parts[1]

    status = g.get("status")
    body_bytes = g.get("body_bytes")

    result: dict[str, Any] = {
        "message": f'{g.get("remote_addr","?")} "{request}" {g.get("status","?")}',
        "client_ip":    g.get("remote_addr"),
        "method":       method,
        "path":         path,
        "status_code":  int(status) if status and status.isdigit() else None,
        "bytes_sent":   int(body_bytes) if body_bytes and body_bytes.isdigit() else None,
        "user_agent":   g.get("user_agent"),
        "x_forwarded_for": g.get("xff") if g.get("xff") not in (None, "-", "") else None,
        "upstream_addr": g.get("upstream_addr") if g.get("upstream_addr") not in (None, "-") else None,
        "upstream_status": g.get("upstream_status") if g.get("upstream_status") not in (None, "-") else None,
        "upstream_response_time_ms": _parse_float_time(g.get("upsrt")),
        "request_time_ms": _parse_float_time(g.get("request_time")),
        "response_time_ms": _parse_float_time(g.get("request_time")),
        "request_id":   g.get("request_id") if g.get("request_id") not in (None, "-") else None,
        "cf_ray":       g.get("cf_ray") if g.get("cf_ray") not in (None, "-") else None,
    }
    return _extract_generic_signals(raw, result)


parse_nginx_access  = lambda raw: _parse_combined_access(raw, "nginx")
parse_apache_access = lambda raw: _parse_combined_access(raw, "apache")


# ── Nginx/Apache error log ────────────────────────────────────────────────────

_NGINX_ERR_RE = re.compile(
    r'(?P<year>\d{4})/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+'
    r'\[(?P<level>\w+)\]\s+\d+#\d+:\s+(?:\*\d+\s+)?(?P<msg>.*)'
)

def parse_nginx_error(raw: str) -> dict[str, Any]:
    m = _NGINX_ERR_RE.match(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    result = {"message": g["msg"], "level": g.get("level", "error")}
    ip_m = re.search(r'client:\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', g["msg"])
    if ip_m:
        result["client_ip"] = ip_m.group(1)
    return _extract_generic_signals(raw, result)


# ── HAProxy HTTP/TCP log ──────────────────────────────────────────────────────
# option httplog format:
# client_ip:port [date] frontend backend/server TR/Tw/Tc/Tr/Ta status bytes - - ---- conns "request"

_HAPROXY_HTTP_RE = re.compile(
    r'(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(?P<client_port>\d+)\s+'
    r'\[[^\]]+\]\s+'
    r'(?P<frontend>\S+)\s+'
    r'(?P<backend>[^/\s]+)/(?P<server>\S+)\s+'
    r'(?P<timers>-?\d+/-?\d+/-?\d+/-?\d+/-?\d+)\s+'
    r'(?P<status_code>\d{3}|-)\s+'
    r'(?P<bytes_sent>\d+)\s+'
    r'\S+\s+\S+\s+'
    r'(?P<termination>\S+)\s+'
    r'[\d/]+\s+[\d/]+\s+'
    r'"(?P<request>[^"]*)"'
)

def parse_haproxy(raw: str) -> dict[str, Any]:
    m = _HAPROXY_HTTP_RE.search(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    timers = g.get("timers", "").split("/")
    total_ms = None
    if len(timers) == 5:
        try:
            total_ms = float(timers[4])
        except (ValueError, IndexError):
            pass
    request = g.get("request", "")
    path = request.split(" ", 2)[1] if len(request.split(" ")) >= 2 else None
    status = g.get("status_code")
    result = {
        "message": f'{g.get("client_ip","?")} -> {g.get("frontend","?")} -> {g.get("backend","?")}/{g.get("server","?")} {g.get("status_code","?")}',
        "client_ip":        g.get("client_ip"),
        "frontend":         g.get("frontend"),
        "backend":          g.get("backend"),
        "upstream_addr":    g.get("server"),   # server = upstream for flow tracing
        "status_code":      int(status) if status and status.isdigit() else None,
        "bytes_sent":       int(g.get("bytes_sent", 0)) if g.get("bytes_sent", "").isdigit() else None,
        "response_time_ms": total_ms,
        "termination_state": g.get("termination"),
        "path":             path,
    }
    return _extract_generic_signals(raw, result)


# ── PostgreSQL ────────────────────────────────────────────────────────────────
# log_line_prefix = '%m [%p] %d %r %a %u '

_PG_RE = re.compile(
    r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\s+\w+)?\s+'
    r'\[(?P<pid>\d+)\]\s+'
    r'(?P<database>\S+)\s+'
    r'(?:(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(?P<client_port>\d+)|\[local\])\s+'
    r'(?P<app>\S+)\s+'
    r'(?P<user>\S+)\s+'
    r'(?P<severity>\w+):\s+'
    r'(?P<msg>.*)'
)
_PG_DURATION_RE = re.compile(r'duration:\s*([\d.]+)\s*ms')
_PG_STMT_RE     = re.compile(r'statement:\s*(\w+)', re.I)
_PG_SLOW_RE     = re.compile(r'slow\s+query.*duration:\s*([\d.]+)\s*ms', re.I)

def parse_postgres(raw: str) -> dict[str, Any]:
    m = _PG_RE.match(raw)
    dur_m = _PG_DURATION_RE.search(raw)
    duration_ms = float(dur_m.group(1)) if dur_m else None
    if not m:
        return _extract_generic_signals(raw, {"message": raw, "duration_ms": duration_ms, "response_time_ms": duration_ms})
    g = m.groupdict()
    msg = g.get("msg", raw)
    stmt_m = _PG_STMT_RE.search(msg)
    result = {
        "message": msg,
        "client_ip": g.get("client_ip"),
        "database": g.get("database"),
        "user": g.get("user"),
        "application_name": g.get("app"),
        "pid": g.get("pid"),
        "severity": g.get("severity"),
        "duration_ms": duration_ms,
        "response_time_ms": duration_ms,
        "query_type": stmt_m.group(1).upper() if stmt_m else None,
    }
    return _extract_generic_signals(raw, result)


# ── MySQL slow query / general log ────────────────────────────────────────────
# Slow query log: # Time: ... # User@Host: user[user] @ host [ip] # Query_time: X Lock_time: Y ...

_MYSQL_SLOW_RE = re.compile(
    r'#\s+User@Host:\s+\S+\s+@\s+\S+\s+\[(?P<client_ip>[^\]]*)\]'
    r'.*?#\s+Query_time:\s+(?P<query_time>[\d.]+)\s+Lock_time:\s+(?P<lock_time>[\d.]+)'
    r'.*?#\s+Rows_sent:\s+(?P<rows_sent>\d+)\s+Rows_examined:\s+(?P<rows_examined>\d+)',
    re.DOTALL | re.I,
)
_MYSQL_GENERAL_RE = re.compile(
    r'(?P<ts>\d{4}-\d{2}-\d{2}\w\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+'
    r'(?P<pid>\d+)\s+'
    r'(?P<cmd>\w+)\s+'
    r'(?P<query>.*)'
)
_MYSQL_CONNECT_RE = re.compile(r'Connect\s+\S+@(?P<host>\S+)', re.I)

def parse_mysql_slow(raw: str) -> dict[str, Any]:
    m = _MYSQL_SLOW_RE.search(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    qt = float(g["query_time"]) * 1000 if g.get("query_time") else None
    result = {
        "message": raw[:200],
        "client_ip": g.get("client_ip") or None,
        "response_time_ms": qt,
        "duration_ms": qt,
        "rows_sent": int(g.get("rows_sent", 0)),
        "rows_examined": int(g.get("rows_examined", 0)),
    }
    return _extract_generic_signals(raw, result)

def parse_mysql_general(raw: str) -> dict[str, Any]:
    result = {"message": raw}
    m = _MYSQL_CONNECT_RE.search(raw)
    if m:
        result["client_ip"] = m.group("host")
    return _extract_generic_signals(raw, result)


# ── MongoDB ───────────────────────────────────────────────────────────────────
# v4+ logs in JSON; older versions use a text format

def parse_mongodb(raw: str) -> dict[str, Any]:
    # Try JSON first (v4+)
    try:
        obj = json.loads(raw)
        attr = obj.get("attr", {})
        client = attr.get("remote", attr.get("client", ""))
        client_ip = None
        if client and ":" in str(client):
            client_ip = str(client).rsplit(":", 1)[0]
        dur = attr.get("durationMillis") or attr.get("duration", {}).get("ms")
        ns = attr.get("ns", "")
        cmd_type = list(attr.get("command", {}).keys())[0] if attr.get("command") else None
        result = {
            "message": obj.get("msg", raw[:200]),
            "severity": obj.get("s", "I"),
            "component": obj.get("c"),
            "client_ip": client_ip,
            "namespace": ns,
            "query_type": cmd_type,
            "duration_ms": float(dur) if dur is not None else None,
            "response_time_ms": float(dur) if dur is not None else None,
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        pass

    # Older text format: timestamp severity [component] message
    _MONGO_TEXT_RE = re.compile(
        r'\w{3}\s+\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+\s+'
        r'(?P<severity>[A-Z])\s+'
        r'(?P<component>\w+)\s+\[(?P<ctx>[^\]]+)\]\s+'
        r'(?P<msg>.*)'
    )
    m = _MONGO_TEXT_RE.match(raw)
    if m:
        g = m.groupdict()
        result = {"message": g["msg"], "component": g.get("component")}
        dur_m = re.search(r'(\d+)ms$', g["msg"])
        if dur_m:
            result["response_time_ms"] = float(dur_m.group(1))
        return _extract_generic_signals(raw, result)

    return _extract_generic_signals(raw, {"message": raw})


# ── Redis ─────────────────────────────────────────────────────────────────────
# Format: pid:role timestamp loglevel message
# Example: 1234:M 01 Jan 2024 00:00:00.000 * Background saving started

_REDIS_RE = re.compile(
    r'(?P<pid>\d+):(?P<role>[MSCX])\s+'
    r'\d+\s+\w+\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+\s+'
    r'(?P<level>[*#\-+!])\s+'
    r'(?P<msg>.*)'
)
_REDIS_LEVEL = {"*": "info", "#": "warning", "-": "info", "+": "debug", "!": "error"}

def parse_redis(raw: str) -> dict[str, Any]:
    m = _REDIS_RE.match(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    result = {
        "message": g["msg"],
        "pid": g.get("pid"),
        "role": g.get("role"),
        "level": _REDIS_LEVEL.get(g.get("level", "*"), "info"),
    }
    # Extract slow log patterns
    dur_m = re.search(r'(?:took|duration|time)\s+(\d+)\s*(?:ms|microseconds?)', g["msg"], re.I)
    if dur_m:
        val = float(dur_m.group(1))
        # Redis slow log reports in microseconds
        if "microseconds" in dur_m.group(0).lower():
            val /= 1000
        result["response_time_ms"] = val
    return _extract_generic_signals(raw, result)


# ── Elasticsearch / OpenSearch slow log ──────────────────────────────────────

_ES_SLOW_RE = re.compile(
    r'\[(?P<ts>[^\]]+)\]\[(?P<level>[A-Z]+)\s*\]\[(?P<component>[^\]]+)\]\s+'
    r'\[(?P<node>[^\]]+)\]\s+\[(?P<index>[^\]]+)\]\s+'
    r'took\[(?P<took>[^\]]+)\].*'
    r'search_type\[(?P<search_type>[^\]]+)\]',
    re.I,
)
_ES_TOOK_RE = re.compile(r'took\[([^\]]+)\]')
_ES_MS_RE   = re.compile(r'([\d.]+)(?:ms|s|micros)')

def parse_elasticsearch(raw: str) -> dict[str, Any]:
    # Try JSON first (ES 7+)
    try:
        obj = json.loads(raw)
        took = obj.get("took") or obj.get("took_millis")
        result = {
            "message": obj.get("message", raw[:200]),
            "level": obj.get("level", "INFO"),
            "component": obj.get("logger_name") or obj.get("component"),
            "response_time_ms": float(took) if took else None,
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        pass

    m = _ES_SLOW_RE.search(raw)
    if m:
        g = m.groupdict()
        dur_m = _ES_MS_RE.search(g.get("took", ""))
        dur_ms = None
        if dur_m:
            val = float(dur_m.group(1))
            unit = g["took"].rstrip("0123456789.").lower()
            if "micro" in unit:
                val /= 1000
            elif not ("ms" in unit or "milli" in unit):
                val *= 1000  # seconds
            dur_ms = val
        return _extract_generic_signals(raw, {
            "message": raw,
            "level": g.get("level"),
            "index": g.get("index"),
            "response_time_ms": dur_ms,
        })

    return _extract_generic_signals(raw, {"message": raw})


# ── RabbitMQ ──────────────────────────────────────────────────────────────────
# Format: =INFO REPORT==== DD-Mon-YYYY::HH:MM:SS === message

_RABBITMQ_RE = re.compile(
    r'=(?P<level>\w+)\s+REPORT====\s+[^=]+=+\s*\n?(?P<msg>.*)',
    re.DOTALL,
)
_RABBITMQ_CONN_RE = re.compile(
    r'connection\s+(?P<conn><[^>]+>).*?(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})',
    re.I,
)

def parse_rabbitmq(raw: str) -> dict[str, Any]:
    # Try JSON (structured logging mode)
    try:
        obj = json.loads(raw)
        result = {
            "message": obj.get("msg", str(obj.get("message", raw[:200]))),
            "level": obj.get("level", "info"),
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        pass

    m = _RABBITMQ_RE.search(raw)
    if m:
        g = m.groupdict()
        msg = g.get("msg", raw).strip()
        result: dict[str, Any] = {
            "message": msg[:300],
            "level": g.get("level", "INFO").lower(),
        }
        conn_m = _RABBITMQ_CONN_RE.search(msg)
        if conn_m:
            result["client_ip"] = conn_m.group("client_ip")
        return _extract_generic_signals(raw, result)

    return _extract_generic_signals(raw, {"message": raw})


# ── Kafka ─────────────────────────────────────────────────────────────────────
# Format: [timestamp] [level] component - message

_KAFKA_RE = re.compile(
    r'\[(?P<ts>[^\]]+)\]\s+'
    r'(?P<level>INFO|WARN|ERROR|DEBUG|TRACE|FATAL)\s+'
    r'(?P<component>\S+)\s+-\s+'
    r'(?P<msg>.*)'
)

def parse_kafka(raw: str) -> dict[str, Any]:
    # Try JSON structured logging
    try:
        obj = json.loads(raw)
        result = {
            "message": obj.get("message", raw[:200]),
            "level": obj.get("level", "INFO").lower(),
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        pass

    m = _KAFKA_RE.match(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    return _extract_generic_signals(raw, {
        "message": g["msg"],
        "level": g.get("level", "INFO").lower(),
        "component": g.get("component"),
    })


# ── Memcached ─────────────────────────────────────────────────────────────────

def parse_memcached(raw: str) -> dict[str, Any]:
    result = {"message": raw}
    # Memcached verbose output: "<pid> new client connection"
    conn_m = re.search(r'(?:connection|client)\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', raw, re.I)
    if conn_m:
        result["client_ip"] = conn_m.group(1)
    return _extract_generic_signals(raw, result)


# ── Traefik (JSON by default) ─────────────────────────────────────────────────

def parse_traefik(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
        client_ip = obj.get("ClientHost") or obj.get("client_ip")
        upstream   = obj.get("upstream_addr") or obj.get("UpstreamAddr")
        rt = obj.get("Duration") or obj.get("duration") or obj.get("request_duration")
        rt_ms = None
        if rt is not None:
            # Traefik logs duration as nanoseconds (int) or seconds (float)
            try:
                rt_f = float(rt)
                rt_ms = rt_f / 1_000_000 if rt_f > 10_000 else rt_f * 1000
            except (ValueError, TypeError):
                pass
        result = {
            "message": (f'{client_ip or "?"} "{obj.get("RequestMethod","?")} '
                       f'{obj.get("RequestPath","?")}" {obj.get("DownstreamStatus","?")}'),
            "client_ip": client_ip,
            "method": obj.get("RequestMethod"),
            "path": obj.get("RequestPath"),
            "status_code": obj.get("DownstreamStatus") or obj.get("status"),
            "upstream_addr": upstream,
            "response_time_ms": rt_ms,
            "request_id": obj.get("request_id") or obj.get("RequestId"),
            "x_forwarded_for": obj.get("ClientAddr"),
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        return _extract_generic_signals(raw, {"message": raw})


# ── Envoy proxy (JSON access log) ────────────────────────────────────────────

def parse_envoy(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
        dur = obj.get("duration") or obj.get("response_duration")
        result = {
            "message": (f'{obj.get("downstream_remote_address","?")} '
                       f'"{obj.get("method","?")} {obj.get("path","?")}" '
                       f'{obj.get("response_code","?")}'),
            "client_ip":    obj.get("downstream_remote_address", "").split(":")[0] or None,
            "method":       obj.get("method"),
            "path":         obj.get("path"),
            "status_code":  obj.get("response_code"),
            "upstream_addr": obj.get("upstream_host") or obj.get("upstream_cluster"),
            "response_time_ms": float(dur) if dur is not None else None,
            "request_id":   obj.get("x_request_id"),
            "x_forwarded_for": obj.get("x_forwarded_for"),
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError):
        return _extract_generic_signals(raw, {"message": raw})


# ── Caddy (JSON access log) ───────────────────────────────────────────────────

def parse_caddy(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
        req = obj.get("request", {})
        resp = obj.get("resp_headers", {})
        dur = obj.get("duration")
        result = {
            "message": (f'{req.get("remote_addr","?")} '
                       f'"{req.get("method","?")} {req.get("uri","?")}" '
                       f'{obj.get("status","?")}'),
            "client_ip":    req.get("remote_addr", "").split(":")[0] or None,
            "method":       req.get("method"),
            "path":         req.get("uri"),
            "status_code":  obj.get("status"),
            "upstream_addr": obj.get("upstream_addr"),
            "response_time_ms": float(dur) * 1000 if dur else None,
            "request_id":   req.get("headers", {}).get("X-Request-Id", [None])[0],
            "x_forwarded_for": (req.get("headers", {}).get("X-Forwarded-For") or [""])[0] or None,
        }
        return _extract_generic_signals(raw, result)
    except (json.JSONDecodeError, KeyError, TypeError):
        return _extract_generic_signals(raw, {"message": raw})


# ── Varnish VSL log ───────────────────────────────────────────────────────────
# varnishlog output: "tag value" lines, often piped through varnishncsa for combined format

_VARNISH_NCSA_RE = re.compile(
    r'(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+-\s+\S+\s+'
    r'\[[^\]]+\]\s+"(?P<request>[^"]*)"\s+(?P<status>\d{3})\s+(?P<bytes>\d+)'
    r'(?:\s+(?P<ttfb>[\d.]+)\s+(?P<total_time>[\d.]+))?'
)

def parse_varnish(raw: str) -> dict[str, Any]:
    m = _VARNISH_NCSA_RE.match(raw)
    if m:
        g = m.groupdict()
        request = g.get("request", "")
        parts = request.split(" ", 2)
        result = {
            "message": raw,
            "client_ip": g.get("client_ip"),
            "method": parts[0] if len(parts) >= 1 else None,
            "path": parts[1] if len(parts) >= 2 else None,
            "status_code": int(g["status"]) if g.get("status") else None,
            "bytes_sent": int(g["bytes"]) if g.get("bytes") else None,
            "response_time_ms": float(g["total_time"]) * 1000 if g.get("total_time") else None,
        }
        return _extract_generic_signals(raw, result)
    return _extract_generic_signals(raw, {"message": raw})


# ── PHP-FPM ───────────────────────────────────────────────────────────────────
# access.log format: %R - %u %t "%m %r" %s
# Default pool log: [date] pool_name access.log line

_PHP_FPM_ACCESS_RE = re.compile(
    r'(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|-)\s+-\s+\S+\s+'
    r'\[[^\]]+\]\s+"(?P<method>\w+)\s+(?P<path>\S+)[^"]*"\s+(?P<status>\d{3})\s+'
    r'(?P<duration>\d+)/?'
)
_PHP_FPM_SLOW_RE = re.compile(r'request_slowlog_timeout.*?(?:script|file):\s+(?P<script>\S+)', re.I)

def parse_php_fpm(raw: str) -> dict[str, Any]:
    m = _PHP_FPM_ACCESS_RE.match(raw)
    if m:
        g = m.groupdict()
        status = g.get("status")
        dur = g.get("duration")
        result = {
            "message": raw,
            "client_ip": g.get("client_ip") if g.get("client_ip") != "-" else None,
            "method": g.get("method"),
            "path": g.get("path"),
            "status_code": int(status) if status and status.isdigit() else None,
            "response_time_ms": float(dur) / 1000 if dur else None,  # php-fpm logs in μs
        }
        return _extract_generic_signals(raw, result)
    return _extract_generic_signals(raw, {"message": raw})


# ── Gunicorn / uWSGI ──────────────────────────────────────────────────────────

_GUNICORN_RE = re.compile(
    r'(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|-)\s+-\s+-\s+'
    r'\[[^\]]+\]\s+"(?P<method>\w+)\s+(?P<path>\S+)[^"]*"\s+'
    r'(?P<status>\d{3})\s+\d+\s+(?P<duration>[\d.]+)'
)
_UWSGI_RE = re.compile(
    r'\[pid:\s*(?P<pid>\d+).*\]\s+'
    r'(?P<client_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|-)\s+\S+\s+\S+\s+'
    r'\[.*?\]\s+(?P<method>\w+)\s+(?P<path>\S+)\s+=>\s+generated.*?(?P<status>\d{3}).*?'
    r'in\s+(?P<duration>[\d.]+)\s*msecs'
)

def parse_gunicorn(raw: str) -> dict[str, Any]:
    m = _GUNICORN_RE.match(raw) or _UWSGI_RE.search(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    status = g.get("status")
    dur = g.get("duration")
    result = {
        "message": raw,
        "client_ip": g.get("client_ip") if g.get("client_ip") != "-" else None,
        "method": g.get("method"),
        "path": g.get("path"),
        "status_code": int(status) if status and status.isdigit() else None,
        "response_time_ms": float(dur) if dur else None,
    }
    return _extract_generic_signals(raw, result)

parse_uwsgi = parse_gunicorn


# ── Generic JSON (catch-all for structured logs) ─────────────────────────────

_JSON_FIELD_MAP = {
    # client IP
    "client_ip": ("remote_addr", "clientip", "client", "remote_ip", "src", "source_ip",
                  "RemoteAddr", "clientHost", "ip"),
    # request ID / trace ID
    "request_id": ("request_id", "req_id", "rid", "x_request_id", "requestId",
                   "trace_id", "traceId", "correlation_id", "correlationId"),
    # upstream
    "upstream_addr": ("upstream", "upstream_addr", "backend", "server", "proxy",
                      "upstreamAddr", "UpstreamAddr"),
    # timing
    "response_time_ms": ("duration", "duration_ms", "latency_ms", "elapsed_ms",
                         "response_time", "rt", "time_ms"),
    # HTTP
    "method": ("method", "http_method", "verb", "RequestMethod"),
    "path": ("path", "uri", "url", "request_uri", "RequestPath", "endpoint"),
    "status_code": ("status", "status_code", "http_status", "StatusCode", "code"),
}

def parse_generic_json(raw: str) -> dict[str, Any]:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _extract_generic_signals(raw, {"message": raw})

    result: dict[str, Any] = {}

    # Find message field
    for key in ("message", "msg", "log", "text", "event", "body"):
        if key in obj and isinstance(obj[key], str):
            result["message"] = obj[key]
            break
    if "message" not in result:
        result["message"] = raw[:300]

    # Map common field names
    for target, candidates in _JSON_FIELD_MAP.items():
        for cand in candidates:
            if cand in obj and obj[cand] is not None:
                val = obj[cand]
                if target in ("status_code",):
                    try:
                        val = int(val)
                    except (ValueError, TypeError):
                        val = None
                elif target == "response_time_ms":
                    try:
                        val = float(val)
                        # If value looks like seconds (< 100), convert
                        if 0 < val < 100:
                            val *= 1000
                    except (ValueError, TypeError):
                        val = None
                if val is not None:
                    result[target] = val
                    break

    # Extract level
    for key in ("level", "severity", "loglevel", "log_level", "lvl"):
        if key in obj:
            result["level"] = str(obj[key]).lower()
            break

    return _extract_generic_signals(raw, result)


# ── syslog (RFC 3164 / 5424) ──────────────────────────────────────────────────

_SYSLOG_RE = re.compile(
    r"(?P<month>\w{3})\s+(?P<day>\d+)\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<process>\S+?)(?:\[(?P<pid>\d+)\])?\s*:\s*(?P<msg>.*)"
)

def parse_syslog(raw: str) -> dict[str, Any]:
    m = _SYSLOG_RE.match(raw)
    if not m:
        return _extract_generic_signals(raw, {"message": raw})
    g = m.groupdict()
    result = {
        "host": g.get("host"),
        "process": g.get("process"),
        "pid": g.get("pid"),
        "message": g.get("msg", raw),
    }
    return _extract_generic_signals(g.get("msg", raw), result)


# ── Kubernetes event ──────────────────────────────────────────────────────────

def parse_k8s_event(raw: str, pre_parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": pre_parsed.get("involvedObject", {}).get("kind"),
        "name": pre_parsed.get("involvedObject", {}).get("name"),
        "namespace": pre_parsed.get("involvedObject", {}).get("namespace"),
        "reason": pre_parsed.get("reason"),
        "message": pre_parsed.get("message", raw),
        "type": pre_parsed.get("type"),
        "action": pre_parsed.get("action"),
    }


# ── CI/CD pipeline log ────────────────────────────────────────────────────────

_ANSI_ESC = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

def parse_pipeline_log(raw: str) -> dict[str, Any]:
    clean = _ANSI_ESC.sub("", raw).strip()
    level = "info"
    if any(w in clean.lower() for w in ("error", "fatal", "failed", "failure")):
        level = "error"
    elif "warning" in clean.lower() or "warn" in clean.lower():
        level = "warning"
    return _extract_generic_signals(clean, {"message": clean, "inferred_level": level})


# ── Dispatch ──────────────────────────────────────────────────────────────────

# Source tags → parser functions
_PARSERS = {
    "nginx_access":   lambda raw, _pp: parse_nginx_access(raw),
    "nginx_error":    lambda raw, _pp: parse_nginx_error(raw),
    "apache_access":  lambda raw, _pp: parse_apache_access(raw),
    "apache_error":   lambda raw, _pp: parse_nginx_error(raw),  # same format
    "haproxy":        lambda raw, _pp: parse_haproxy(raw),
    "postgres":       lambda raw, _pp: parse_postgres(raw),
    "mysql_slow":     lambda raw, _pp: parse_mysql_slow(raw),
    "mysql_general":  lambda raw, _pp: parse_mysql_general(raw),
    "mysql":          lambda raw, _pp: parse_mysql_general(raw),
    "mongodb":        lambda raw, _pp: parse_mongodb(raw),
    "redis":          lambda raw, _pp: parse_redis(raw),
    "elasticsearch":  lambda raw, _pp: parse_elasticsearch(raw),
    "opensearch":     lambda raw, _pp: parse_elasticsearch(raw),
    "rabbitmq":       lambda raw, _pp: parse_rabbitmq(raw),
    "kafka":          lambda raw, _pp: parse_kafka(raw),
    "memcached":      lambda raw, _pp: parse_memcached(raw),
    "traefik":        lambda raw, _pp: parse_traefik(raw),
    "envoy":          lambda raw, _pp: parse_envoy(raw),
    "caddy":          lambda raw, _pp: parse_caddy(raw),
    "varnish":        lambda raw, _pp: parse_varnish(raw),
    "php_fpm":        lambda raw, _pp: parse_php_fpm(raw),
    "gunicorn":       lambda raw, _pp: parse_gunicorn(raw),
    "uwsgi":          lambda raw, _pp: parse_uwsgi(raw),
    "syslog":         lambda raw, _pp: parse_syslog(raw),
    "auth_log":       lambda raw, _pp: parse_syslog(raw),
    "k8s_event":      lambda raw, pp: parse_k8s_event(raw, pp),
    "ci_pipeline":    lambda raw, _pp: parse_pipeline_log(raw),
}


def parse(source: str, raw: str, pre_parsed: dict[str, Any]) -> dict[str, Any]:
    # Direct dispatch
    fn = _PARSERS.get(source)
    if fn:
        return fn(raw, pre_parsed)

    # Auto-detect nginx combined format for app_log / unknown sources
    if _NGINX_DETECT_RE.match(raw):
        return _parse_combined_access(raw, "nginx")

    # Try JSON structured log
    if raw.lstrip().startswith("{"):
        return parse_generic_json(raw)

    # Fall through: generic signal extraction
    result = {"message": raw}
    if pre_parsed:
        result.update(pre_parsed)
    return _extract_generic_signals(raw, result)
