"""
Score job leads 0–100 using Claude Haiku against the user's preferences.
"""

import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import data_loader
from db import store
from generation.client import get_client

import logging
logger = logging.getLogger(__name__)

_SCORE_PROMPT = """\
You are evaluating job fit for a candidate. Score this job lead from 0 to 100 based on how well it matches the candidate's preferences and background.

Candidate preferences:
{prefs}

Job lead:
Title: {title}
Company: {company}
Location: {location}
Description snippet: {snippet}

Respond with ONLY valid JSON on one line: {{"score": <int 0-100>, "reason": "<one sentence>"}}
Higher scores mean stronger fit. Score 0 if the role is clearly outside the candidate's field."""


def score_lead(title: str, company: str, location: str, jd_snippet: str) -> tuple[int, str]:
    """Return (score 0-100, reason string) for a single lead."""
    prefs = data_loader.load_all()["prefs"]
    prompt = _SCORE_PROMPT.format(
        prefs=json.dumps(prefs, indent=2)[:800],
        title=title,
        company=company,
        location=location,
        snippet=jd_snippet[:500],
    )
    client = get_client()
    resp = client.messages.create(
        model=config.MATCHING_MODEL,
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    try:
        parsed = json.loads(raw)
        return int(parsed.get("score", 0)), str(parsed.get("reason", ""))
    except (json.JSONDecodeError, KeyError, ValueError):
        # Fallback: extract first number found
        m = re.search(r"\d+", raw)
        return (int(m.group()) if m else 0), raw[:120]


def score_pending_leads() -> int:
    """Score all pending unscored leads; returns count scored."""
    leads = store.list_job_leads(status="pending")
    unscored = [l for l in leads if l["ai_score"] is None]
    count = 0
    for lead in unscored:
        try:
            score, _ = score_lead(
                title=lead["title"] or "",
                company=lead["company_name"] or "",
                location=lead["location"] or "",
                jd_snippet=lead["jd_snippet"] or "",
            )
            store.update_lead_score(lead["id"], score)
            count += 1
        except Exception as exc:
            logger.warning("scoring failed for lead %d: %s", lead["id"], exc)
    logger.info("scorer: scored %d leads", count)
    return count
