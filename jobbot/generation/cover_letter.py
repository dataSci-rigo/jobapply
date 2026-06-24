"""Generate a cover letter for a job application."""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from generation.client import generate

_TEMPLATE = (Path(__file__).parent / "prompts" / "cover_letter.txt").read_text()


def generate_cover_letter(
    company_name: str,
    job_title: str,
    jd_text: str,
) -> str:
    prompt = _TEMPLATE.format(
        company_name=company_name,
        job_title=job_title,
        jd_text=jd_text[:6000],
    )
    return generate(prompt, max_tokens=1024)
