#!/usr/bin/env python3
"""
InfraWatch log shipper.
Runs on Linux hosts and K8s nodes.
Tails log files + watches K8s events and POSTs them to the backend.

Usage:
    export INFRAWATCH_API_KEY=your-key
    export INFRAWATCH_API_URL=https://your-infrawatch.example.com
    python shipper.py --sources syslog,k8s

Config via env vars:
    INFRAWATCH_API_KEY         required
    INFRAWATCH_API_URL         default: http://localhost:8000
    INFRAWATCH_NODE_NAME       default: hostname
    INFRAWATCH_NODE_KIND       default: linux_host
    INFRAWATCH_FLUSH_INTERVAL  seconds between batch flushes (default: 5)
    INFRAWATCH_BATCH_SIZE      max events per batch (default: 100)
    INFRAWATCH_BUFFER_DIR      dir for disk buffer when backend unreachable (default: /tmp/infrawatch)
    INFRAWATCH_HEARTBEAT_INTERVAL  seconds between heartbeats (default: 60)
"""

import argparse
import json
import logging
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("infrawatch-shipper")

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("INFRAWATCH_API_KEY", "")
API_URL = os.environ.get("INFRAWATCH_API_URL", "http://localhost:8000").rstrip("/")
NODE_NAME = os.environ.get("INFRAWATCH_NODE_NAME", socket.gethostname())
NODE_KIND = os.environ.get("INFRAWATCH_NODE_KIND", "linux_host")
FLUSH_INTERVAL = int(os.environ.get("INFRAWATCH_FLUSH_INTERVAL", "5"))
BATCH_SIZE = int(os.environ.get("INFRAWATCH_BATCH_SIZE", "100"))
BUFFER_DIR = os.environ.get("INFRAWATCH_BUFFER_DIR", "/tmp/infrawatch")
HEARTBEAT_INTERVAL = int(os.environ.get("INFRAWATCH_HEARTBEAT_INTERVAL", "60"))

INGEST_URL = f"{API_URL}/api/v1/ingest/"
HEARTBEAT_URL = f"{API_URL}/api/v1/heartbeat/"

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
    while True:
        time.sleep(FLUSH_INTERVAL)
        with _lock:
            _flush_locked()
        _drain_disk_buffer()


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
                log.warning("Unexpected status %d from ingest endpoint", resp.status)
    except urllib.error.URLError as e:
        log.warning("Backend unreachable (%s) — writing %d events to disk buffer", e, len(events))
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

def heartbeat_loop() -> None:
    """Send a heartbeat every HEARTBEAT_INTERVAL seconds."""
    while True:
        try:
            payload = json.dumps({"node_name": NODE_NAME, "node_kind": NODE_KIND}).encode()
            req = urllib.request.Request(
                HEARTBEAT_URL,
                data=payload,
                headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            log.debug("Heartbeat failed: %s", e)
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="InfraWatch log shipper")
    parser.add_argument(
        "--sources",
        default="syslog",
        help="Comma-separated sources: syslog,k8s,pipeline",
    )
    parser.add_argument("--k8s-namespace", default="--all-namespaces")
    args = parser.parse_args()

    if not API_KEY:
        log.error("INFRAWATCH_API_KEY not set")
        sys.exit(1)

    sources = [s.strip() for s in args.sources.split(",")]
    log.info("Starting InfraWatch shipper | node=%s | sources=%s", NODE_NAME, sources)

    # Start flush + heartbeat threads
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()

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
