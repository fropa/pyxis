"""
Cross-source correlator.

Problem: A CI/CD pipeline fails but the pipeline logs show no clear error.
The real failure is in K8s: missing secret, ImagePullBackOff, CrashLoopBackOff, etc.
The correlator gathers context from ALL sources related to the same service
within a time window so the AI engine has the full picture.

More generally: for any incident from any source, find related events
from other sources by matching on service name, image name, namespace, and time.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import LogEvent
from app.models.topology import Node
from app.models.deploy_event import DeployEvent


# Time window: look ±10 minutes around the trigger event
CORRELATION_WINDOW_MINUTES = 10

# Signals that mean "pipeline tried to deploy something"
PIPELINE_DEPLOY_SIGNALS = [
    "kubectl apply",
    "helm upgrade",
    "helm install",
    "helm deploy",
    "deploy",
    "applying manifest",
    "rolling update",
    "image:",
    "docker push",
    "push",
]

# K8s signals that are typically the REAL cause when a deploy fails silently
K8S_FAILURE_SIGNALS = [
    "ImagePullBackOff",
    "ErrImagePull",
    "CrashLoopBackOff",
    "OOMKilled",
    "Evicted",
    "FailedScheduling",
    "FailedMount",
    "secret",           # "secret not found", "couldn't find secret"
    "configmap",        # same pattern for configmaps
    "forbidden",
    "unauthorized",
    "permission denied",
    "no space left",
    "Readiness probe failed",
    "Liveness probe failed",
    "Back-off pulling image",
    "failed to pull",
]


async def gather_correlated_context(
    trigger_event: LogEvent,
    tenant_id: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Given a trigger event, return a dict with:
    - pipeline_events: CI/CD events from the same time window
    - k8s_events: K8s events that may explain the failure
    - service_name: extracted service/deployment name
    - correlation_notes: human-readable notes about what was found
    """
    ts = trigger_event.event_ts
    window_start = ts - timedelta(minutes=CORRELATION_WINDOW_MINUTES)
    window_end = ts + timedelta(minutes=CORRELATION_WINDOW_MINUTES)

    # Extract service/image name from the trigger event
    service_name = _extract_service_name(trigger_event)
    image_name = _extract_image_name(trigger_event)

    # Fetch events from all sources in the time window
    result = await db.execute(
        select(LogEvent).where(
            LogEvent.tenant_id == tenant_id,
            LogEvent.event_ts >= window_start,
            LogEvent.event_ts <= window_end,
            LogEvent.id != trigger_event.id,
        ).order_by(LogEvent.event_ts)
    )
    all_events = result.scalars().all()

    pipeline_events = [e for e in all_events if e.source == "ci_pipeline"]
    k8s_events = [e for e in all_events if e.source == "k8s_event"]
    syslog_events = [e for e in all_events if e.source == "syslog"]

    # If trigger is a pipeline failure, specifically look for K8s events
    # matching the service name or known failure patterns
    k8s_root_cause_events = []
    if trigger_event.source == "ci_pipeline":
        k8s_root_cause_events = _find_k8s_root_cause(
            k8s_events, service_name, image_name
        )

    # If trigger is K8s, look for pipeline that triggered it
    related_pipeline_events = []
    if trigger_event.source == "k8s_event":
        related_pipeline_events = _find_related_pipeline(pipeline_events, service_name, image_name)

    # Build correlation notes
    notes = _build_correlation_notes(
        trigger_event=trigger_event,
        service_name=service_name,
        image_name=image_name,
        k8s_root_cause_events=k8s_root_cause_events,
        related_pipeline_events=related_pipeline_events,
    )

    # Check for recent deploys within ±30 min of the incident
    deploy_window_start = ts - timedelta(minutes=30)
    deploy_window_end = ts + timedelta(minutes=5)
    deploy_result = await db.execute(
        select(DeployEvent).where(
            DeployEvent.tenant_id == tenant_id,
            DeployEvent.deployed_at >= deploy_window_start,
            DeployEvent.deployed_at <= deploy_window_end,
        ).order_by(DeployEvent.deployed_at.desc()).limit(10)
    )
    recent_deploys = deploy_result.scalars().all()
    deploy_context = _format_deploy_events(recent_deploys, service_name)

    if recent_deploys and not notes:
        notes = _build_correlation_notes(
            trigger_event=trigger_event,
            service_name=service_name,
            image_name=image_name,
            k8s_root_cause_events=k8s_root_cause_events,
            related_pipeline_events=related_pipeline_events,
        )
    if recent_deploys:
        deploy_note = (
            f"\nDEPLOYMENT CORRELATION: {len(recent_deploys)} deployment(s) detected within 30 min before this incident: "
            + ", ".join(f"{d.service}@{d.version or 'unknown'}" for d in recent_deploys)
        )
        notes = (notes or "") + deploy_note

    return {
        "service_name": service_name,
        "image_name": image_name,
        "pipeline_events": _format_events(pipeline_events[:20]),
        "k8s_events": _format_events(k8s_events[:20]),
        "syslog_events": _format_events(syslog_events[:10]),
        "k8s_root_cause_events": _format_events(k8s_root_cause_events),
        "related_pipeline_events": _format_events(related_pipeline_events),
        "recent_deploys": deploy_context,
        "correlation_notes": notes,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def _extract_service_name(event: LogEvent) -> str | None:
    """Try to extract a service/deployment name from the event."""
    text = event.message or ""
    parsed = event.parsed or {}

    # From K8s event involvedObject
    if "involvedObject" in parsed:
        name = parsed["involvedObject"].get("name", "")
        if name:
            # Strip pod hash suffixes: my-service-6f9b4d-xyz → my-service
            return re.sub(r"-[a-f0-9]{5,10}-[a-z0-9]{5}$", "", name)

    # From pipeline: look for "deploy <name>", "helm upgrade <name>", "service: <name>"
    patterns = [
        r"helm\s+(?:upgrade|install)\s+(\S+)",
        r"kubectl.*(?:deploy|apply).*?(\S+)",
        r"service[:\s]+([a-z0-9-]+)",
        r"deployment[:\s]+([a-z0-9-]+)",
        r"app[:\s]+([a-z0-9-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)

    return None


def _extract_image_name(event: LogEvent) -> str | None:
    """Extract Docker image name from event text."""
    text = event.message or ""
    parsed = event.parsed or {}

    # K8s events often include the image name
    m = re.search(r"image[:\s]+\"?([a-z0-9./:-]+)\"?", text, re.IGNORECASE)
    if m:
        return m.group(1)

    # Pipeline logs
    m = re.search(r"(?:docker pull|pulling image)[:\s]+\"?([a-z0-9./:-]+)\"?", text, re.IGNORECASE)
    if m:
        return m.group(1)

    return None


def _find_k8s_root_cause(
    k8s_events: list[LogEvent],
    service_name: str | None,
    image_name: str | None,
) -> list[LogEvent]:
    """
    Find K8s events that are likely the root cause of a pipeline failure.
    Prioritizes: ImagePullBackOff, missing secrets, CrashLoopBackOff.
    """
    hits = []
    for e in k8s_events:
        text = e.message or ""
        parsed = e.parsed or {}

        # Service name match
        name_match = False
        if service_name:
            k8s_name = parsed.get("involvedObject", {}).get("name", "")
            if service_name.lower() in k8s_name.lower() or k8s_name.lower() in service_name.lower():
                name_match = True

        # Image name match
        img_match = image_name and image_name.split(":")[0].split("/")[-1] in text.lower()

        # Known failure pattern
        failure_match = any(sig.lower() in text.lower() for sig in K8S_FAILURE_SIGNALS)

        if failure_match or name_match or img_match:
            hits.append(e)

    # Sort by relevance: failure signals first
    return sorted(
        hits,
        key=lambda e: any(sig.lower() in (e.message or "").lower() for sig in K8S_FAILURE_SIGNALS),
        reverse=True,
    )


def _find_related_pipeline(
    pipeline_events: list[LogEvent],
    service_name: str | None,
    image_name: str | None,
) -> list[LogEvent]:
    """Find pipeline events that deployed the service that is now failing in K8s."""
    hits = []
    for e in pipeline_events:
        text = e.message or ""
        if service_name and service_name.lower() in text.lower():
            hits.append(e)
        elif image_name and image_name.split(":")[0].split("/")[-1] in text.lower():
            hits.append(e)
        elif any(sig.lower() in text.lower() for sig in PIPELINE_DEPLOY_SIGNALS):
            hits.append(e)
    return hits


def _build_correlation_notes(
    trigger_event: LogEvent,
    service_name: str | None,
    image_name: str | None,
    k8s_root_cause_events: list[LogEvent],
    related_pipeline_events: list[LogEvent],
) -> str:
    lines = []

    if trigger_event.source == "ci_pipeline":
        lines.append(
            "CORRELATION: This incident was triggered by a CI/CD pipeline event. "
            "The pipeline logs may not show the root cause directly. "
            "Checking K8s cluster events from the same time window."
        )
        if k8s_root_cause_events:
            lines.append(
                f"FOUND {len(k8s_root_cause_events)} K8s event(s) that may explain the failure:"
            )
            for e in k8s_root_cause_events[:5]:
                lines.append(f"  - [{e.event_ts.isoformat()}] {e.message}")
        else:
            lines.append(
                "No directly correlated K8s failure events found in the time window. "
                "Check: pod describe output, secret existence, image registry access."
            )

    elif trigger_event.source == "k8s_event":
        if related_pipeline_events:
            lines.append(
                f"CORRELATION: Found {len(related_pipeline_events)} related CI/CD pipeline event(s) "
                "that may have triggered this K8s failure:"
            )
            for e in related_pipeline_events[:3]:
                lines.append(f"  - [{e.event_ts.isoformat()}] {e.message}")

    if service_name:
        lines.append(f"IDENTIFIED SERVICE: {service_name}")
    if image_name:
        lines.append(f"IDENTIFIED IMAGE: {image_name}")

    # Specific guidance for known failure patterns
    all_k8s_text = " ".join(e.message or "" for e in k8s_root_cause_events)
    if "ImagePullBackOff" in all_k8s_text or "ErrImagePull" in all_k8s_text:
        lines.append(
            "HINT: ImagePullBackOff detected. Check: "
            "(1) image registry credentials / imagePullSecrets in the deployment, "
            "(2) image tag exists in the registry, "
            "(3) network access to the registry from the node."
        )
    if "secret" in all_k8s_text.lower() and "not found" in all_k8s_text.lower():
        lines.append(
            "HINT: Missing Kubernetes secret. Check if the secret referenced in "
            "the pod spec (env.valueFrom.secretKeyRef or volumes.secret) exists in the namespace."
        )
    if "CrashLoopBackOff" in all_k8s_text:
        lines.append(
            "HINT: CrashLoopBackOff. The container starts but immediately exits. "
            "Check: (1) application startup errors in pod logs, "
            "(2) missing env vars or config, (3) readiness/liveness probe configuration."
        )

    return "\n".join(lines)


def _format_deploy_events(events: list[DeployEvent], service_name: str | None) -> list[dict[str, Any]]:
    return [
        {
            "ts": e.deployed_at.isoformat(),
            "service": e.service,
            "version": e.version,
            "deployed_by": e.deployed_by,
            "environment": e.environment,
            "likely_related": service_name is not None and service_name.lower() in e.service.lower(),
        }
        for e in events
    ]


def _format_events(events: list[LogEvent]) -> list[dict[str, Any]]:
    return [
        {
            "ts": e.event_ts.isoformat(),
            "source": e.source,
            "level": e.level,
            "message": e.message,
            "node": e.node_id,
        }
        for e in events
    ]
