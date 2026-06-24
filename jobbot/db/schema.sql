PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS company (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    website         TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS application (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES company(id),
    job_title         TEXT NOT NULL,
    job_url           TEXT,
    ats_platform      TEXT,
    jd_text           TEXT,
    status            TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft','submitted','callback','rejected','ghosted')),
    resume_version_id INTEGER REFERENCES resume_version(id),
    cover_letter_id   INTEGER REFERENCES cover_letter(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS question (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text   TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    question_hash   TEXT NOT NULL,   -- sha256(normalized_text)
    field_type      TEXT NOT NULL DEFAULT 'text'
                      CHECK(field_type IN ('text','textarea','select','radio','checkbox','file'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_question_hash ON question(question_hash);

CREATE TABLE IF NOT EXISTS answer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES application(id),
    question_id    INTEGER NOT NULL REFERENCES question(id),
    answer_text    TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'generated'
                     CHECK(source IN ('previous','generated','manual','edited')),
    is_reference   INTEGER NOT NULL DEFAULT 0,   -- 1 when application reaches callback
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS resume_version (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES application(id),  -- nullable: reusable
    resume_json    TEXT NOT NULL,
    pdf_path       TEXT,
    label          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cover_letter (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES application(id),
    text           TEXT NOT NULL,
    pdf_path       TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_answer_question ON answer(question_id);
CREATE INDEX IF NOT EXISTS idx_answer_app     ON answer(application_id);
CREATE INDEX IF NOT EXISTS idx_app_company    ON application(company_id);

CREATE TABLE IF NOT EXISTS job_lead (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    title        TEXT,
    company_name TEXT,
    location     TEXT,
    job_url      TEXT UNIQUE,
    jd_snippet   TEXT,
    ai_score     INTEGER,
    status       TEXT NOT NULL DEFAULT 'pending'
                   CHECK(status IN ('pending','kept','discarded','imported')),
    raw_json     TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lead_status ON job_lead(status);

CREATE TABLE IF NOT EXISTS gmail_sync (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id   INTEGER REFERENCES application(id),
    gmail_message_id TEXT UNIQUE,
    subject          TEXT,
    sender           TEXT,
    detected_status  TEXT CHECK(detected_status IN ('callback','rejected','ghosted','irrelevant')),
    confidence       REAL,
    synced_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
