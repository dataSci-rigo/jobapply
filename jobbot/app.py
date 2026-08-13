"""
JobBot Flask control panel.
Run single-process (threaded=False) to keep the Patchright browser safe.
"""

import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

sys.path.insert(0, str(Path(__file__).parent))

import config
import credentials as cred_store
import data_loader
from db import store
from matching.question_match import (
    find_or_create_question,
    get_previous_answers,
    normalize_company_name,
)
from generation.answers import generate_answer
from generation.resume_tailor import tailor_resume
from generation.cover_letter import generate_cover_letter
from resume.render import render_pdf

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Lazy browser import — Patchright only starts when the first browser route is hit
_driver_module = None


def get_driver():
    global _driver_module
    if _driver_module is None:
        from selenium_layer import driver as _d
        _driver_module = _d
    return _driver_module


# ── Init ───────────────────────────────────────────────────────────────────────

@app.before_request
def _startup():
    store.init_db()
    data_loader.load_all()  # logs warnings for missing files; does not crash


@app.errorhandler(data_loader.DataMissingError)
def _handle_data_missing(exc):
    return (
        f"<h2>Setup needed</h2><p>{exc}</p>"
        "<p>See <code>jobbot/data/*.example.json</code> for templates.</p>",
        503,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    return dict(row)


# ── Intake ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("intake"))


@app.route("/intake", methods=["GET"])
def intake():
    return render_template("intake.html")


@app.route("/intake/submit", methods=["POST"])
def intake_submit():
    data = request.form
    company_name = data.get("company_name", "").strip()
    job_title    = data.get("job_title", "").strip()
    job_url      = data.get("job_url", "").strip()
    ats_platform = data.get("ats_platform", "").strip()
    jd_text      = data.get("jd_text", "").strip()

    if not company_name or not job_title:
        return render_template("intake.html", error="Company name and job title are required.")

    normalized = normalize_company_name(company_name)
    company_id = store.upsert_company(company_name, normalized)
    prev_apps  = store.get_previous_applications_for_company(company_id)

    app_id = store.create_application(
        company_id=company_id,
        job_title=job_title,
        job_url=job_url,
        ats_platform=ats_platform,
        jd_text=jd_text,
    )
    session["current_app_id"] = app_id

    return redirect(url_for("review", app_id=app_id, prior=len(prev_apps)))


# ── Review ─────────────────────────────────────────────────────────────────────

@app.route("/review/<int:app_id>", methods=["GET"])
def review(app_id: int):
    application = _row_to_dict(store.get_application(app_id))
    if not application:
        return "Application not found", 404

    answers = [_row_to_dict(a) for a in store.get_answers_for_application(app_id)]

    rv_id = application.get("resume_version_id")
    resume_version = _row_to_dict(store.get_resume_version(rv_id)) if rv_id else {}

    cl_id = application.get("cover_letter_id")
    cover_letter = _row_to_dict(store.get_cover_letter(cl_id)) if cl_id else {}

    prior = request.args.get("prior", 0, type=int)
    return render_template(
        "review.html",
        app=application,
        answers=answers,
        resume_version=resume_version,
        cover_letter=cover_letter,
        prior_count=prior,
    )


@app.route("/api/generate/resume", methods=["POST"])
def api_generate_resume():
    data = request.json or {}
    app_id = data.get("app_id") or session.get("current_app_id")
    if not app_id:
        return jsonify(error="No app_id"), 400

    application = store.get_application(app_id)
    if not application:
        return jsonify(error="Application not found"), 404

    jd_text = application["jd_text"] or ""
    try:
        tailored = tailor_resume(jd_text)
    except ValueError as exc:
        return jsonify(error=str(exc)), 500

    company_name = application["company_name"]
    job_title    = application["job_title"]
    label        = f"{company_name} – {job_title}"

    pdf_path = render_pdf(tailored, label=label)
    rv_id    = store.save_resume_version(tailored, str(pdf_path), label, application_id=app_id)
    store.link_resume_to_application(app_id, rv_id)

    return jsonify(
        resume_version_id=rv_id,
        pdf_path=str(pdf_path),
        resume_json=tailored,
    )


@app.route("/api/generate/cover_letter", methods=["POST"])
def api_generate_cover_letter():
    data = request.json or {}
    app_id = data.get("app_id") or session.get("current_app_id")
    if not app_id:
        return jsonify(error="No app_id"), 400

    application = store.get_application(app_id)
    if not application:
        return jsonify(error="Application not found"), 404

    text = generate_cover_letter(
        company_name=application["company_name"],
        job_title=application["job_title"],
        jd_text=application["jd_text"] or "",
    )
    cl_id = store.save_cover_letter(text, application_id=app_id)
    store.link_cover_letter_to_application(app_id, cl_id)
    return jsonify(cover_letter_id=cl_id, text=text)


@app.route("/api/generate/answer", methods=["POST"])
def api_generate_answer():
    data = request.json or {}
    app_id        = data.get("app_id") or session.get("current_app_id")
    question_text = data.get("question", "").strip()
    field_type    = data.get("field_type", "textarea")
    char_limit    = int(data.get("char_limit", 0))

    if not app_id or not question_text:
        return jsonify(error="app_id and question required"), 400

    application = store.get_application(app_id)
    if not application:
        return jsonify(error="Application not found"), 404

    question_id = find_or_create_question(question_text, field_type)
    prev_answers = get_previous_answers(question_text, field_type)
    ref_answers  = [r for r in prev_answers if r.get("is_reference")]

    answer_text = generate_answer(
        question=question_text,
        jd_snippet=application["jd_text"] or "",
        field_type=field_type,
        char_limit=char_limit,
        reference_answers=ref_answers or prev_answers[:3],
    )

    answer_id = store.save_answer(app_id, question_id, answer_text, source="generated")
    return jsonify(
        answer_id=answer_id,
        question_id=question_id,
        answer_text=answer_text,
        previous=prev_answers,
    )


@app.route("/api/save/answer", methods=["POST"])
def api_save_answer():
    data = request.json or {}
    app_id        = data.get("app_id") or session.get("current_app_id")
    question_text = data.get("question", "").strip()
    answer_text   = data.get("answer_text", "").strip()
    field_type    = data.get("field_type", "text")
    source        = data.get("source", "manual")

    if not app_id or not question_text:
        return jsonify(error="app_id and question required"), 400

    question_id = find_or_create_question(question_text, field_type)
    answer_id   = store.save_answer(app_id, question_id, answer_text, source=source)
    return jsonify(answer_id=answer_id)


@app.route("/api/update/answer", methods=["POST"])
def api_update_answer():
    data = request.json or {}
    answer_id   = data.get("answer_id")
    answer_text = data.get("answer_text", "").strip()
    if not answer_id:
        return jsonify(error="answer_id required"), 400
    store.update_answer(answer_id, answer_text, source="edited")
    return jsonify(ok=True)


@app.route("/api/cover_letter/update", methods=["POST"])
def api_update_cover_letter():
    data = request.json or {}
    cl_id = data.get("cover_letter_id")
    text  = data.get("text", "")
    if not cl_id:
        return jsonify(error="cover_letter_id required"), 400
    with store.tx() as conn:
        conn.execute("UPDATE cover_letter SET text=? WHERE id=?", (text, cl_id))
    return jsonify(ok=True)


# ── Browser / Push to Form ────────────────────────────────────────────────────

@app.route("/api/browser/scan", methods=["POST"])
def api_browser_scan():
    """Scan the live browser window for form fields."""
    try:
        drv    = get_driver()
        fields = drv.extract_fields()
        return jsonify(fields=fields)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/push", methods=["POST"])
def api_browser_push():
    """Push accepted answers into the live browser window."""
    data  = request.json or {}
    fills = data.get("fills", [])
    if not fills:
        return jsonify(error="No fills provided"), 400
    try:
        drv     = get_driver()
        results = drv.fill_fields(fills)
        return jsonify(results=results)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/navigate", methods=["POST"])
def api_browser_navigate():
    data = request.json or {}
    url  = data.get("url", "")
    if not url:
        return jsonify(error="url required"), 400
    try:
        get_driver().navigate(url)
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/current_url", methods=["GET"])
def api_browser_current_url():
    try:
        return jsonify(url=get_driver().current_url())
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/save_cookies", methods=["POST"])
def api_browser_save_cookies():
    """Persist current browser cookies to disk (call after manual login)."""
    try:
        count = get_driver().save_cookies()
        return jsonify(ok=True, count=count)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


# ── Outcomes ───────────────────────────────────────────────────────────────────

@app.route("/outcomes", methods=["GET"])
def outcomes():
    applications = [_row_to_dict(a) for a in store.list_applications()]
    return render_template("outcomes.html", applications=applications)


@app.route("/outcomes/update_status", methods=["POST"])
def outcomes_update_status():
    data   = request.form
    app_id = int(data.get("app_id", 0))
    status = data.get("status", "")
    if not app_id or status not in ("submitted", "callback", "rejected", "ghosted"):
        return redirect(url_for("outcomes"))
    store.update_application_status(app_id, status)
    return redirect(url_for("outcomes"))


@app.route("/outcomes/submit/<int:app_id>", methods=["POST"])
def mark_submitted(app_id: int):
    store.update_application_status(app_id, "submitted")
    return redirect(url_for("outcomes"))


# ── Dev / Debug ────────────────────────────────────────────────────────────────

# ── Credentials ───────────────────────────────────────────────────────────────

@app.route("/credentials", methods=["GET"])
def credentials_page():
    entries = cred_store.list_credentials()
    return render_template("credentials.html", credentials=entries)


@app.route("/credentials/add", methods=["POST"])
def credentials_add():
    site     = request.form.get("site", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    notes    = request.form.get("notes", "").strip()
    if site and username and password:
        cred_store.add_credential(site, username, password, notes)
    return redirect(url_for("credentials_page"))


@app.route("/credentials/delete", methods=["POST"])
def credentials_delete():
    idx = request.form.get("index", type=int)
    if idx is not None:
        cred_store.delete_credential(idx)
    return redirect(url_for("credentials_page"))


@app.route("/api/browser/login", methods=["POST"])
def api_browser_login():
    """Navigate to a site and auto-fill saved credentials."""
    data = request.json or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify(error="url required"), 400
    try:
        page   = get_driver().get_page()
        result = cred_store.auto_login(page, url)
        status = 200 if result.get("ok") else (404 if "No saved" in result.get("error","") else 500)
        return jsonify(result), status
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/google_login", methods=["POST"])
def api_browser_google_login():
    """Log into a site using Google SSO ('Continue with Google' button)."""
    data = request.json or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify(error="url required"), 400
    try:
        page   = get_driver().get_page()
        result = cred_store.google_sso_login(page, url)
        status = 200 if result.get("ok") else (400 if "not supported" in result.get("error", "") else 500)
        return jsonify(result), status
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/browser/submit_otp", methods=["POST"])
def api_browser_submit_otp():
    """Submit a one-time code on the current page."""
    data = request.json or {}
    otp  = data.get("otp", "").strip()
    url  = data.get("url", "").strip()
    if not otp:
        return jsonify(error="otp required"), 400
    try:
        page   = get_driver().get_page()
        result = cred_store.submit_otp(page, url or page.url, otp)
        return jsonify(result)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


# ── Discovery ──────────────────────────────────────────────────────────────────

@app.route("/discover", methods=["GET"])
def discover():
    active_status = request.args.get("status") or None
    leads = [dict(l) for l in store.list_job_leads(status=active_status)]
    return render_template(
        "discover.html",
        leads=leads,
        active_status=active_status or "",
        scrape_hour=config.SCRAPE_HOUR,
    )


@app.route("/api/discover/run", methods=["POST"])
def api_discover_run():
    try:
        from discovery.scraper import scrape_and_save
        from discovery.scorer import score_pending_leads
        new_leads = scrape_and_save()
        scored = score_pending_leads()
        return jsonify(new_leads=new_leads, scored=scored)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/discover/import/<int:lead_id>", methods=["POST"])
def api_discover_import(lead_id: int):
    try:
        app_id = store.import_lead_as_application(lead_id)
        session["current_app_id"] = app_id
        return jsonify(redirect=url_for("review", app_id=app_id))
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/api/discover/discard/<int:lead_id>", methods=["POST"])
def api_discover_discard(lead_id: int):
    try:
        store.update_lead_status(lead_id, "discarded")
        return jsonify(ok=True)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


# ── Gmail ───────────────────────────────────────────────────────────────────────

@app.route("/gmail", methods=["GET"])
def gmail_setup():
    from gmail.oauth import is_connected, get_last_sync
    creds_file_exists = config.GMAIL_CREDENTIALS_PATH.exists()
    connected = is_connected()
    syncs = [dict(s) for s in store.list_gmail_syncs()] if connected else []
    return render_template(
        "gmail_setup.html",
        connected=connected,
        creds_file_exists=creds_file_exists,
        last_sync=get_last_sync(),
        syncs=syncs,
        sync_days=config.GMAIL_SYNC_DAYS,
    )


@app.route("/gmail/auth", methods=["GET"])
def gmail_auth():
    from gmail.oauth import start_oauth_flow
    auth_url = start_oauth_flow(callback_url=url_for("gmail_callback", _external=True))
    return redirect(auth_url)


@app.route("/gmail/callback", methods=["GET"])
def gmail_callback():
    from gmail.oauth import finish_oauth_flow
    code = request.args.get("code", "")
    if not code:
        return "OAuth cancelled or failed.", 400
    finish_oauth_flow(code, callback_url=url_for("gmail_callback", _external=True))
    return redirect(url_for("gmail_setup"))


@app.route("/api/gmail/sync", methods=["POST"])
def api_gmail_sync():
    from gmail.reader import fetch_recent_recruiter_emails
    from gmail.classifier import classify_email
    try:
        emails = fetch_recent_recruiter_emails(days=config.GMAIL_SYNC_DAYS)
        open_apps = store.get_applications_for_gmail_sync()
        updated = 0
        for email in emails:
            for application in open_apps:
                ctx = f"{application['company_name']} {application['job_title']}"
                result = classify_email(
                    subject=email["subject"],
                    sender=email["sender"],
                    snippet=email["snippet"],
                    application_context=ctx,
                )
                if result["status"] == "irrelevant":
                    continue
                store.save_gmail_sync(
                    application_id=application["id"],
                    message_id=email["message_id"],
                    subject=email["subject"],
                    sender=email["sender"],
                    detected_status=result["status"],
                    confidence=result["confidence"],
                )
                if result["confidence"] >= 0.7:
                    store.update_application_status(application["id"], result["status"])
                    updated += 1
                break  # one email → one application match
        return jsonify(updated=updated)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.route("/gmail/status", methods=["GET"])
def gmail_status():
    from gmail.oauth import is_connected, get_last_sync
    return jsonify(connected=is_connected(), last_sync=get_last_sync())


# ── Dev / Debug ────────────────────────────────────────────────────────────────

@app.route("/api/debug/data", methods=["GET"])
def api_debug_data():
    data = data_loader.load_all()
    return jsonify(
        resume_name=data["resume"].get("name"),
        pref_keys=list(data["prefs"].keys()),
        star_count=len(data["star"]),
    )


if __name__ == "__main__":
    store.init_db()
    ready, missing = data_loader.is_ready()
    if not ready:
        print(f"[jobbot] WARNING: missing data files: {missing}")
        print("[jobbot] Copy data/*.example.json to data/*.json and fill in your info.")
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        threaded=False,   # REQUIRED: single-process for Patchright browser persistence
        use_reloader=False,
    )
