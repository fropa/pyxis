"""
Log verbosity analyzer.

Scores how much useful signal a node's logs contain for flow tracing and root
cause analysis, then produces exact configuration fixes for every detected
service type (nginx, haproxy, apache, traefik, envoy, caddy, varnish,
postgres, mysql, mongodb, redis, elasticsearch, rabbitmq, kafka, memcached,
php-fpm, gunicorn/uwsgi, generic app).

Score 0–100:
  +20 has IP addresses in logs
  +25 has request/trace IDs
  +20 has response timing
  +20 has upstream/backend info
  +10 has HTTP status codes
  +5  has Cloudflare signals (CF-Ray)
"""
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.event import LogEvent
from app.models.topology import Node

# ── Signal detectors ──────────────────────────────────────────────────────────

_IP_RE      = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_UUID_RE    = re.compile(r'\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b', re.I)
_REQID_RE   = re.compile(
    r'(?:request[_-]?id|req[_-]?id|x[_-]request[_-]id|traceid|trace[_-]?id'
    r'|correlation[_-]?id|rid|reqid)\s*[=:\s"\']+([a-zA-Z0-9_\-]{8,})', re.I)
_TIMING_RE  = re.compile(
    r'\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?)\b'
    r'|(?:duration|time|took|elapsed|latency|rt|upsrt)\s*[=:]\s*[\d.]+', re.I)
_UPSTREAM_RE = re.compile(
    r'(?:upstream|backend|server|proxy|forward|via)\s*[=:\s"\']+\S+', re.I)
_STATUS_RE  = re.compile(r'\b[1-5]\d{2}\b')
_CF_RE      = re.compile(r'cf.ray|cloudflare|cfray', re.I)


# ── Service detection ─────────────────────────────────────────────────────────

_SERVICE_SRC_MAP = {
    "nginx_access": "nginx", "nginx_error": "nginx",
    "apache_access": "apache", "apache_error": "apache",
    "haproxy": "haproxy",
    "traefik": "traefik",
    "envoy": "envoy",
    "caddy": "caddy",
    "varnish": "varnish",
    "postgres": "postgres",
    "mysql": "mysql", "mysql_slow": "mysql", "mysql_general": "mysql",
    "mongodb": "mongodb",
    "redis": "redis",
    "elasticsearch": "elasticsearch", "opensearch": "elasticsearch",
    "rabbitmq": "rabbitmq",
    "kafka": "kafka",
    "memcached": "memcached",
    "php_fpm": "php_fpm",
    "gunicorn": "gunicorn", "uwsgi": "gunicorn",
}

_SERVICE_CONTENT_PATTERNS = {
    "nginx":   re.compile(r'nginx|\bupstream\b|" \d{3} \d+\s+"', re.I),
    "apache":  re.compile(r'apache|httpd|mod_|AH\d{5}', re.I),
    "haproxy": re.compile(r'haproxy|frontend|backend.*ELOG|termination_state', re.I),
    "traefik": re.compile(r'traefik|RouterName|entryPoints', re.I),
    "envoy":   re.compile(r'envoy|downstream_remote|upstream_host|upstream_cluster', re.I),
    "caddy":   re.compile(r'caddy|RequestHost|ResponseSize', re.I),
    "varnish": re.compile(r'varnish|VCL|bereq|beresp', re.I),
    "postgres": re.compile(r'postgres|LOG:\s+duration|statement:|FATAL.*database', re.I),
    "mysql":   re.compile(r'mysql|innodb|Query_time|InnoDB', re.I),
    "mongodb": re.compile(r'mongod|QUERY|WRITE|COMMAND|ns:.*\.|planSummary', re.I),
    "redis":   re.compile(r'\d+:[MSCX]\s+\d+\s+\w+\s+\d{2}:\d{2}:\d{2}|AOF|RDB|SAVE', re.I),
    "elasticsearch": re.compile(r'elasticsearch|opensearch|took\[|msearch|\.kibana', re.I),
    "rabbitmq": re.compile(r'rabbitmq|amqp|vhost|channel|exchange|queue', re.I),
    "kafka":   re.compile(r'kafka|KafkaServer|Log partition|__consumer_offsets', re.I),
    "memcached": re.compile(r'memcached|slab|eviction|cas_misses', re.I),
    "php_fpm": re.compile(r'php-fpm|PHP|fpm.pool|NOTICE.*fpm', re.I),
    "gunicorn": re.compile(r'gunicorn|uvicorn|uwsgi|\[pid\s+\d+\].*\d{3}\s+\d+', re.I),
}


# ── Recommendations per service ───────────────────────────────────────────────

def _recs_for(service: str, dims: dict) -> list[dict]:
    recs = []

    if service == "nginx":
        if not dims["has_upstream"] or not dims["has_timing"] or not dims["has_request_ids"]:
            recs.append({
                "title": "Enable extended nginx log format with upstream & request ID",
                "priority": "high",
                "config": """\
# nginx.conf — in http {} block:
log_format pyxis '$remote_addr - [$time_local] "$request" $status '
                 '$body_bytes_sent "$http_referer" "$http_user_agent" '
                 '"$http_x_forwarded_for" '
                 'upstream=$upstream_addr upstream_status=$upstream_status '
                 'rt=$request_time upsrt=$upstream_response_time '
                 'reqid=$request_id cfray=$http_cf_ray';

# In server {} block:
access_log /var/log/nginx/access.log pyxis;""",
            })
        if not dims["has_request_ids"]:
            recs.append({
                "title": "Generate and propagate request IDs",
                "priority": "high",
                "config": """\
# nginx.conf — in http {} block:
map $http_x_request_id $req_id {
    default   $http_x_request_id;
    ""        $request_id;
}
# In server {} block:
add_header    X-Request-ID $req_id always;
proxy_set_header X-Request-ID $req_id;""",
            })

    elif service == "apache":
        if not dims["has_upstream"] or not dims["has_request_ids"] or not dims["has_timing"]:
            recs.append({
                "title": "Enable extended Apache access log with timing and proxy info",
                "priority": "high",
                "config": """\
# httpd.conf or apache2.conf:
LoadModule unique_id_module modules/mod_unique_id.so
LogFormat "%h %l %u %t \\"%r\\" %>s %O \\"%{Referer}i\\" \\"%{User-Agent}i\\" \\"%{X-Forwarded-For}i\\" %{UNIQUE_ID}e %D" pyxis
CustomLog /var/log/apache2/access.log pyxis
# %D = time to serve in microseconds; %{UNIQUE_ID}e = unique request ID""",
            })

    elif service == "haproxy":
        if not dims["has_timing"] or not dims["has_ips"] or not dims["has_request_ids"]:
            recs.append({
                "title": "Enable full HTTP logging with request ID capture",
                "priority": "high",
                "config": """\
# haproxy.cfg — in frontend block:
option httplog
log-format "%ci:%cp [%tr] %ft %b/%s %TR/%Tw/%Tc/%Tr/%Ta %ST %B %tsc %ac/%fc/%bc/%sc/%rc %{+Q}r"

# Capture and inject request ID:
http-request set-header X-Request-ID %[uuid()] unless { req.hdr(X-Request-ID) -m found }
http-request capture req.hdr(X-Request-ID) len 36
capture request header X-Request-ID len 36""",
            })

    elif service == "traefik":
        if not dims["has_request_ids"] or not dims["has_timing"]:
            recs.append({
                "title": "Enable Traefik access log with request ID",
                "priority": "medium",
                "config": """\
# traefik.yml static config:
accessLog:
  filePath: "/var/log/traefik/access.log"
  format: json
  fields:
    defaultMode: keep
    headers:
      defaultMode: keep
      names:
        X-Request-Id: keep
        X-Forwarded-For: keep""",
            })

    elif service == "envoy":
        if not dims["has_request_ids"]:
            recs.append({
                "title": "Enable Envoy access log with trace IDs",
                "priority": "medium",
                "config": """\
# envoy.yaml — in http_connection_manager:
access_log:
  - name: envoy.access_loggers.file
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog
      path: /dev/stdout
      log_format:
        json_format:
          start_time: "%START_TIME%"
          method: "%REQ(:METHOD)%"
          path: "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"
          response_code: "%RESPONSE_CODE%"
          duration: "%DURATION%"
          upstream_host: "%UPSTREAM_HOST%"
          x_request_id: "%REQ(X-REQUEST-ID)%"
          x_forwarded_for: "%REQ(X-FORWARDED-FOR)%\"""",
            })

    elif service == "caddy":
        if not dims["has_timing"] or not dims["has_request_ids"]:
            recs.append({
                "title": "Enable Caddy structured access logs",
                "priority": "medium",
                "config": """\
# Caddyfile — in global block or site block:
{
    log {
        output file /var/log/caddy/access.log
        format json
    }
}
# Or in site block:
log {
    output file /var/log/caddy/access.log
    format json
}""",
            })

    elif service == "varnish":
        if not dims["has_timing"] or not dims["has_ips"]:
            recs.append({
                "title": "Enable varnishncsa for structured access logging",
                "priority": "high",
                "config": """\
# Run varnishncsa as a service to produce combined-format logs:
varnishncsa -F '%h - %u [%{%d/%b/%Y:%T %z}t] "%r" %s %b "%{Referer}i" "%{User-agent}i" %{Varnish:time_firstbyte}x %{Varnish:hitmiss}x' \\
  -w /var/log/varnish/access.log

# VCL — set request ID in vcl_recv:
sub vcl_recv {
    if (!req.http.X-Request-Id) {
        set req.http.X-Request-Id = req.http.host + "-" + now;
    }
}""",
            })

    elif service == "postgres":
        if not dims["has_ips"] or not dims["has_timing"]:
            recs.append({
                "title": "Enable PostgreSQL connection and query logging",
                "priority": "high",
                "config": """\
# postgresql.conf:
log_connections = on
log_disconnections = on
log_min_duration_statement = 100    # log all queries slower than 100ms
log_line_prefix = '%m [%p] %d %r %a %u '
# %m=timestamp %p=pid %d=database %r=client_ip:port %a=app_name %u=user
log_statement = 'ddl'
log_temp_files = 0                  # log all temp file usage""",
            })

    elif service == "mysql":
        if not dims["has_timing"]:
            recs.append({
                "title": "Enable MySQL slow query and connection logging",
                "priority": "high",
                "config": """\
# my.cnf or my.ini — in [mysqld] section:
slow_query_log = ON
slow_query_log_file = /var/log/mysql/slow.log
long_query_time = 1                 # log queries slower than 1s (use 0.1 for 100ms)
log_queries_not_using_indexes = ON
log_slow_admin_statements = ON

# General log (careful in production — high volume):
general_log = ON
general_log_file = /var/log/mysql/general.log""",
            })

    elif service == "mongodb":
        if not dims["has_timing"] or not dims["has_ips"]:
            recs.append({
                "title": "Enable MongoDB slow operation logging",
                "priority": "high",
                "config": """\
# mongod.conf:
systemLog:
  verbosity: 1
  path: /var/log/mongodb/mongod.log
  logAppend: true
operationProfiling:
  slowOpThresholdMs: 100      # profile operations slower than 100ms
  mode: slowOp

# Or set at runtime:
db.setProfilingLevel(1, { slowms: 100 })
db.adminCommand({ setParameter: 1, logLevel: 1 })""",
            })

    elif service == "redis":
        if not dims["has_timing"]:
            recs.append({
                "title": "Enable Redis slow log and verbose logging",
                "priority": "medium",
                "config": """\
# redis.conf:
loglevel verbose          # or 'debug' for more detail
slowlog-log-slower-than 10000   # microseconds (10ms)
slowlog-max-len 256

# At runtime:
CONFIG SET loglevel verbose
CONFIG SET slowlog-log-slower-than 10000

# Check slow log:
SLOWLOG GET 25""",
            })

    elif service == "elasticsearch":
        if not dims["has_timing"]:
            recs.append({
                "title": "Enable Elasticsearch slow log",
                "priority": "medium",
                "config": """\
# elasticsearch.yml:
# Set per-index (or use _all):
PUT /my-index/_settings
{
  "index.search.slowlog.threshold.query.warn": "2s",
  "index.search.slowlog.threshold.query.info": "500ms",
  "index.search.slowlog.threshold.fetch.warn": "1s",
  "index.indexing.slowlog.threshold.index.warn": "2s",
  "index.indexing.slowlog.source": "1000"
}

# log4j2.properties:
logger.index_search_slowlog_rolling.level = trace""",
            })

    elif service == "rabbitmq":
        if not dims["has_ips"]:
            recs.append({
                "title": "Enable RabbitMQ connection and channel debug logging",
                "priority": "medium",
                "config": """\
# rabbitmq.conf:
log.level = info
log.connection.level = debug    # log all connection events with client IPs
log.channel.level = warning

# Or at runtime:
rabbitmqctl set_log_level debug

# Advanced config (advanced.config):
[{rabbit, [{log, [{console, [{level, info}]}, {file, [{level, debug}]}]}]}].""",
            })

    elif service == "kafka":
        if not dims["has_timing"] or not dims["has_request_ids"]:
            recs.append({
                "title": "Enable Kafka request logging",
                "priority": "medium",
                "config": """\
# log4j.properties (Kafka broker):
log4j.logger.kafka.request.logger=DEBUG, requestAppender
log4j.additivity.kafka.request.logger=false
log4j.appender.requestAppender=org.apache.log4j.DailyRollingFileAppender
log4j.appender.requestAppender.DatePattern='.'yyyy-MM-dd-HH
log4j.appender.requestAppender.File=${kafka.logs.dir}/kafka-request.log
log4j.appender.requestAppender.layout=org.apache.log4j.PatternLayout
log4j.appender.requestAppender.layout.ConversionPattern=[%d] %p %m (%c)%n""",
            })

    elif service == "memcached":
        if not dims["has_ips"]:
            recs.append({
                "title": "Enable Memcached verbose connection logging",
                "priority": "medium",
                "config": """\
# Start memcached with verbose flag:
memcached -v        # basic connection events
memcached -vv       # all requests
memcached -vvv      # all internal events

# systemd unit override:
# /etc/systemd/system/memcached.service.d/override.conf
[Service]
ExecStart=
ExecStart=/usr/bin/memcached -v -m 64 -p 11211 -u memcache""",
            })

    elif service == "php_fpm":
        if not dims["has_ips"] or not dims["has_timing"]:
            recs.append({
                "title": "Enable PHP-FPM access log and slow log",
                "priority": "high",
                "config": """\
# /etc/php/X.Y/fpm/pool.d/www.conf:
access.log = /var/log/php-fpm/$pool.access.log
access.format = "%R - %u %t \\"%m %r%Q%q\\" %s %f %{mili}dms %{kilo}MMB %C%%"
# %R=client IP %m=method %r=path %s=status %f=script %d=duration

slowlog = /var/log/php-fpm/$pool.slow.log
request_slowlog_timeout = 5s       # log requests slower than 5s

# Also set in php.ini:
log_errors = On
error_log = /var/log/php/error.log""",
            })

    elif service == "gunicorn":
        if not dims["has_timing"] or not dims["has_request_ids"]:
            recs.append({
                "title": "Enable Gunicorn structured access logging",
                "priority": "high",
                "config": """\
# gunicorn.conf.py or command line:
accesslog = "/var/log/gunicorn/access.log"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sμs'
# %(h)=client_ip %(r)=request %(s)=status %(D)=time_in_microseconds

# For uvicorn:
# --access-log --log-level info

# Add request ID middleware (Python):
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id", str(uuid.uuid4()))
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response""",
            })

    else:  # generic application
        if not dims["has_request_ids"]:
            recs.append({
                "title": "Add request/trace IDs to every log line",
                "priority": "high",
                "config": """\
# Goal: every log line should include a unique request ID so flows can be reconstructed.
# Generate one ID per incoming request, log it everywhere, pass it to upstreams.

# Python (structlog):
import structlog, uuid
log = structlog.get_logger()
log.info("handling request", request_id=request.headers.get("x-request-id", str(uuid.uuid4())))

# Node.js (winston):
const { v4: uuidv4 } = require('uuid');
logger.info('request', { requestId: req.headers['x-request-id'] || uuidv4() });

# Java (logback) — logback.xml pattern:
<pattern>%d %thread [%X{requestId}] %-5level %logger - %msg%n</pattern>
# Set via: MDC.put("requestId", requestId)

# Go (zerolog):
log.Info().Str("request_id", r.Header.Get("X-Request-ID")).Msg("handling request")""",
            })
        if not dims["has_upstream"]:
            recs.append({
                "title": "Log all outbound calls with destination and duration",
                "priority": "high",
                "config": """\
# Log every outbound HTTP call, DB query, and cache operation with:
# - destination URL/host
# - response status
# - duration in ms
# - the same request_id propagated from the incoming request
#
# This is the single most important change for flow tracing.

# Python example:
import time, requests
def call_upstream(url, request_id):
    t0 = time.time()
    resp = requests.get(url, headers={"X-Request-ID": request_id})
    log.info("upstream call", url=url, status=resp.status_code,
             duration_ms=int((time.time()-t0)*1000), request_id=request_id)
    return resp""",
            })
        if not dims["has_timing"]:
            recs.append({
                "title": "Add response time to access logs",
                "priority": "medium",
                "config": "Log request duration (ms) for every HTTP request to enable latency flow visualization.",
            })

    return recs


# ── Main analysis function ────────────────────────────────────────────────────

async def analyze_node_verbosity(node_id: str, tenant_id: str, db: AsyncSession) -> dict:
    """Analyze recent logs for a node and return verbosity score + recommendations."""
    node_r = await db.execute(
        select(Node).where(Node.id == node_id, Node.tenant_id == tenant_id)
    )
    node = node_r.scalar_one_or_none()
    if node is None:
        return {"error": "node not found"}

    since = datetime.now(timezone.utc) - timedelta(hours=24)

    # Try by node_id first, then node_name (for nodes registered via heartbeat)
    log_r = await db.execute(
        select(LogEvent.message, LogEvent.source, LogEvent.request_id,
               LogEvent.client_ip, LogEvent.upstream_addr, LogEvent.response_time_ms)
        .where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.node_id == node_id,
            LogEvent.event_ts >= since,
            LogEvent.message.isnot(None),
        )
        .limit(300)
    )
    rows = log_r.all()

    if not rows:
        log_r2 = await db.execute(
            select(LogEvent.message, LogEvent.source, LogEvent.request_id,
                   LogEvent.client_ip, LogEvent.upstream_addr, LogEvent.response_time_ms)
            .where(
                LogEvent.tenant_id == tenant_id,
                LogEvent.node_name == node.external_id,
                LogEvent.event_ts >= since,
                LogEvent.message.isnot(None),
            )
            .limit(300)
        )
        rows = log_r2.all()

    if not rows:
        return {
            "score": 0, "log_count": 0, "detected_service": "unknown",
            "dimensions": {k: False for k in ("has_ips", "has_request_ids", "has_timing",
                                               "has_upstream", "has_status_codes",
                                               "has_cf_ray", "has_error_context")},
            "missing": ["No logs received in last 24h"],
            "recommendations": [{
                "title": "No logs received",
                "priority": "high",
                "config": "Ensure the Pyxis agent is running: systemctl status pyxis-agent",
            }],
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

    messages = [r.message for r in rows if r.message]
    full_text = " ".join(messages[:150])
    sources = {r.source for r in rows}

    # Detect service type
    detected_service = "application"
    # Check source tags first
    for src in sources:
        if src in _SERVICE_SRC_MAP:
            detected_service = _SERVICE_SRC_MAP[src]
            break
    # Then check content
    if detected_service == "application":
        for svc, pat in _SERVICE_CONTENT_PATTERNS.items():
            if pat.search(full_text):
                detected_service = svc
                break

    # Score each dimension
    has_ips          = bool(_IP_RE.search(full_text)) or any(r.client_ip for r in rows)
    has_request_ids  = (bool(_REQID_RE.search(full_text)) or bool(_UUID_RE.search(full_text))
                        or any(r.request_id for r in rows))
    has_timing       = bool(_TIMING_RE.search(full_text)) or any(r.response_time_ms for r in rows)
    has_upstream     = bool(_UPSTREAM_RE.search(full_text)) or any(r.upstream_addr for r in rows)
    has_status_codes = bool(_STATUS_RE.search(full_text))
    has_cf_ray       = bool(_CF_RE.search(full_text))
    has_error_context = bool(re.search(
        r'\b(?:error|exception|fatal|traceback|panic|oom|killed|segfault)\b',
        full_text, re.I,
    ))

    score = sum([
        has_ips          * 20,
        has_request_ids  * 25,
        has_timing       * 20,
        has_upstream     * 20,
        has_status_codes * 10,
        has_cf_ray       * 5,
    ])

    dims = {
        "has_ips": has_ips,
        "has_request_ids": has_request_ids,
        "has_timing": has_timing,
        "has_upstream": has_upstream,
        "has_status_codes": has_status_codes,
        "has_cf_ray": has_cf_ray,
        "has_error_context": has_error_context,
    }

    missing = []
    if not has_ips:          missing.append("IP addresses")
    if not has_request_ids:  missing.append("Request/trace IDs")
    if not has_timing:       missing.append("Response timing/latency")
    if not has_upstream:     missing.append("Upstream/backend info")
    if not has_status_codes: missing.append("HTTP status codes")

    recs = _recs_for(detected_service, dims)

    return {
        "score": score,
        "log_count": len(rows),
        "detected_service": detected_service,
        "dimensions": dims,
        "missing": missing,
        "recommendations": recs,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
