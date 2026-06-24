"""Tailor the master resume JSON to a specific job description."""

import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import data_loader
from generation.client import generate

_TEMPLATE = (Path(__file__).parent / "prompts" / "resume_tailor.txt").read_text()


def tailor_resume(jd_text: str) -> dict:
    """Return a tailored resume dict. Raises ValueError if the model returns invalid JSON."""
    prompt = _TEMPLATE.format(jd_text=jd_text[:6000])
    raw = generate(prompt, max_tokens=4096)

    # Strip any accidental markdown fences the model may have added
    cleaned = re.sub(r"^```[a-z]*\n?|```$", "", raw.strip(), flags=re.MULTILINE).strip()

    try:
        tailored = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {exc}\n---\n{cleaned[:500]}") from exc

    # Merge missing top-level keys from master to prevent dropping non-tailored sections
    master = data_loader.get_resume()
    for key in master:
        if key not in tailored:
            tailored[key] = master[key]

    return tailored
