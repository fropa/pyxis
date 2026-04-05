"""Send incident notifications to Slack via incoming webhook."""
import httpx
from app.models.incident import Incident

SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "high":     ":large_orange_circle:",
    "medium":   ":large_yellow_circle:",
    "low":      ":white_circle:",
}


async def send_slack(webhook_url: str, incident: Incident) -> None:
    emoji = SEVERITY_EMOJI.get(incident.severity, ":large_yellow_circle:")
    summary = incident.rca_summary or "AI analysis in progress..."

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Incident: {incident.title[:80]}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{incident.severity.upper()}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{incident.status}"},
                    {"type": "mrkdwn", "text": f"*Started:*\n{incident.started_at.strftime('%Y-%m-%d %H:%M UTC')}"},
                    {"type": "mrkdwn", "text": f"*Confidence:*\n{int((incident.rca_confidence or 0) * 100)}%"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{summary[:500]}"},
            },
        ]
    }

    if incident.cited_knowledge:
        files = "\n".join(f"• `{f}`" for f in incident.cited_knowledge[:5])
        payload["blocks"].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*IaC files referenced:*\n{files}"},
        })

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
