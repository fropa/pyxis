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


def tail_custom_file(path: str) -> None:
    """Tail a custom log file path, sending entries as 'app_log' source."""
    p = Path(path)
    if not p.exists():
        log.warning("Custom log path not found: %s — will retry when file appears", path)
    log.info("Tailing custom log: %s", path)
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
                enqueue(make_event("app_log", line, level=infer_level(line),
                                   labels={"path": path}))
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


# ── Network connection reporter ───────────────────────────────────────────────

CONNECTIONS_URL = f"{API_URL}/api/v1/connections/report"
CONNECTIONS_INTERVAL = int(os.environ.get("PYXIS_CONNECTIONS_INTERVAL", "30"))


def _parse_ss_connections() -> list[dict]:
    """Run 'ss -tnp' and return established TCP connections as dicts."""
    try:
        result = subprocess.run(
            ["ss", "-tnp"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        log.warning("ss not found — network connection reporting disabled")
        return []
    except Exception as e:
        log.debug("ss error: %s", e)
        return []

    connections = []
    for line in result.stdout.splitlines():
        # ss -tnp output columns: State RecvQ SendQ LocalAddr:Port PeerAddr:Port [Process]
        # Only care about ESTAB lines
        if not line.startswith("ESTAB"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            local_addr = parts[3]   # 10.x.x.x:PORT
            peer_addr  = parts[4]   # 10.x.x.x:PORT

            local_port  = int(local_addr.rsplit(":", 1)[-1])
            remote_ip   = peer_addr.rsplit(":", 1)[0]
            remote_port = int(peer_addr.rsplit(":", 1)[-1])

            # Skip loopback connections
            if remote_ip.startswith("127.") or remote_ip == "::1":
                continue

            # Parse process name from "users:(("nginx",pid=1234,fd=3))"
            process = ""
            if len(parts) >= 6:
                proc_field = parts[5]
                if proc_field.startswith("users:"):
                    try:
                        process = proc_field.split('"')[1]
                    except IndexError:
                        pass

            connections.append({
                "remote_ip":   remote_ip,
                "remote_port": remote_port,
                "local_port":  local_port,
                "process":     process,
            })
        except (ValueError, IndexError):
            continue

    return connections


def network_connections_loop() -> None:
    """Report established TCP connections to the backend every CONNECTIONS_INTERVAL seconds."""
    log.info("Network connection reporter started (interval=%ds)", CONNECTIONS_INTERVAL)
    while True:
        time.sleep(CONNECTIONS_INTERVAL)
        try:
            conns = _parse_ss_connections()
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
                if data.get("edges_created") or data.get("edges_updated"):
                    log.info(
                        "Connections: %d checked, %d edges created, %d updated",
                        data.get("connections_checked", 0),
                        data.get("edges_created", 0),
                        data.get("edges_updated", 0),
                    )
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
    except Exception as e:
        log.warning("Initial heartbeat failed (will retry via heartbeat loop): %s", e)

    # Core threads — always run
    threading.Thread(target=flush_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=exec_loop, daemon=True).start()
    threading.Thread(target=autoupdate_loop, daemon=True).start()
    threading.Thread(target=network_connections_loop, daemon=True).start()
    log.info("Remote exec + auto-update + network connection reporter enabled")

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
