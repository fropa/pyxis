"""
Error fingerprinter.

Normalizes variable parts of log messages into a stable signature.
Two log lines that mean the same thing produce the same fingerprint,
regardless of pod hash, IP address, timestamp, UUID, or counter.

Example:
  "pod/my-app-6f9b4d-abc CrashLoopBackOff (13 times)"
  "pod/my-app-7c8e2f-xyz CrashLoopBackOff (27 times)"
  → both produce: "k8s:my-app:CrashLoopBackOff"

The fingerprint is used for:
  1. Incident deduplication (same fingerprint = same incident)
  2. Rate window keys in Redis
  3. Pattern matching against known failure library
"""
import hashlib
import re


# ── Substitution rules applied in order ──────────────────────────────────────

_RULES: list[tuple[re.Pattern, str]] = [
    # Timestamps
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"), "<ts>"),
    # UUIDs
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    # IPv4 + port
    (re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?"), "<ip>"),
    # IPv6
    (re.compile(r"([0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}", re.I), "<ipv6>"),
    # K8s pod hash suffixes: my-app-6f9b4d-abc → my-app
    (re.compile(r"-[a-f0-9]{5,10}-[a-z0-9]{4,6}\b"), ""),
    # Counters in parentheses: "(13 times)", "(restart count: 5)"
    (re.compile(r"\(\d+\s*(?:times?|restarts?|retries|count[s]?(?:\s*:\s*\d+)?)\)", re.I), "(<N>)"),
    # Bare numbers that are likely counters/ports/pids (keep things like error codes)
    (re.compile(r"\b\d{5,}\b"), "<N>"),
    # File descriptor numbers, exit codes
    (re.compile(r"\bfd\s*\d+\b", re.I), "fd<N>"),
    (re.compile(r"\bexit(?:\s+code)?\s*[=:]?\s*\d+\b", re.I), "exit(<N>)"),
    # Git SHAs
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<sha>"),
    # Paths with variable segments (keep the last meaningful part)
    (re.compile(r"(?:/[a-z0-9_.-]+){4,}"), "<path>"),
    # Extra whitespace
    (re.compile(r"\s{2,}"), " "),
]

# High-signal K8s reason words — kept verbatim in the fingerprint
_K8S_REASONS = {
    "CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull",
    "Evicted", "Preempting", "FailedScheduling", "FailedMount",
    "NodeNotReady", "NodeReady", "Killing", "BackOff",
    "Readiness", "Liveness", "FailedCreate", "SuccessfulCreate",
}

# Source-specific prefix for the fingerprint
_SOURCE_PREFIX = {
    "k8s_event": "k8s",
    "syslog": "sys",
    "ci_pipeline": "ci",
    "app_log": "app",
}


def fingerprint(source: str, message: str, parsed: dict | None = None) -> str:
    """
    Return a stable short fingerprint for a log event.
    Format: "<prefix>:<normalized_message_hash>"
    with a human-readable prefix when we can extract one.
    """
    prefix = _SOURCE_PREFIX.get(source, "log")

    # For K8s events, use the reason as the primary signal
    if source == "k8s_event" and parsed:
        reason = parsed.get("reason", "")
        obj_name = parsed.get("involvedObject", {}).get("name", "")
        # Strip pod suffix from name
        obj_name = re.sub(r"-[a-f0-9]{5,10}-[a-z0-9]{4,6}$", "", obj_name)
        if reason:
            return f"k8s:{obj_name}:{reason}" if obj_name else f"k8s:{reason}"

    # Normalize the message
    normalized = _normalize(message)

    # For short normalized messages, use them directly (readable)
    if len(normalized) <= 80:
        return f"{prefix}:{normalized}"

    # Long messages: use a hash for the tail but keep a readable prefix
    readable = normalized[:60].rstrip()
    h = hashlib.sha1(normalized.encode()).hexdigest()[:8]
    return f"{prefix}:{readable}:{h}"


def _normalize(text: str) -> str:
    result = text
    for pattern, replacement in _RULES:
        result = pattern.sub(replacement, result)
    return result.strip().lower()
