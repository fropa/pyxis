"""Generic outbound webhook — works with PagerDuty, OpsGenie, custom endpoints."""
import httpx
from app.models.incident import Incident


async def send_webhook(url: str, headers: dict, incident: Incident) -> None:
    payload = {
        "event_type": "incident_opened",
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "started_at": incident.started_at.isoformat(),
            "rca_summary": incident.rca_summary,
            "rca_confidence": incident.rca_confidence,
            "cited_files": incident.cited_knowledge,
        },
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
