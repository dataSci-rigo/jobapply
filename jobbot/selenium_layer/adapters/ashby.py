"""
Ashby adapter (Patchright).
Ashby HQ forms are SPA-rendered with React. Fields have aria-label attributes
and work well with the generic extractor. This adapter provides a form-ready
check and section label helper.
"""

import time


ASHBY_HOST = "jobs.ashbyhq.com"


def is_ashby(page) -> bool:
    return ASHBY_HOST in page.url


def wait_for_ashby_form(page, timeout: float = 10_000) -> bool:
    """Wait until the Ashby React form is rendered. Returns True on success."""
    try:
        page.wait_for_selector('form, [role="form"]', timeout=timeout)
        return True
    except Exception:
        return False


def get_ashby_section_labels(page) -> list[str]:
    """Return visible section headings so the user knows where they are."""
    try:
        headings = page.query_selector_all("h2, h3, [class*='section-title']")
        return [(h.inner_text() or "").strip() for h in headings
                if (h.inner_text() or "").strip()]
    except Exception:
        return []
