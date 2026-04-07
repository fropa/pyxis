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
import json
import logging
import logging.handlers
import os
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

def _send_heartbeat() -> None:
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
        log.debug("Heartbeat sent → %d", resp.status)


def heartbeat_loop() -> None:
    """Send a heartbeat every HEARTBEAT_INTERVAL seconds."""
    while True:
        try:
            _send_heartbeat()
        except urllib.error.HTTPError as e:
            log.error("Heartbeat: HTTP %d from backend — check API key", e.code)
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


# ── Syslog tail ───────────────────────────────────────────────────────────────

SYSLOG_PATHS = [
    "/var/log/syslog",
    "/var/log/messages",
    "/var/log/auth.log",
    "/var/log/kern.log",
]

ERROR_KEYWORDS = ["error", "fail", "critical", "panic", "oom", "killed", "denied"]


def infer_level(line: str) -> str:
    lower = line.lower()
    if any(k in lower for k in ("critical", "emerg", "alert", "panic")):
        return "critical"
    if any(k in lower for k in ("error", "err", "fail", "killed", "oom", "denied")):
        return "error"
    if "warn" in lower:
        return "warning"
    return "info"


def tail_syslog(path: str) -> None:
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
    except Exception as e:
        log.error("syslog tail error on %s: %s", path, e)


def start_syslog_tailers() -> None:
    for path in SYSLOG_PATHS:
        if Path(path).exists():
            t = threading.Thread(target=tail_syslog, args=(path,), daemon=True)
            t.start()


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
            log.debug("exec poll: HTTP %d", e.code)
        except urllib.error.URLError as e:
            log.debug("exec poll: backend unreachable (%s)", e.reason)
        except Exception as e:
            log.debug("exec poll error: %s", e)

        time.sleep(EXEC_POLL_INTERVAL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pyxis log shipper")
    parser.add_argument(
        "--sources",
        default="syslog",
        help="Comma-separated sources: syslog,k8s,pipeline",
    )
    parser.add_argument("--k8s-namespace", default="--all-namespaces")
    args = parser.parse_args()

    if not API_KEY:
        log.error("PYXIS_API_KEY not set")
        sys.exit(1)

    sources = [s.strip() for s in args.sources.split(",")]
    log.info("Starting Pyxis shipper | node=%s | ip=%s | sources=%s", NODE_NAME, NODE_IP or "unknown", sources)

    # Register node immediately on startup
    try:
        _send_heartbeat()
        log.info("Node registered with backend")
    except Exception as e:
        log.warning("Initial registration failed (will retry via heartbeat loop): %s", e)

    # Start flush + heartbeat + exec threads
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=exec_loop, daemon=True).start()
    log.info("Remote exec enabled — polling every %ds", EXEC_POLL_INTERVAL)

    threads: list[threading.Thread] = []

    if "syslog" in sources:
        start_syslog_tailers()

    if "k8s" in sources:
        t = threading.Thread(target=watch_k8s_events, args=(args.k8s_namespace,), daemon=True)
        t.start()
        threads.append(t)

    if "pipeline" in sources:
        # Run in main thread (reads stdin)
        read_stdin_pipeline()
        return

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
