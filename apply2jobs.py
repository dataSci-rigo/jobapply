#!/usr/bin/env python3
"""Entrypoint: initialise DB, validate data files, launch Flask."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "jobbot"))

import config
from db import store
import data_loader

def main():
    print(f"[jobbot] Initialising DB at {config.DB_PATH}")
    store.init_db()

    print("[jobbot] Validating source files…")
    ready, missing = data_loader.is_ready()
    if ready:
        data = data_loader.load_all()
        print(f"  resume: {data['resume'].get('name')}")
        print(f"  prefs:  {list(data['prefs'].keys())[:4]}…")
        print(f"  star:   {len(data['star'])} stories")
    else:
        print(f"  WARNING: missing data files: {missing}")
        print("  Copy data/*.example.json → data/*.json and fill in your info.")

    print(f"[jobbot] Starting daily scrape scheduler (hour={config.SCRAPE_HOUR}:00)")
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from discovery.scraper import scrape_and_save
        from discovery.scorer import score_pending_leads

        def _daily_scrape():
            try:
                n = scrape_and_save()
                s = score_pending_leads()
                print(f"[scheduler] daily scrape: {n} new leads, {s} scored")
            except Exception as exc:
                print(f"[scheduler] scrape error: {exc}")

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(_daily_scrape, "cron", hour=config.SCRAPE_HOUR, minute=0, id="daily_scrape")
        scheduler.start()
        print(f"[jobbot] Scheduler started — next scrape at {config.SCRAPE_HOUR:02d}:00")
    except ImportError:
        print("[jobbot] apscheduler not installed — daily scrape disabled (run: pip install apscheduler)")

    print(f"[jobbot] Starting Flask on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    from app import app
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        threaded=False,
        use_reloader=False,
    )

if __name__ == "__main__":
    main()
