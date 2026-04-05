"""
Raw log line → structured fields.
Each source type has its own parser.
"""
import re
from datetime import datetime, timezone
from typing import Any


# ── syslog (RFC 3164 / 5424) ──────────────────────────────────────────────────

_SYSLOG_RE = re.compile(
    r"(?P<month>\w{3})\s+(?P<day>\d+)\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<process>\S+?)(?:\[(?P<pid>\d+)\])?\s*:\s*(?P<msg>.*)"
)


def parse_syslog(raw: str) -> dict[str, Any]:
    m = _SYSLOG_RE.match(raw)
    if not m:
        return {"message": raw}
    return {
        "host": m.group("host"),
        "process": m.group("process"),
        "pid": m.group("pid"),
        "message": m.group("msg"),
    }


# ── Kubernetes event ──────────────────────────────────────────────────────────

def parse_k8s_event(raw: str, pre_parsed: dict[str, Any]) -> dict[str, Any]:
    """
    K8s events come pre-parsed from kubectl / client-go.
    We just normalise the fields we care about.
    """
    return {
        "kind": pre_parsed.get("involvedObject", {}).get("kind"),
        "name": pre_parsed.get("involvedObject", {}).get("name"),
        "namespace": pre_parsed.get("involvedObject", {}).get("namespace"),
        "reason": pre_parsed.get("reason"),
        "message": pre_parsed.get("message", raw),
        "type": pre_parsed.get("type"),   # Normal | Warning
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
    return {"message": clean, "inferred_level": level}


# ── Generic JSON log ─────────────────────────────────────────────────────────

def parse_generic(raw: str, pre_parsed: dict[str, Any]) -> dict[str, Any]:
    result = {"message": raw}
    # Use whatever the agent already parsed
    if pre_parsed:
        result.update(pre_parsed)
    return result


# ── Dispatch ─────────────────────────────────────────────────────────────────

def parse(source: str, raw: str, pre_parsed: dict[str, Any]) -> dict[str, Any]:
    if source == "syslog":
        return parse_syslog(raw)
    if source == "k8s_event":
        return parse_k8s_event(raw, pre_parsed)
    if source == "ci_pipeline":
        return parse_pipeline_log(raw)
    return parse_generic(raw, pre_parsed)
