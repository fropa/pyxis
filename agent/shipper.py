#!/usr/bin/env python3
"""
Pyxis log shipper.
Runs on Linux hosts and K8s nodes.
Tails log files + watches K8s events and POSTs them to the backend.

Usage:
    export PYXIS_API_KEY=your-key
    export PYXIS_API_URL=https://your-pyxis.example.com
    python shipper.py --sources syslog,k8s

Config via env vars:
    PYXIS_API_KEY         required
    PYXIS_API_URL         default: http://localhost:8000
    PYXIS_NODE_NAME       default: hostname
    PYXIS_NODE_KIND       default: linux_host
    PYXIS_FLUSH_INTERVAL  seconds between batch flushes (default: 5)
    PYXIS_BATCH_SIZE      max events per batch (default: 100)
    PYXIS_BUFFER_DIR      dir for disk buffer when backend unreachable (default: /tmp/pyxis)
    PYXIS_HEARTBEAT_INTERVAL  seconds between heartbeats (default: 60)
"""

import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error
import urllib.parse

# shorthand used in exec_loop
urllib.request.quote = urllib.parse.quote

_LOG_DIR = os.environ.get("PYXIS_LOG_DIR", "/opt/pyxis/logs")
_LOG_FILE = os.path.join(_LOG_DIR, "shipper.log")

os.makedirs(_LOG_DIR, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
)
_file_handler.setFormatter(_fmt)
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stdout_handler])
log = logging.getLogger("pyxis-shipper")

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("PYXIS_API_KEY", "")
API_URL = os.environ.get("PYXIS_API_URL", "http://localhost:8000").rstrip("/")
NODE_NAME = os.environ.get("PYXIS_NODE_NAME", socket.gethostname())
NODE_KIND = os.environ.get("PYXIS_NODE_KIND", "linux_host")
FLUSH_INTERVAL = int(os.environ.get("PYXIS_FLUSH_INTERVAL", "5"))
BATCH_SIZE = int(os.environ.get("PYXIS_BATCH_SIZE", "100"))
BUFFER_DIR = os.environ.get("PYXIS_BUFFER_DIR", "/tmp/pyxis")
HEARTBEAT_INTERVAL = int(os.environ.get("PYXIS_HEARTBEAT_INTERVAL", "60"))

INGEST_URL = f"{API_URL}/api/v1/ingest/"
HEARTBEAT_URL = f"{API_URL}/api/v1/heartbeat/"
EXEC_POLL_URL = f"{API_URL}/api/v1/exec/poll"
EXEC_RESULT_URL = f"{API_URL}/api/v1/exec/result"
EXEC_POLL_INTERVAL = 3

SHIPPER_PATH = os.environ.get("PYXIS_SHIPPER_PATH", "/opt/pyxis/shipper.py")
CONFIG_PATH  = os.environ.get("PYXIS_CONFIG_PATH", "/opt/pyxis/config.json")
AUTOUPDATE_INTERVAL = int(os.environ.get("PYXIS_AUTOUPDATE_INTERVAL", "300"))  # 5 min

# ── Persisted agent config (overrides env vars when set from web) ─────────────

def _load_local_config() -> dict:
    try:
        return json.loads(Path(CONFIG_PATH).read_text())
    except Exception:
        return {}

def _save_local_config(cfg: dict) -> None:
    try:
        Path(CONFIG_PATH).write_text(json.dumps(cfg))
    except Exception as e:
        log.warning("Failed to save config: %s", e)

_local_config: dict = {}  # populated in main() after log setup


def _get_local_ip() -> str:
    """Best-effort: get the primary outbound IP address of this host."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


NODE_IP = _get_local_ip()

# Ensure disk buffer directory exists
Path(BUFFER_DIR).mkdir(parents=True, exist_ok=True)

# ── Event buffer ──────────────────────────────────────────────────────────────

_buffer: list[dict[str, Any]] = []
_lock = threading.Lock()


def enqueue(event: dict[str, Any]) -> None:
    with _lock:
        _buffer.append(event)
        if len(_buffer) >= BATCH_SIZE:
            _flush_locked()


def _flush_locked() -> None:
    if not _buffer:
        return
    batch = _buffer[:]
    _buffer.clear()
    _send(batch)


def flush_loop() -> None:
    """Flush in-memory buffer and drain any disk-buffered files."""
    ticks = 0
    while True:
        time.sleep(FLUSH_INTERVAL)
        with _lock:
            n = len(_buffer)
            _flush_locked()
            if n:
                log.info("Flushed %d events to backend", n)
        _drain_disk_buffer()
        ticks += 1
        if ticks % 12 == 0:  # every ~60s at 5s interval
            log.info("Shipper alive | node=%s | ip=%s", NODE_NAME, NODE_IP or "unknown")


def _send(events: list[dict[str, Any]]) -> None:
    payload = json.dumps({"events": events}).encode()
    req = urllib.request.Request(
        INGEST_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 202:
                log.warning("Ingest: unexpected status %d", resp.status)
            else:
                log.debug("Ingest: sent %d events → %d", len(events), resp.status)
    except urllib.error.HTTPError as e:
        log.error("Ingest: HTTP %d from backend (%s) — buffering %d events", e.code, e.reason, len(events))
        _write_disk_buffer(events)
    except urllib.error.URLError as e:
        log.warning("Ingest: backend unreachable (%s) — buffering %d events", e, len(events))
        _write_disk_buffer(events)


# ── Disk buffer ───────────────────────────────────────────────────────────────

def _write_disk_buffer(events: list[dict[str, Any]]) -> None:
    """Write events to a timestamped file when backend is unreachable."""
    ts = int(time.time() * 1000)
    path = Path(BUFFER_DIR) / f"events_{ts}.json"
    try:
        path.write_text(json.dumps(events))
    except Exception as e:
        log.error("Failed to write disk buffer: %s", e)


def _drain_disk_buffer() -> None:
    """Replay buffered files in order after backend becomes reachable."""
    buf_path = Path(BUFFER_DIR)
    files = sorted(buf_path.glob("events_*.json"))
    if not files:
        return
    for f in files:
        try:
            events = json.loads(f.read_text())
            payload = json.dumps({"events": events}).encode()
            req = urllib.request.Request(
                INGEST_URL,
                data=payload,
                headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 202:
                    f.unlink()
                    log.info("Drained buffered file %s (%d events)", f.name, len(events))
        except Exception:
            break  # backend still down, stop draining


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _send_heartbeat() -> dict:
    payload = json.dumps({
        "node_name": NODE_NAME,
        "node_kind": NODE_KIND,
        "ip_address": NODE_IP or None,
    }).encode()
    req = urllib.request.Request(
        HEARTBEAT_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _apply_config_if_changed(new_config: dict) -> None:
    """If the backend sent a new agent config, save it and restart to apply."""
    global _local_config
    if not new_config or new_config == _local_config:
        return
    log.info("Agent config changed (sources=%s, paths=%s) — restarting to apply",
             new_config.get("sources"), new_config.get("custom_log_paths"))
    _save_local_config(new_config)
    _local_config = new_config
    # Restart via systemd so the new config is loaded on startup
    try:
        subprocess.run(["systemctl", "restart", "pyxis-agent"], timeout=10)
    except Exception as e:
        log.warning("Could not restart via systemd (%s) — restart the agent manually", e)


def heartbeat_loop() -> None:
    """Send a heartbeat every HEARTBEAT_INTERVAL seconds, check for config changes."""
    while True:
        try:
            resp = _send_heartbeat()
            log.debug("Heartbeat sent → node_id=%s", resp.get("node_id"))
            _apply_config_if_changed(resp.get("config") or {})
            update_known_node_ips(resp)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                log.error(
                    "Heartbeat: 401 Unauthorized — PYXIS_API_KEY is invalid or the server was redeployed. "
                    "Reinstall the agent with a fresh key from the Pyxis dashboard."
                )
            else:
                log.error("Heartbeat: HTTP %d from backend", e.code)
        except urllib.error.URLError as e:
            log.warning("Heartbeat: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.warning("Heartbeat failed: %s", e)
        time.sleep(HEARTBEAT_INTERVAL)


def make_event(
    source: str,
    raw: str,
    level: str = "info",
    parsed: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "node_name": NODE_NAME,
        "node_kind": NODE_KIND,
        "raw": raw,
        "parsed": parsed or {},
        "labels": labels or {},
    }


# ── Syslog / Journal tail ─────────────────────────────────────────────────────

SYSLOG_PATHS = [
    "/var/log/syslog",
    "/var/log/messages",
    "/var/log/auth.log",
    "/var/log/kern.log",
]

# syslog priority → level
_PRIORITY_LEVEL = {0: "critical", 1: "critical", 2: "critical",
                   3: "error", 4: "warning", 5: "info", 6: "info", 7: "debug"}


def infer_level(line: str) -> str:
    lower = line.lower()
    if any(k in lower for k in ("critical", "emerg", "alert", "panic")):
        return "critical"
    if any(k in lower for k in ("error", "err", "fail", "killed", "oom", "denied")):
        return "error"
    if "warn" in lower:
        return "warning"
    return "info"


def tail_journald() -> None:
    """Stream system journal via journalctl --output=json (preferred on systemd hosts).
    Retries indefinitely if journalctl exits unexpectedly."""
    retry_delay = 5
    while True:
        log.info("Tailing journald (systemd journal)")
        try:
            proc = subprocess.Popen(
                ["journalctl", "-f", "-n", "50", "--output=json", "--no-pager"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                # bufsize=1 ensures we read line-by-line without waiting for an 8KB buffer
                bufsize=1,
                text=True,
            )
            # readline() unblocks as soon as journalctl writes a newline,
            # unlike "for line in proc.stdout" which may buffer
            while True:
                line = proc.stdout.readline()
                if not line:
                    # journalctl exited — break inner loop to restart
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    message = entry.get("MESSAGE", "")
                    if not message or not isinstance(message, str):
                        continue
                    unit = entry.get("SYSLOG_IDENTIFIER") or entry.get("_SYSTEMD_UNIT", "")
                    priority = int(entry.get("PRIORITY", 6))
                    level = _PRIORITY_LEVEL.get(priority, "info")
                    enqueue(make_event(
                        "syslog",
                        message,
                        level=level,
                        labels={"unit": unit},
                    ))
                    scan_message_for_peer_ips(message)
                except (json.JSONDecodeError, ValueError):
                    continue
            proc.wait()
            log.warning("journalctl exited (code=%s), restarting in %ds…", proc.returncode, retry_delay)
        except FileNotFoundError:
            log.warning("journalctl not found — falling back to syslog files")
            return  # don't retry — start_syslog_tailers will use files
        except Exception as e:
            log.error("journald tail error: %s — restarting in %ds", e, retry_delay)
        time.sleep(retry_delay)


def tail_syslog_file(path: str) -> None:
    log.info("Tailing %s", path)
    try:
        proc = subprocess.Popen(
            ["tail", "-F", "-n", "0", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            enqueue(make_event("syslog", line, level=infer_level(line)))
            scan_message_for_peer_ips(line)
    except Exception as e:
        log.error("syslog tail error on %s: %s", path, e)


# Auth-related syslog identifiers to collect as "auth_log" source
_AUTH_IDENTIFIERS = ["sshd", "sudo", "su", "login", "passwd", "useradd", "userdel",
                     "groupadd", "groupdel", "chpasswd", "systemd-logind", "polkitd"]


def tail_auth_journald() -> None:
    """Stream SSH / sudo / auth events from journald as 'auth_log' source."""
    identifier_args = []
    for ident in _AUTH_IDENTIFIERS:
        identifier_args += ["--identifier", ident]

    retry_delay = 5
    while True:
        log.info("Tailing auth logs from journald")
        try:
            proc = subprocess.Popen(
                ["journalctl", "-f", "-n", "100", "--output=json", "--no-pager"] + identifier_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                text=True,
            )
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    message = entry.get("MESSAGE", "")
                    if not message or not isinstance(message, str):
                        continue
                    unit = entry.get("SYSLOG_IDENTIFIER") or entry.get("_SYSTEMD_UNIT", "")
                    priority = int(entry.get("PRIORITY", 6))
                    level = _PRIORITY_LEVEL.get(priority, "info")
                    enqueue(make_event(
                        "auth_log",
                        message,
                        level=level,
                        labels={"unit": unit},
                    ))
                except (json.JSONDecodeError, ValueError):
                    continue
            proc.wait()
            log.warning("auth journalctl exited (code=%s), restarting in %ds…", proc.returncode, retry_delay)
        except FileNotFoundError:
            log.warning("journalctl not found — auth log collection disabled")
            return
        except Exception as e:
            log.error("auth journald tail error: %s — restarting in %ds", e, retry_delay)
        time.sleep(retry_delay)


def _detect_source_from_path(path: str) -> str:
    """Detect the log source type from the file path so the backend uses the right parser."""
    p = path.lower()
    if "nginx" in p:
        return "nginx_access" if "access" in p else ("nginx_error" if "error" in p else "nginx_access")
    if "apache" in p or "httpd" in p:
        return "apache_access" if "access" in p else "apache_error"
    if "haproxy" in p:
        return "haproxy"
    if "traefik" in p:
        return "traefik"
    if "caddy" in p:
        return "caddy"
    if "varnish" in p:
        return "varnish"
    if "envoy" in p:
        return "envoy"
    if "postgres" in p or "postgresql" in p or "pgsql" in p:
        return "postgres"
    if "mysql" in p or "mariadb" in p:
        return "mysql_slow" if "slow" in p else "mysql_general"
    if "mongodb" in p or "mongod" in p:
        return "mongodb"
    if "redis" in p:
        return "redis"
    if "elasticsearch" in p or "opensearch" in p:
        return "elasticsearch"
    if "rabbitmq" in p or "rabbit" in p:
        return "rabbitmq"
    if "kafka" in p:
        return "kafka"
    if "memcached" in p:
        return "memcached"
    if "php" in p and "fpm" in p:
        return "php_fpm"
    if "gunicorn" in p:
        return "gunicorn"
    if "uwsgi" in p:
        return "uwsgi"
    return "app_log"


def tail_custom_file(path: str) -> None:
    """Tail a custom log file path, auto-detecting source type from the path."""
    p = Path(path)
    if not p.exists():
        log.warning("Custom log path not found: %s — will retry when file appears", path)
    source = _detect_source_from_path(path)
    log.info("Tailing custom log: %s (source=%s)", path, source)
    retry_delay = 10
    while True:
        try:
            proc = subprocess.Popen(
                ["tail", "-F", "-n", "0", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                text=True,
            )
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip()
                if not line:
                    continue
                enqueue(make_event(source, line, level=infer_level(line),
                                   labels={"path": path}))
                scan_message_for_peer_ips(line)
            proc.wait()
            log.warning("tail on %s exited, restarting in %ds…", path, retry_delay)
        except Exception as e:
            log.error("custom file tail error on %s: %s", path, e)
        time.sleep(retry_delay)


def start_syslog_tailers() -> None:
    """Start journald first (preferred); fall back to log files if not available."""
    if subprocess.run(["which", "journalctl"], capture_output=True).returncode == 0:
        threading.Thread(target=tail_journald, daemon=True).start()
    else:
        found = [p for p in SYSLOG_PATHS if Path(p).exists()]
        if found:
            for path in found:
                threading.Thread(target=tail_syslog_file, args=(path,), daemon=True).start()
        else:
            log.warning("No syslog source found (no journalctl, no log files). Logs will not be collected.")


# ── K8s event watcher ─────────────────────────────────────────────────────────

def watch_k8s_events(namespace: str = "--all-namespaces") -> None:
    """Stream K8s events using kubectl."""
    log.info("Watching K8s events (namespace=%s)", namespace)
    ns_flag = ["--all-namespaces"] if namespace == "--all-namespaces" else ["-n", namespace]
    cmd = ["kubectl", "get", "events", "--watch", "-o", "json"] + ns_flag

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        buf = ""
        for chunk in iter(lambda: proc.stdout.read(4096), ""):
            buf += chunk
            # Try to parse complete JSON objects
            while True:
                try:
                    obj, idx = _parse_json_stream(buf)
                    if obj is None:
                        break
                    buf = buf[idx:]
                    _handle_k8s_event(obj)
                except Exception:
                    break
    except FileNotFoundError:
        log.warning("kubectl not found — skipping K8s event watcher")
    except Exception as e:
        log.error("K8s event watcher error: %s", e)


def _parse_json_stream(buf: str) -> tuple[dict | None, int]:
    """Try to extract one complete JSON object from the buffer."""
    decoder = json.JSONDecoder()
    buf = buf.lstrip()
    try:
        obj, idx = decoder.raw_decode(buf)
        return obj, idx
    except json.JSONDecodeError:
        return None, 0


def _handle_k8s_event(obj: dict[str, Any]) -> None:
    reason = obj.get("reason", "")
    message = obj.get("message", "")
    ev_type = obj.get("type", "Normal")  # Normal | Warning
    involved = obj.get("involvedObject", {})
    node_name = involved.get("name", NODE_NAME)
    node_kind = "k8s_" + involved.get("kind", "node").lower()

    level = "warning" if ev_type == "Warning" else "info"
    if reason in ("Failed", "BackOff", "Killing", "OOMKilling", "Evicted"):
        level = "error"

    raw = f"[K8s] {reason}: {message}"
    enqueue({
        "source": "k8s_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "node_name": node_name,
        "node_kind": node_kind,
        "raw": raw,
        "parsed": {
            "reason": reason,
            "message": message,
            "type": ev_type,
            "involvedObject": involved,
            "action": obj.get("action"),
        },
        "labels": {
            "namespace": involved.get("namespace", ""),
            "kind": involved.get("kind", ""),
        },
    })


# ── CI/CD log stdin reader ────────────────────────────────────────────────────

def read_stdin_pipeline() -> None:
    """
    Read pipeline logs from stdin (pipe from CI runner).
    Usage: your-pipeline-command 2>&1 | python shipper.py --sources pipeline
    """
    log.info("Reading pipeline logs from stdin")
    for line in sys.stdin:
        line = line.rstrip()
        if not line:
            continue
        enqueue(make_event(
            "ci_pipeline",
            line,
            level=infer_level(line),
            labels={"pipeline": os.environ.get("CI_PIPELINE_ID", ""),
                    "job": os.environ.get("CI_JOB_NAME", "")},
        ))


# ── Remote exec ───────────────────────────────────────────────────────────────

import re as _re

# Commands that are explicitly blocked regardless of user.
# These patterns are matched against the full command string (lowercased).
_EXEC_BLOCK_PATTERNS = [
    _re.compile(p) for p in [
        r'\brm\s+-',                     # rm -rf etc.
        r'\brmdir\b',
        r'\bdd\s+',                      # disk destroyer
        r'\bmkfs\b', r'\bformat\b',
        r'\bfdisk\b', r'\bparted\b', r'\bsgdisk\b',
        r'\bshred\b', r'\bwipe\b',
        r'\bchmod\b', r'\bchown\b', r'\bchattr\b',
        r'\buseradd\b', r'\buserdel\b', r'\busermod\b',
        r'\bgroupadd\b', r'\bgroupdel\b',
        r'\bpasswd\b', r'\bchpasswd\b',
        r'\bvisudo\b',
        r'\bsu\s', r'\bsudo\s',          # no privilege escalation
        r'\biptables\b', r'\bip6tables\b', r'\bnftables\b', r'\bufw\b',
        r'\bfirewall-cmd\b',
        r'\bshutdown\b', r'\breboot\b', r'\bhalt\b', r'\bpoweroff\b', r'\binit\s+[06]\b',
        r'\bcrontab\s+-[re]\b',          # editing crontab (list -l is ok)
        r'\bat\s+\b',                    # at scheduler
        r'>\s*/',                        # redirect writes to absolute paths
        r'>\s*~',                        # redirect to home
        r'\|\s*(sh|bash|zsh|dash|python|perl|ruby|node)\b',  # pipe to shell
        r'curl\s+.*\|\s*(sh|bash)',      # curl pipe shell
        r'wget\s+.*\|\s*(sh|bash)',      # wget pipe shell
        r'\bbase64\s+-d\b.*\|\s*(sh|bash)',
        r'`[^`]+`',                      # backtick subshell in certain contexts
        r'\$\([^)]+\)',                  # command substitution ($(...))
        r'\beval\b',
        r'\bexec\s+\d',                  # exec fd redirects
        r'\bkill\s+-9\s+1\b',           # kill init/systemd
        r'\bpkill\s+-9\b',
        r'\bmkdir\s+.*(/etc|/bin|/sbin|/boot|/lib)',
        r'\bsystemctl\s+(start|stop|restart|disable|enable|mask)\b(?!.*pyxis)',
        # allow: systemctl status, is-active, show, list-units
    ]
]

# Allow-listed command prefixes — even if not blocked above, only these run.
# Each entry is a prefix; the command must start with one of these (stripped).
_EXEC_ALLOW_PREFIXES = (
    "free", "df ", "df\t", "du ",
    "top ", "htop", "atop",
    "ps ", "ps\t", "pgrep", "pstree",
    "uptime", "uname", "hostname", "hostnamectl",
    "uname",
    "cat /proc/", "cat /sys/",
    "cat /etc/os-release", "cat /etc/hostname",
    "lscpu", "lsmem", "lsblk", "lspci", "lsusb",
    "lsof ",
    "ss ", "netstat", "ip addr", "ip route", "ip link",
    "ping ", "traceroute ", "tracepath ", "mtr ",
    "dig ", "nslookup ", "host ",
    "curl ", "wget ",
    "journalctl ",
    "systemctl status", "systemctl is-active", "systemctl is-enabled",
    "systemctl show ", "systemctl list-units",
    "service ",  # service X status
    "tail ", "head ", "less ", "more ",
    "grep ", "egrep ", "fgrep ", "zgrep ",
    "find /var/log", "find /tmp",
    "ls ", "ls\t", "ll ", "dir ",
    "echo ", "printf ",
    "date", "timedatectl",
    "who", "w ", "last ", "lastlog",
    "id", "whoami", "groups",
    "env", "printenv",
    "python3 --version", "python3 -c",
    "openssl ",
    "vmstat", "iostat", "mpstat", "sar ",
    "dmesg",
    "mount",
)


def _exec_allowed(cmd: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Two-layer check: blocklist first, then allowlist prefix.
    """
    cmd_lower = cmd.lower().strip()

    # Layer 1: blocklist
    for pat in _EXEC_BLOCK_PATTERNS:
        if pat.search(cmd_lower):
            return False, f"blocked pattern: {pat.pattern}"

    # Layer 2: allowlist prefix
    for prefix in _EXEC_ALLOW_PREFIXES:
        if cmd_lower.startswith(prefix.lower()):
            return True, "allowed"

    return False, "not in allowlist"


def exec_loop() -> None:
    """Poll for commands from the backend and execute them."""
    while True:
        try:
            url = f"{EXEC_POLL_URL}?node_name={urllib.request.quote(NODE_NAME)}"
            req = urllib.request.Request(
                url,
                headers={"X-API-Key": API_KEY},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            cmd_id = data.get("cmd_id")
            cmd = data.get("cmd")
            if cmd_id and cmd:
                allowed, reason = _exec_allowed(cmd)
                if not allowed:
                    log.warning("exec: BLOCKED command [%s]: %s — reason: %s", cmd_id[:8], cmd[:120], reason)
                    output = f"[pyxis] Command blocked by security policy: {reason}\nOnly read-only diagnostic commands are permitted."
                    exit_code = 126
                    duration_ms = 0
                else:
                    log.info("exec: running command [%s]: %s", cmd_id[:8], cmd[:120])
                    t0 = time.time()
                    try:
                        result = subprocess.run(
                            cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=25,
                        )
                        output = result.stdout + result.stderr
                        exit_code = result.returncode
                    except subprocess.TimeoutExpired:
                        output = "Command timed out (25s limit)"
                        exit_code = 124
                    except Exception as e:
                        output = f"Execution error: {e}"
                        exit_code = 1
                    duration_ms = int((time.time() - t0) * 1000)
                    log.info("exec: done [%s] exit=%d in %dms", cmd_id[:8], exit_code, duration_ms)

                payload = json.dumps({
                    "output": output[:32000],  # cap at 32KB
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                }).encode()
                result_req = urllib.request.Request(
                    f"{EXEC_RESULT_URL}/{cmd_id}",
                    data=payload,
                    headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                    method="POST",
                )
                urllib.request.urlopen(result_req, timeout=10)

        except urllib.error.HTTPError as e:
            if e.code == 401:
                log.error("exec poll: 401 Unauthorized — API key invalid, reinstall agent")
            else:
                log.debug("exec poll: HTTP %d", e.code)
        except urllib.error.URLError as e:
            log.debug("exec poll: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.debug("exec poll error: %s", e)

        time.sleep(EXEC_POLL_INTERVAL)


# ── Auto-update ───────────────────────────────────────────────────────────────

def autoupdate_loop() -> None:
    """Check for a new shipper.py every AUTOUPDATE_INTERVAL seconds.
    If the server has a newer version, download and restart via systemd."""
    while True:
        time.sleep(AUTOUPDATE_INTERVAL)
        try:
            # Compute hash of the running script
            try:
                current_hash = hashlib.sha256(Path(SHIPPER_PATH).read_bytes()).hexdigest()
            except Exception:
                continue  # can't read own file (Docker / non-standard install), skip

            # Ask backend for its current hash
            req = urllib.request.Request(
                f"{API_URL}/install/shipper-version",
                headers={"X-API-Key": API_KEY},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            remote_hash = data.get("sha256", "")
            if not remote_hash or remote_hash == current_hash:
                log.debug("Auto-update: up to date (%s…)", current_hash[:12])
                continue

            log.info("Auto-update: new version detected, downloading…")
            dl_req = urllib.request.Request(
                f"{API_URL}/install/shipper.py",
                headers={"X-API-Key": API_KEY},
                method="GET",
            )
            with urllib.request.urlopen(dl_req, timeout=30) as resp:
                new_content = resp.read()

            # Verify downloaded content before overwriting
            if hashlib.sha256(new_content).hexdigest() != remote_hash:
                log.error("Auto-update: hash mismatch after download — skipping")
                continue

            Path(SHIPPER_PATH).write_bytes(new_content)
            log.info("Auto-update: new agent written to %s, restarting service…", SHIPPER_PATH)
            subprocess.run(["systemctl", "restart", "pyxis-agent"], timeout=10)
            # systemd will kill this process — code below won't run

        except urllib.error.URLError:
            log.debug("Auto-update: backend unreachable, skipping")
        except Exception as e:
            log.warning("Auto-update check failed: %s", e)


# ── System health metrics ─────────────────────────────────────────────────────

METRICS_URL      = f"{API_URL}/api/v1/metrics/report"
METRICS_INTERVAL = int(os.environ.get("PYXIS_METRICS_INTERVAL", "60"))

# Two-sample CPU tracking (delta between readings gives real %)
_prev_cpu_stat: dict | None = None
_prev_cpu_lock  = threading.Lock()


def _read_cpu_stat() -> dict | None:
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return None
        vals = [int(x) for x in parts[1:11]]
        return {
            "user": vals[0], "nice": vals[1], "system": vals[2],
            "idle": vals[3],
            "iowait": vals[4] if len(vals) > 4 else 0,
            "total": sum(vals),
        }
    except Exception:
        return None


def _cpu_delta_pct() -> tuple[float, float]:
    """Return (cpu_used_pct, iowait_pct) computed from delta between last two /proc/stat reads."""
    global _prev_cpu_stat
    curr = _read_cpu_stat()
    if curr is None:
        return 0.0, 0.0
    with _prev_cpu_lock:
        prev = _prev_cpu_stat
        _prev_cpu_stat = curr
    if prev is None:
        return 0.0, 0.0
    dt = curr["total"] - prev["total"]
    if dt == 0:
        return 0.0, 0.0
    used_pct   = round((1 - (curr["idle"] - prev["idle"]) / dt) * 100, 1)
    iowait_pct = round((curr["iowait"] - prev["iowait"]) / dt * 100, 1)
    return max(0.0, used_pct), max(0.0, iowait_pct)


def _collect_system_metrics() -> dict:
    """Collect CPU, memory, disk, I/O, FD, process, and TCP metrics from /proc."""
    m: dict = {}

    # ── CPU count + load average ──────────────────────────────────────────────
    try:
        m["cpu_count"] = os.cpu_count() or 1
    except Exception:
        m["cpu_count"] = 1

    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
        m["load_avg_1m"]  = float(p[0])
        m["load_avg_5m"]  = float(p[1])
        m["load_avg_15m"] = float(p[2])
    except Exception:
        pass

    # ── CPU usage % (two-sample delta) ───────────────────────────────────────
    cpu_used, iowait = _cpu_delta_pct()
    m["cpu_used_pct"]  = cpu_used
    m["iowait_pct"]    = iowait

    # ── Memory from /proc/meminfo ─────────────────────────────────────────────
    try:
        mi: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mi[parts[0].rstrip(":")] = int(parts[1])
        total     = mi.get("MemTotal", 0)
        available = mi.get("MemAvailable", mi.get("MemFree", 0))
        used      = total - available
        swap_t    = mi.get("SwapTotal", 0)
        swap_f    = mi.get("SwapFree", 0)
        m["mem_total_mb"]     = total // 1024
        m["mem_available_mb"] = available // 1024
        m["mem_used_mb"]      = used // 1024
        m["mem_used_pct"]     = round(used / total * 100, 1) if total else 0.0
        m["swap_total_mb"]    = swap_t // 1024
        m["swap_used_mb"]     = (swap_t - swap_f) // 1024
        m["swap_used_pct"]    = round((swap_t - swap_f) / swap_t * 100, 1) if swap_t else 0.0
    except Exception:
        pass

    # ── Disk usage per mount ──────────────────────────────────────────────────
    try:
        _SKIP_FS = {"tmpfs","devtmpfs","sysfs","proc","devpts","cgroup","cgroup2",
                    "overlay","aufs","squashfs","fuse.lxcfs","hugetlbfs","mqueue",
                    "securityfs","debugfs","tracefs","bpf","pstore","configfs"}
        seen_devs: set[str] = set()
        disk_mounts = []
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount, fstype = parts[0], parts[1], parts[2]
                if fstype in _SKIP_FS or device in seen_devs:
                    continue
                if not device.startswith("/dev") and not device.startswith("//"):
                    continue
                seen_devs.add(device)
                try:
                    st = os.statvfs(mount)
                    blk_total = st.f_blocks * st.f_frsize
                    blk_free  = st.f_bfree  * st.f_frsize
                    blk_used  = blk_total - blk_free
                    used_pct  = round(blk_used / blk_total * 100, 1) if blk_total else 0.0
                    free_gb   = round(blk_free / 1073741824, 1)
                    ino_total = st.f_files
                    ino_free  = st.f_ffree
                    ino_pct   = round((ino_total - ino_free) / ino_total * 100, 1) if ino_total else 0.0
                    disk_mounts.append({
                        "mount": mount, "device": device,
                        "used_pct": used_pct, "free_gb": free_gb,
                        "inode_used_pct": ino_pct,
                    })
                except (PermissionError, OSError):
                    pass
        m["disk_mounts"] = disk_mounts
    except Exception:
        pass

    # ── Open file descriptors ─────────────────────────────────────────────────
    try:
        with open("/proc/sys/fs/file-nr") as f:
            fd_open = int(f.read().split()[0])
        with open("/proc/sys/fs/file-max") as f:
            fd_max = int(f.read().strip())
        m["fd_open"]     = fd_open
        m["fd_max"]      = fd_max
        m["fd_used_pct"] = round(fd_open / fd_max * 100, 1) if fd_max else 0.0
    except Exception:
        pass

    # ── TCP connection counts ─────────────────────────────────────────────────
    try:
        established = time_wait = 0
        for tcp_f in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(tcp_f) as f:
                    for line in f:
                        st = line.split()
                        if len(st) < 4:
                            continue
                        if st[3] == "01":
                            established += 1
                        elif st[3] == "06":
                            time_wait += 1
            except OSError:
                pass
        m["tcp_established"] = established
        m["tcp_time_wait"]   = time_wait
        try:
            with open("/proc/sys/net/core/somaxconn") as f:
                m["tcp_somaxconn"] = int(f.read().strip())
        except Exception:
            pass
    except Exception:
        pass

    # ── Process count vs system limit ─────────────────────────────────────────
    try:
        proc_count = sum(1 for p in os.listdir("/proc") if p.isdigit())
        m["process_count"] = proc_count
        with open("/proc/sys/kernel/threads-max") as f:
            m["process_max"] = int(f.read().strip())
        m["process_used_pct"] = round(proc_count / m["process_max"] * 100, 1) if m.get("process_max") else 0.0
    except Exception:
        pass

    # ── Uptime ────────────────────────────────────────────────────────────────
    try:
        with open("/proc/uptime") as f:
            m["uptime_seconds"] = int(float(f.read().split()[0]))
    except Exception:
        pass

    return m


def _score_pct(pct: float, thresholds: list[tuple[float, int]]) -> int:
    """Map a percentage to a score using a threshold table [(pct_limit, score), ...]."""
    for limit, score in thresholds:
        if pct < limit:
            return score
    return thresholds[-1][1]


def compute_health_score(m: dict) -> tuple[int, dict]:
    """
    Compute a 0-100 health score from raw metrics.
    Returns (overall_score, {component: score}) where lower = worse.
    """
    components: dict[str, int] = {}

    # CPU: use cpu_used_pct; also penalise if load queue is deep
    if "cpu_used_pct" in m:
        s = _score_pct(m["cpu_used_pct"], [
            (50, 100), (70, 90), (85, 70), (95, 35), (100, 10),
        ])
        # Extra load-queue penalty: load/core > 1.5 means jobs are queuing
        load_norm = m.get("load_avg_1m", 0) / max(m.get("cpu_count", 1), 1)
        if load_norm > 2.0:   s = min(s, 15)
        elif load_norm > 1.5: s = min(s, 35)
        elif load_norm > 1.0: s = min(s, 60)
        components["cpu"] = s

    # Memory
    if "mem_used_pct" in m:
        s = _score_pct(m["mem_used_pct"], [
            (60, 100), (75, 85), (85, 60), (92, 30), (97, 10), (100, 5),
        ])
        # Swap pressure amplifies memory stress
        swap_pct = m.get("swap_used_pct", 0)
        if swap_pct > 80:   s = min(s, 15)
        elif swap_pct > 50: s = min(s, 40)
        elif swap_pct > 20: s = max(s - 10, 10)
        components["memory"] = s

    # Disk: worst mount (space), then also check inodes
    if "disk_mounts" in m and m["disk_mounts"]:
        worst_space = max(d["used_pct"] for d in m["disk_mounts"])
        worst_inode = max(d.get("inode_used_pct", 0) for d in m["disk_mounts"])
        s = _score_pct(worst_space, [
            (70, 100), (80, 85), (90, 55), (95, 25), (99, 8), (100, 2),
        ])
        # Inode exhaustion is just as bad as space exhaustion
        if worst_inode > 95:   s = min(s, 10)
        elif worst_inode > 85: s = min(s, 40)
        components["disk"] = s

    # I/O wait
    if "iowait_pct" in m:
        components["io"] = _score_pct(m["iowait_pct"], [
            (5, 100), (15, 75), (30, 50), (50, 20), (100, 5),
        ])

    # File descriptors
    if "fd_used_pct" in m:
        components["file_descriptors"] = _score_pct(m["fd_used_pct"], [
            (50, 100), (75, 80), (90, 40), (95, 15), (100, 5),
        ])

    # Processes vs limit
    if "process_used_pct" in m:
        components["processes"] = _score_pct(m["process_used_pct"], [
            (50, 100), (75, 80), (90, 40), (95, 15), (100, 5),
        ])

    if not components:
        return 100, {}

    weights = {
        "cpu": 0.28, "memory": 0.28, "disk": 0.24,
        "io": 0.10, "file_descriptors": 0.06, "processes": 0.04,
    }
    total_w = sum(weights.get(k, 0.05) for k in components)
    score = sum(components[k] * weights.get(k, 0.05) for k in components) / total_w
    return max(0, min(100, round(score))), components


def metrics_loop() -> None:
    """Collect system metrics every METRICS_INTERVAL seconds and report to backend."""
    # Warm up CPU delta (first reading is always 0)
    _read_cpu_stat()
    time.sleep(2)
    _cpu_delta_pct()

    while True:
        time.sleep(METRICS_INTERVAL)
        try:
            raw = _collect_system_metrics()
            score, components = compute_health_score(raw)
            payload = json.dumps({
                "node_name": NODE_NAME,
                "metrics": raw,
                "health_score": score,
                "health_components": components,
            }).encode()
            req = urllib.request.Request(
                METRICS_URL,
                data=payload,
                headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            log.debug("metrics: reported score=%d components=%s", score, components)
        except urllib.error.URLError as e:
            log.debug("metrics: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.warning("metrics loop error: %s", e)


# ── Network connection reporter ───────────────────────────────────────────────

CONNECTIONS_URL = f"{API_URL}/api/v1/connections/report"
CONNECTIONS_INTERVAL = int(os.environ.get("PYXIS_CONNECTIONS_INTERVAL", "30"))

# Known peer node IPs — populated from heartbeat response, used for log scanning
# Maps ip_address → node_name for all peer nodes reported by the backend
_known_node_ips: dict[str, str] = {}
_known_ips_lock = threading.Lock()

# Log-detected connections — populated by log tailers, drained by network_connections_loop
_log_detected: list[dict] = []
_log_detected_lock = threading.Lock()

# Regex to find IPv4 addresses in log lines
_IP_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def update_known_node_ips(heartbeat_response: dict) -> None:
    """Update the known peer IPs from a heartbeat response."""
    global _known_node_ips
    new_ips = heartbeat_response.get("known_ips") or {}
    if new_ips:
        with _known_ips_lock:
            _known_node_ips = new_ips


def scan_message_for_peer_ips(message: str) -> None:
    """
    Check a log message for any known peer node IPs.
    If found, enqueue as a log-pattern-based connection evidence.
    Called from the log tailers on every line.
    """
    with _known_ips_lock:
        ips = dict(_known_node_ips)
    if not ips:
        return

    found = _IP_RE.findall(message)
    for ip in found:
        if ip in ips and not ip.startswith("127."):
            with _log_detected_lock:
                _log_detected.append({
                    "remote_ip":   ip,
                    "remote_port": 0,
                    "local_port":  0,
                    "process":     "",
                    "source":      "log_pattern",
                })


def _parse_ss_connections() -> list[dict]:
    """Run 'ss -tnp' (via sudo if available) and return ESTABLISHED TCP connections.
    Falls back to 'ss -tn' without process info when sudo is not configured."""
    # Try with sudo first (pyxis user has sudoers rule for exactly this command)
    for cmd in (["sudo", "-n", "ss", "-tnp"], ["ss", "-tnp"], ["ss", "-tn"]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                break
        except FileNotFoundError:
            continue
        except Exception:
            continue
    else:
        return []

    connections = []
    for line in result.stdout.splitlines():
        if not line.startswith("ESTAB"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            local_addr  = parts[3]
            peer_addr   = parts[4]
            local_port  = int(local_addr.rsplit(":", 1)[-1])
            remote_ip   = peer_addr.rsplit(":", 1)[0]
            remote_port = int(peer_addr.rsplit(":", 1)[-1])
            if remote_ip.startswith("127.") or remote_ip == "::1":
                continue
            process = ""
            if len(parts) >= 6 and parts[5].startswith("users:"):
                try:
                    process = parts[5].split('"')[1]
                except IndexError:
                    pass
            connections.append({
                "remote_ip": remote_ip, "remote_port": remote_port,
                "local_port": local_port, "process": process,
                "source": "ss_established",
            })
        except (ValueError, IndexError):
            continue
    return connections


def _hex_to_ip(hex_addr: str) -> str:
    """Convert little-endian hex address from /proc/net/tcp to dotted IPv4."""
    try:
        n = int(hex_addr, 16)
        return f"{n & 0xff}.{(n >> 8) & 0xff}.{(n >> 16) & 0xff}.{(n >> 24) & 0xff}"
    except ValueError:
        return ""


def _parse_proc_net_tcp() -> list[dict]:
    """
    Parse /proc/net/tcp (and tcp6) for ESTABLISHED + TIME_WAIT connections.
    TIME_WAIT entries represent connections closed in the last ~2 minutes —
    important for catching short-lived connections that ss misses.

    State hex codes: 01=ESTABLISHED, 06=TIME_WAIT, 08=CLOSE_WAIT
    """
    STATE_SOURCE = {
        "01": "proc_net_estab",
        "06": "proc_net_timewait",
        "08": "proc_net_timewait",  # CLOSE_WAIT also means recently active
    }
    connections = []

    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                lines = f.readlines()[1:]  # skip header
        except OSError:
            continue

        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                state_hex = parts[3]
                if state_hex not in STATE_SOURCE:
                    continue

                rem_field = parts[2]  # hex "AABBCCDD:PPPP"
                rem_hex_addr, rem_hex_port = rem_field.split(":")
                remote_ip   = _hex_to_ip(rem_hex_addr)
                remote_port = int(rem_hex_port, 16)

                loc_field = parts[1]
                _, loc_hex_port = loc_field.split(":")
                local_port = int(loc_hex_port, 16)

                if not remote_ip or remote_ip.startswith("0.0.0.0") or remote_ip.startswith("127."):
                    continue

                connections.append({
                    "remote_ip": remote_ip, "remote_port": remote_port,
                    "local_port": local_port, "process": "",
                    "source": STATE_SOURCE[state_hex],
                })
            except (ValueError, IndexError):
                continue

    return connections


def _parse_arp_cache() -> list[dict]:
    """
    Read ARP cache — any IP in the table was communicated with recently on LAN.
    Provides the widest net: catches UDP, ICMP, and even very brief TCP sessions.
    """
    try:
        result = subprocess.run(["arp", "-n"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        try:
            # Fallback: read kernel ARP table directly
            with open("/proc/net/arp") as f:
                lines = f.readlines()[1:]
            connections = []
            for line in lines:
                parts = line.split()
                if parts and not parts[0].startswith("127."):
                    connections.append({
                        "remote_ip": parts[0], "remote_port": 0,
                        "local_port": 0, "process": "",
                        "source": "arp",
                    })
            return connections
        except Exception:
            return []
    except Exception:
        return []

    connections = []
    for line in result.stdout.splitlines():
        # arp -n: "Address HWtype HWaddress Flags Iface"
        parts = line.split()
        if len(parts) >= 3 and parts[1] != "HWtype":  # skip header
            ip = parts[0]
            if not ip.startswith("127.") and "." in ip:
                connections.append({
                    "remote_ip": ip, "remote_port": 0,
                    "local_port": 0, "process": "",
                    "source": "arp",
                })
    return connections


def _collect_all_connections() -> list[dict]:
    """
    Combine all detection sources, deduplicating by (remote_ip, remote_port).
    Higher-confidence sources win when the same endpoint is seen in multiple sources.
    """
    SOURCE_RANK = {
        "ss_established": 5,
        "proc_net_estab": 4,
        "proc_net_timewait": 3,
        "log_pattern": 2,
        "arp": 1,
    }

    best: dict[tuple, dict] = {}  # (remote_ip, remote_port) → best entry

    def _add(conns: list[dict]) -> None:
        for c in conns:
            key = (c["remote_ip"], c.get("remote_port", 0))
            existing = best.get(key)
            if existing is None or SOURCE_RANK.get(c["source"], 0) > SOURCE_RANK.get(existing["source"], 0):
                best[key] = c

    _add(_parse_ss_connections())
    _add(_parse_proc_net_tcp())
    _add(_parse_arp_cache())

    # Drain log-detected connections (de-dup by IP only since port=0)
    with _log_detected_lock:
        log_conns = _log_detected[:]
        _log_detected.clear()
    _add(log_conns)

    return list(best.values())


def network_connections_loop() -> None:
    """
    Collect TCP connections from multiple OS sources every CONNECTIONS_INTERVAL seconds
    and report to the backend. Sources: ss (ESTAB), /proc/net/tcp (TIME_WAIT),
    ARP cache, and log-line IP scanning.
    """
    log.info("Network connection reporter started (interval=%ds, sources: ss + /proc/net/tcp + arp + log-scan)",
             CONNECTIONS_INTERVAL)
    while True:
        time.sleep(CONNECTIONS_INTERVAL)
        try:
            conns = _collect_all_connections()
            if not conns:
                continue

            payload = json.dumps({
                "node_name":   NODE_NAME,
                "connections": conns,
            }).encode()
            req = urllib.request.Request(
                CONNECTIONS_URL,
                data=payload,
                headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                created = data.get("edges_created", 0)
                updated = data.get("edges_updated", 0)
                if created or updated:
                    log.info("Connections: %d checked → %d new edges, %d updated",
                             data.get("connections_checked", 0), created, updated)
        except urllib.error.URLError as e:
            log.debug("Connections report: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.debug("Connections report error: %s", e)


# ── Kubernetes cluster monitor ────────────────────────────────────────────────

K8S_STATE_URL = f"{API_URL}/api/v1/k8s/state"
K8S_MONITOR_INTERVAL = int(os.environ.get("PYXIS_K8S_INTERVAL", "30"))


def _kubectl_get(resource: str, all_namespaces: bool = False) -> list:
    """Run kubectl get <resource> -o json and return items list."""
    cmd = ["kubectl", "get", resource, "-o", "json"]
    if all_namespaces:
        cmd.append("--all-namespaces")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            return json.loads(result.stdout).get("items", [])
    except FileNotFoundError:
        log.warning("kubectl not found — K8s monitoring disabled")
    except Exception as e:
        log.debug("kubectl get %s failed: %s", resource, e)
    return []


def k8s_monitor_loop() -> None:
    """Snapshot cluster state and push to backend every K8S_MONITOR_INTERVAL seconds."""
    # Check kubectl is available
    if subprocess.run(["which", "kubectl"], capture_output=True).returncode != 0:
        log.warning("kubectl not found — K8s cluster monitor disabled")
        return

    log.info("K8s cluster monitor started (interval=%ds)", K8S_MONITOR_INTERVAL)
    while True:
        try:
            state = {
                "nodes":       _kubectl_get("nodes"),
                "pods":        _kubectl_get("pods",        all_namespaces=True),
                "deployments": _kubectl_get("deployments", all_namespaces=True),
                "namespaces":  _kubectl_get("namespaces"),
            }
            payload = json.dumps(state).encode()
            req = urllib.request.Request(
                K8S_STATE_URL,
                data=payload,
                headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                log.debug(
                    "K8s state pushed: %d nodes, %d pods, %d deployments, %d namespaces",
                    len(state["nodes"]), len(state["pods"]),
                    len(state["deployments"]), len(state["namespaces"]),
                )
        except urllib.error.HTTPError as e:
            log.error("K8s monitor push: HTTP %d", e.code)
        except urllib.error.URLError as e:
            log.warning("K8s monitor push: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.warning("K8s monitor error: %s", e)

        time.sleep(K8S_MONITOR_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _local_config

    parser = argparse.ArgumentParser(description="Pyxis log shipper")
    parser.add_argument(
        "--sources",
        default=os.environ.get("PYXIS_SOURCES", "syslog"),
        help="Comma-separated sources: syslog,auth,k8s,pipeline",
    )
    parser.add_argument("--k8s-namespace", default="--all-namespaces")
    args = parser.parse_args()

    if not API_KEY:
        log.error("PYXIS_API_KEY not set")
        sys.exit(1)

    # Load persisted config (may override CLI sources)
    _local_config = _load_local_config()
    if _local_config.get("sources_str"):
        sources_str = _local_config["sources_str"]
        log.info("Using web-configured sources: %s", sources_str)
    else:
        sources_str = args.sources
    sources = [s.strip() for s in sources_str.split(",") if s.strip()]
    custom_paths = _local_config.get("custom_log_paths", [])

    log.info("Starting Pyxis shipper | node=%s | ip=%s | sources=%s", NODE_NAME, NODE_IP or "unknown", sources)

    # Register node immediately on startup
    try:
        resp = _send_heartbeat()
        log.info("Node registered with backend (node_id=%s)", resp.get("node_id"))
        # Apply config from first heartbeat response
        _apply_config_if_changed(resp.get("config") or {})
        update_known_node_ips(resp)
        if resp.get("known_ips"):
            log.info("Loaded %d known peer IPs for log scanning", len(resp["known_ips"]))
    except Exception as e:
        log.warning("Initial heartbeat failed (will retry via heartbeat loop): %s", e)

    # Core threads — always run
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=exec_loop, daemon=True).start()
    threading.Thread(target=autoupdate_loop, daemon=True).start()
    threading.Thread(target=network_connections_loop, daemon=True).start()
    threading.Thread(target=metrics_loop, daemon=True).start()
    log.info("Remote exec + auto-update + network connection reporter + health metrics enabled")

    if "syslog" in sources:
        start_syslog_tailers()

    if "auth" in sources:
        threading.Thread(target=tail_auth_journald, daemon=True).start()
        log.info("Auth/SSH log collection enabled")

    if "k8s" in sources:
        threading.Thread(target=watch_k8s_events, args=(args.k8s_namespace,), daemon=True).start()
        threading.Thread(target=k8s_monitor_loop, daemon=True).start()
        log.info("K8s event watcher + cluster monitor enabled")

    for path in custom_paths:
        if path.strip():
            threading.Thread(target=tail_custom_file, args=(path.strip(),), daemon=True).start()
            log.info("Custom log path: %s", path)

    if "pipeline" in sources:
        # Only block on stdin when it's actually a pipe (not /dev/null from systemd)
        try:
            import stat as _stat
            _mode = os.fstat(sys.stdin.fileno()).st_mode
            _is_pipe = _stat.S_ISFIFO(_mode) or _stat.S_ISREG(_mode)
        except Exception:
            _is_pipe = False
        if _is_pipe:
            read_stdin_pipeline()
            return
        else:
            log.warning("'pipeline' source requested but stdin is not a pipe — ignoring")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        with _lock:
            _flush_locked()
        log.info("Shipper stopped")


if __name__ == "__main__":
    main()
