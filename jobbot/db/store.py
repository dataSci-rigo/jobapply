"""SQLite access layer. All mutations go through this module."""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_SCHEMA = Path(__file__).parent / "schema.sql"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Module-level connection reused within a single process (Flask single-process).
_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(_SCHEMA.read_text())
    conn.commit()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Company ────────────────────────────────────────────────────────────────────

def upsert_company(name: str, normalized_name: str, website: str = "", notes: str = "") -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO company (name, normalized_name, website, notes) VALUES (?,?,?,?)"
            " ON CONFLICT(normalized_name) DO UPDATE SET name=excluded.name,"
            " website=COALESCE(excluded.website, website),"
            " notes=COALESCE(excluded.notes, notes)"
            " RETURNING id",
            (name, normalized_name, website, notes),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        return get_conn().execute(
            "SELECT id FROM company WHERE normalized_name=?", (normalized_name,)
        ).fetchone()["id"]


def get_company_by_normalized(normalized_name: str) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM company WHERE normalized_name=?", (normalized_name,)
    ).fetchone()


def list_companies() -> list:
    return get_conn().execute("SELECT * FROM company ORDER BY name").fetchall()


# ── Application ────────────────────────────────────────────────────────────────

def create_application(company_id: int, job_title: str, job_url: str = "",
                        ats_platform: str = "", jd_text: str = "") -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO application (company_id, job_title, job_url, ats_platform, jd_text)"
            " VALUES (?,?,?,?,?) RETURNING id",
            (company_id, job_title, job_url, ats_platform, jd_text),
        )
        return cur.fetchone()["id"]


def get_application(app_id: int) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT a.*, c.name as company_name, c.normalized_name"
        " FROM application a JOIN company c ON c.id=a.company_id"
        " WHERE a.id=?", (app_id,)
    ).fetchone()


def list_applications(limit: int = 100) -> list:
    return get_conn().execute(
        "SELECT a.*, c.name as company_name FROM application a"
        " JOIN company c ON c.id=a.company_id"
        " ORDER BY a.created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def update_application_status(app_id: int, status: str) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE application SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, app_id),
        )
        if status == "callback":
            # Promote all answers for this application to reference material
            conn.execute(
                "UPDATE answer SET is_reference=1 WHERE application_id=?", (app_id,)
            )
        if status == "submitted":
            conn.execute(
                "UPDATE application SET submitted_at=datetime('now') WHERE id=?", (app_id,)
            )


def link_resume_to_application(app_id: int, resume_version_id: int) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE application SET resume_version_id=?, updated_at=datetime('now') WHERE id=?",
            (resume_version_id, app_id),
        )


def link_cover_letter_to_application(app_id: int, cover_letter_id: int) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE application SET cover_letter_id=?, updated_at=datetime('now') WHERE id=?",
            (cover_letter_id, app_id),
        )


def get_previous_applications_for_company(company_id: int) -> list:
    return get_conn().execute(
        "SELECT * FROM application WHERE company_id=? AND status != 'draft'"
        " ORDER BY created_at DESC",
        (company_id,),
    ).fetchall()


# ── Question & Answer ──────────────────────────────────────────────────────────

def upsert_question(question_text: str, normalized_text: str,
                    question_hash: str, field_type: str = "text") -> int:
    with tx() as conn:
        conn.execute(
            "INSERT INTO question (question_text, normalized_text, question_hash, field_type)"
            " VALUES (?,?,?,?) ON CONFLICT(question_hash) DO NOTHING",
            (question_text, normalized_text, question_hash, field_type),
        )
    row = get_conn().execute(
        "SELECT id FROM question WHERE question_hash=?", (question_hash,)
    ).fetchone()
    return row["id"]


def get_question_by_hash(question_hash: str) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM question WHERE question_hash=?", (question_hash,)
    ).fetchone()


def list_all_questions() -> list:
    return get_conn().execute("SELECT * FROM question").fetchall()


def save_answer(application_id: int, question_id: int, answer_text: str,
                source: str = "generated") -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO answer (application_id, question_id, answer_text, source)"
            " VALUES (?,?,?,?) RETURNING id",
            (application_id, question_id, answer_text, source),
        )
        return cur.fetchone()["id"]


def get_answers_for_application(app_id: int) -> list:
    return get_conn().execute(
        "SELECT ans.*, q.question_text, q.field_type"
        " FROM answer ans JOIN question q ON q.id=ans.question_id"
        " WHERE ans.application_id=?", (app_id,)
    ).fetchall()


def get_previous_answers_for_question(question_id: int, limit: int = 5) -> list:
    """Return past answers for a question, reference answers first."""
    return get_conn().execute(
        "SELECT ans.*, a.job_title, c.name as company_name"
        " FROM answer ans"
        " JOIN application a ON a.id=ans.application_id"
        " JOIN company c ON c.id=a.company_id"
        " WHERE ans.question_id=? AND a.status != 'draft'"
        " ORDER BY ans.is_reference DESC, ans.created_at DESC LIMIT ?",
        (question_id, limit),
    ).fetchall()


def get_reference_answers(limit: int = 20) -> list:
    """All is_reference=1 answers, for use as generation exemplars."""
    return get_conn().execute(
        "SELECT ans.answer_text, q.question_text"
        " FROM answer ans JOIN question q ON q.id=ans.question_id"
        " WHERE ans.is_reference=1 ORDER BY ans.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def update_answer(answer_id: int, answer_text: str, source: str = "edited") -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE answer SET answer_text=?, source=? WHERE id=?",
            (answer_text, source, answer_id),
        )


# ── Resume version ─────────────────────────────────────────────────────────────

def save_resume_version(resume_json: dict, pdf_path: str = "",
                         label: str = "", application_id: Optional[int] = None) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO resume_version (application_id, resume_json, pdf_path, label)"
            " VALUES (?,?,?,?) RETURNING id",
            (application_id, json.dumps(resume_json), pdf_path, label),
        )
        return cur.fetchone()["id"]


def get_resume_version(rv_id: int) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM resume_version WHERE id=?", (rv_id,)
    ).fetchone()


def get_resume_versions_for_company(company_id: int) -> list:
    return get_conn().execute(
        "SELECT rv.* FROM resume_version rv"
        " JOIN application a ON a.id=rv.application_id"
        " WHERE a.company_id=? ORDER BY rv.created_at DESC",
        (company_id,),
    ).fetchall()


# ── Cover letter ───────────────────────────────────────────────────────────────

def save_cover_letter(text: str, pdf_path: str = "",
                       application_id: Optional[int] = None) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO cover_letter (application_id, text, pdf_path)"
            " VALUES (?,?,?) RETURNING id",
            (application_id, text, pdf_path),
        )
        return cur.fetchone()["id"]


def get_cover_letter(cl_id: int) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM cover_letter WHERE id=?", (cl_id,)
    ).fetchone()


# ── Job leads ──────────────────────────────────────────────────────────────────

def save_job_lead(source: str, title: str, company_name: str, location: str,
                  url: str, jd_snippet: str, raw_json: dict) -> Optional[int]:
    """Insert a new lead; returns id, or None if URL already exists."""
    with tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO job_lead"
            " (source, title, company_name, location, job_url, jd_snippet, raw_json)"
            " VALUES (?,?,?,?,?,?,?) RETURNING id",
            (source, title, company_name, location, url, jd_snippet[:600],
             json.dumps(raw_json)),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def update_lead_score(lead_id: int, score: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE job_lead SET ai_score=? WHERE id=?", (score, lead_id))


def update_lead_status(lead_id: int, status: str) -> None:
    with tx() as conn:
        conn.execute("UPDATE job_lead SET status=? WHERE id=?", (status, lead_id))


def get_job_lead(lead_id: int) -> Optional[sqlite3.Row]:
    return get_conn().execute(
        "SELECT * FROM job_lead WHERE id=?", (lead_id,)
    ).fetchone()


def list_job_leads(status: Optional[str] = None, limit: int = 200) -> list:
    if status:
        return get_conn().execute(
            "SELECT * FROM job_lead WHERE status=? ORDER BY ai_score DESC NULLS LAST, created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return get_conn().execute(
        "SELECT * FROM job_lead ORDER BY ai_score DESC NULLS LAST, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_existing_lead_urls() -> set:
    rows = get_conn().execute("SELECT job_url FROM job_lead").fetchall()
    return {r["job_url"] for r in rows if r["job_url"]}


def import_lead_as_application(lead_id: int) -> int:
    """Convert a job_lead into a draft application and mark the lead imported."""
    from matching.question_match import normalize_company_name
    lead = get_job_lead(lead_id)
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")
    normalized = normalize_company_name(lead["company_name"] or "Unknown")
    company_id = upsert_company(lead["company_name"] or "Unknown", normalized)
    app_id = create_application(
        company_id=company_id,
        job_title=lead["title"] or "",
        job_url=lead["job_url"] or "",
        jd_text=lead["jd_snippet"] or "",
    )
    update_lead_status(lead_id, "imported")
    return app_id


# ── Gmail sync ─────────────────────────────────────────────────────────────────

def save_gmail_sync(application_id: int, message_id: str, subject: str,
                    sender: str, detected_status: str, confidence: float) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO gmail_sync"
            " (application_id, gmail_message_id, subject, sender, detected_status, confidence)"
            " VALUES (?,?,?,?,?,?) RETURNING id",
            (application_id, message_id, subject, sender, detected_status, confidence),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        return get_conn().execute(
            "SELECT id FROM gmail_sync WHERE gmail_message_id=?", (message_id,)
        ).fetchone()["id"]


def get_applications_for_gmail_sync() -> list:
    """Submitted applications that don't yet have a final outcome."""
    return get_conn().execute(
        "SELECT a.*, c.name as company_name FROM application a"
        " JOIN company c ON c.id=a.company_id"
        " WHERE a.status='submitted'"
        " ORDER BY a.submitted_at DESC"
    ).fetchall()


def list_gmail_syncs(limit: int = 50) -> list:
    return get_conn().execute(
        "SELECT gs.*, a.job_title, c.name as company_name"
        " FROM gmail_sync gs"
        " JOIN application a ON a.id=gs.application_id"
        " JOIN company c ON c.id=a.company_id"
        " WHERE gs.detected_status != 'irrelevant'"
        " ORDER BY gs.synced_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
