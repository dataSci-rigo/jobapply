"""
Classify a recruiter email against a specific application using Claude Haiku.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from generation.client import get_client

_CLASSIFY_PROMPT = """\
You are classifying a recruiter email to determine the outcome for a job application.

Application context: {context}

Email from: {sender}
Subject: {subject}
Body snippet: {snippet}

Classify this email into exactly one category:
- callback: Positive response — interview invite, screening call request, offer, or next steps.
- rejected: Rejection — application not moving forward, position filled, not a fit.
- ghosted: Automated acknowledgement with no action or a non-committal "we'll be in touch".
- irrelevant: Unrelated to this specific application (different company, newsletter, spam).

Respond ONLY with valid JSON on one line: {{"status": "<category>", "confidence": <0.0-1.0>}}"""


def classify_email(subject: str, sender: str, snippet: str,
                   application_context: str) -> dict:
    """Return {status, confidence} for a single email against an application."""
    prompt = _CLASSIFY_PROMPT.format(
        context=application_context[:200],
        sender=sender[:120],
        subject=subject[:200],
        snippet=snippet[:400],
    )
    client = get_client()
    resp = client.messages.create(
        model=config.MATCHING_MODEL,
        max_tokens=64,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        status = parsed.get("status", "irrelevant")
        if status not in ("callback", "rejected", "ghosted", "irrelevant"):
            status = "irrelevant"
        confidence = float(parsed.get("confidence", 0.5))
        return {"status": status, "confidence": confidence}
    except (json.JSONDecodeError, ValueError):
        return {"status": "irrelevant", "confidence": 0.0}
