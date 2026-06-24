"""Render a tailored resume dict to PDF using WeasyPrint + Jinja2."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_TEMPLATE_DIR = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)


def render_pdf(resume_dict: dict, label: str = "") -> Path:
    """Render resume_dict to a PDF file and return its absolute path."""
    config.PDF_DIR.mkdir(parents=True, exist_ok=True)

    template = _env.get_template("template.html")
    html_str = template.render(r=resume_dict)

    # Deterministic filename: hash of content so identical resumes share a file
    content_hash = hashlib.sha256(json.dumps(resume_dict, sort_keys=True).encode()).hexdigest()[:10]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = label.replace(" ", "_")[:30] if label else "resume"
    filename = f"{slug}_{timestamp}_{content_hash}.pdf"
    pdf_path = config.PDF_DIR / filename

    HTML(string=html_str, base_url=str(_TEMPLATE_DIR)).write_pdf(str(pdf_path))
    return pdf_path
