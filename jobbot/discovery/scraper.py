"""
jobspy wrapper: scrape job boards and persist new leads.
Returns the count of newly saved leads.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import data_loader
from db import store

import logging
logger = logging.getLogger(__name__)


def scrape_and_save() -> int:
    """Run jobspy for each configured search term; deduplicate by URL; persist new leads."""
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        raise RuntimeError("jobspy is not installed — run: pip install jobspy")

    prefs = data_loader.load_all()["prefs"]
    location = prefs.get("location", "") or config.JOBSPY_LOCATION
    is_remote = bool(prefs.get("open_to_remote", False))

    existing_urls = store.get_existing_lead_urls()
    saved = 0

    search_terms = [t.strip() for t in config.JOBSPY_SEARCH_TERMS.split(",") if t.strip()]

    for term in search_terms:
        try:
            df = scrape_jobs(
                site_name=config.JOBSPY_SITE_NAMES,
                search_term=term,
                location=location,
                results_wanted=config.JOBSPY_RESULTS_WANTED,
                is_remote=is_remote,
                linkedin_fetch_description=True,
            )
        except Exception as exc:
            logger.warning("jobspy scrape failed for %r: %s", term, exc)
            continue

        for _, row in df.iterrows():
            url = str(row.get("job_url") or "").strip()
            if not url or url in existing_urls:
                continue

            description = str(row.get("description") or "")
            snippet = description[:600] if description else ""
            raw = {
                k: (v if not hasattr(v, "isoformat") else v.isoformat())
                for k, v in row.items()
                if v is not None and str(v) != "nan"
            }

            lead_id = store.save_job_lead(
                source="jobspy",
                title=str(row.get("title") or ""),
                company_name=str(row.get("company") or ""),
                location=str(row.get("location") or ""),
                url=url,
                jd_snippet=snippet,
                raw_json=raw,
            )
            if lead_id is not None:
                existing_urls.add(url)
                saved += 1

    logger.info("scraper: saved %d new leads", saved)
    return saved
