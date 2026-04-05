"""
Log Anomaly Playground — ad-hoc Claude analysis without needing an agent installed.
"""
import re

import anthropic
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.deps import get_current_tenant
from app.models.tenant import Tenant

router = APIRouter()
settings = get_settings()
_anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


class LogAnalysisRequest(BaseModel):
    logs: str
    context: str | None = None


class LogAnalysisResponse(BaseModel):
    analysis: str
    confidence: float


@router.post("/logs", response_model=LogAnalysisResponse)
async def analyze_logs(
    payload: LogAnalysisRequest,
    tenant: Tenant = Depends(get_current_tenant),
):
    context_block = f"\n\n## Additional Context\n{payload.context}" if payload.context else ""

    user_prompt = f"""Analyze the following logs for anomalies, errors, and potential root causes.

## Logs
```
{payload.logs[:10000]}
```
{context_block}

Provide a structured analysis with these sections:
1. **Summary** — what's happening in these logs (2-3 sentences)
2. **Anomalies Detected** — specific errors, spikes, or unusual patterns (cite exact log lines)
3. **Root Cause Hypothesis** — most likely cause based on the evidence
4. **Recommended Actions** — immediate steps to investigate or fix
5. **Confidence** — your confidence level (0-100%) and what additional data would help"""

    message = await _anthropic.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2048,
        system=(
            "You are an expert SRE analyzing infrastructure logs. "
            "Be specific, cite exact log lines, and give actionable recommendations. "
            "Format your response in Markdown."
        ),
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = message.content[0].text
    m = re.search(r"confidence[:\s]+(\d+)%?", text, re.IGNORECASE)
    confidence = min(int(m.group(1)), 100) / 100.0 if m else 0.7

    return LogAnalysisResponse(analysis=text, confidence=confidence)
