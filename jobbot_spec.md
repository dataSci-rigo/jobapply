# JobBot — Build Spec for Claude Code

A local, human-in-the-loop assistant that speeds up job applications. It does **not** auto-submit. A Flask control panel and a Selenium-driven browser run side by side; the user reviews everything and clicks **Submit** themselves in the real browser window.

---

## 1. Goals

- Cut per-application time by automating the slow parts: tailoring the resume, writing a cover letter, and answering application questions.
- Reuse prior work: when applying to a company again, or answering a question seen before, surface previous answers as a pick-able option alongside freshly generated ones.
- Learn from outcomes: answers from applications that got a callback become reference material that biases future generation.
- Keep everything truthful: all generated content is constrained to the user's real history (master resume JSON + STAR JSON + intake), never invented.

## 2. Non-goals / hard constraints

- **No automated submission.** The bot fills fields; the human clicks Submit in the live browser. The Flask UI must never trigger a submit on the target site.
- No email parsing. Outcome status (callback / rejected / ghosted) is logged manually by the user.
- No headless scraping farm. Target rate is ~10–20 applications/hour — human-paced.
- The Selenium browser is the single live "application window" the user interacts with; Flask is a separate control panel positioned beside it.

## 3. Tech stack

- **Python 3.11+**
- **Flask** — intake + review control panel (the UI), runs locally.
- **SQLite** — single-file storage (`data/jobbot.db`). Use the stdlib `sqlite3` or a thin layer; no heavy ORM required.
- **Selenium** — owns one persistent WebDriver instance (visible Chrome). Use `undetected-chromedriver` or a real Chrome profile to reduce trivial bot fingerprinting; never run headless for the live application.
- **Anthropic SDK** (`anthropic`) — generation. Pin a current model from docs.claude.com in `config.py` (a Sonnet-tier model for resume/cover-letter/answer generation; consider a Haiku-tier model for cheap matching/normalization tasks). Use **prompt caching** for the master resume, preferences, and STAR JSON since they're sent on most calls.
- **Resume → PDF**: HTML/Jinja template rendered with **WeasyPrint** (clean, easily styled, good enough for ~30 variants). LaTeX is an acceptable alternative if finer typography is wanted later.
- **rapidfuzz** — fuzzy question matching (phase 4).

## 4. Input data (provided by the user, treated as source of truth)

Place in `data/`:

- `master_resume.json` — full structured work history (companies, roles, dates, bullets, skills, education, etc.). The single source of truth for resume tailoring. Generation may **select, reorder, and rephrase** these facts to match a JD, but must not introduce experience not present here.
- `preferences.json` — standing answers that rarely change: work authorization, sponsorship needs, salary expectation, location/relocation, notice period, demographic/EEO defaults, links (portfolio, GitHub, LinkedIn), etc.
- `star.json` — output of a separate STAR interview artifact: structured situation/task/action/result stories. Supplements the intake as a source of concrete examples for behavioral questions.

These three files are read at startup and cached. The app should validate their shape and fail loudly with a clear message if a file is malformed or missing.

## 5. Data model (SQLite)

```
company        id, name, normalized_name, website, notes
               -- normalized_name used for "applied here before?" matching

application    id, company_id, job_title, job_url, ats_platform,
               jd_text, status, resume_version_id, cover_letter_id,
               created_at, submitted_at, updated_at
               -- status: draft | submitted | callback | rejected | ghosted

question       id, question_text, normalized_text, question_hash,
               field_type
               -- field_type: text | textarea | select | radio | checkbox | file
               -- question_hash = sha256(normalized_text) for exact-match bucketing

answer         id, application_id, question_id, answer_text,
               source, is_reference, created_at
               -- source: previous | generated | manual | edited
               -- is_reference flips true when its application reaches status=callback

resume_version id, application_id (nullable, reusable), resume_json,
               pdf_path, label, created_at

cover_letter   id, application_id, text, pdf_path, created_at
```

Notes:
- `question` is a shared bank across all applications. Two answers can attach to the same question across different applications — that's what powers "show me previous answers to this question."
- A resume version may be reused across applications (`application_id` nullable), since the user expects to converge on ~30 variants rather than a unique one per job.

## 6. Question matching (powers "previous answers as an option")

Two surfacing dimensions, combined:

1. **By company** — if `normalized_name` matches an existing company, surface all previous answers, resume versions, and cover letters used for that company.
2. **By question** — normalize each detected question (lowercase, strip punctuation, collapse whitespace, trim known boilerplate), hash it, and look up matching answers regardless of company. Many behavioral/standing questions repeat verbatim across sites.

MVP = exact match on `question_hash` + company scoping. Phase 4 adds `rapidfuzz` token-sort similarity (threshold ~85) to catch near-duplicates. Embedding similarity is a possible later upgrade, not required.

## 7. Generation modules (`generation/`)

All generation is constrained to real history and respects any field character limits captured by the extractor.

- `client.py` — Anthropic wrapper. Holds the cached system context (master resume + preferences + STAR + relevant reference answers). One place to swap model/prompt.
- `answers.py` — given a question + JD + context, generate an answer. If `is_reference` answers exist for similar questions, include them as style/substance exemplars.
- `resume_tailor.py` — given master resume JSON + JD, produce a tailored resume **JSON** (selected/reordered/rephrased bullets, reweighted skills). Output is JSON so it can be (a) rendered to PDF and (b) stored as a `resume_version`. Must not fabricate.
- `cover_letter.py` — given JD + tailored resume + company context, produce cover letter text.
- `prompts/` — prompt templates as files, not inline strings, so they're easy to iterate.

The reference-answer loop: when an application's status becomes `callback`, its answers get `is_reference=true`. Generation pulls relevant reference answers into context as exemplars.

## 8. Resume rendering (`resume/`)

- `render.py` — tailored resume JSON → PDF via Jinja HTML template + WeasyPrint. Deterministic, styleable, fast enough for many variants. Store the PDF path on the `resume_version` row.
- File-upload fields on application forms receive the **absolute path** of this PDF via Selenium `send_keys` on the `input[type=file]`.

## 9. Selenium layer (`selenium_layer/`)

The control model: **Flask owns one persistent WebDriver.** The Selenium Chrome window is the live application browser the user works in. The Flask UI runs in a separate ordinary browser window the user positions beside it. UI actions hit Flask endpoints → Flask calls driver methods → fields populate in the visible Selenium window. The user advances pages and clicks Submit themselves.

- `driver.py` — creates and holds the single WebDriver for the app's lifetime. Because the driver must persist across requests, run Flask single-process and guard the driver (note the threading caveat below). Provide navigate/refresh/get-current-page helpers.
- `field_extractor.py` — **generic, site-agnostic** detector. Scan all `input`, `textarea`, `select` (and custom combobox widgets) across the document **and inside iframes**. For each, capture: best-effort label (via `for`, `aria-label`, `placeholder`, nearby text), field type, `maxlength`, required flag, and a **stable selector** (prefer label/aria/name over volatile auto-generated IDs).
- `filler.py` — fill a given field with a given value. Handle: native `<select>`, custom react-select-style dropdowns (click → type → choose option), radio/checkbox, and file inputs (absolute path). Never guess at a required field it couldn't confidently map — flag it back to the UI for the human.
- `adapters/` — optional per-ATS refinements where the DOM is predictable: `greenhouse.py`, `lever.py`, `ashby.py`. Workday is the hardest case (heavy iframes, multi-page, dynamic IDs, bot-hostile) — implement best-effort generic handling and surface a "mostly manual on this one" notice rather than over-investing.

"All job boards" = robust generic extractor + a few adapters + graceful manual fallback. Do not attempt a bespoke adapter per site.

## 10. Flask control panel (`templates/`, `app.py`)

Views:

1. **Intake** — paste/confirm JD, job title, URL, detected ATS. Trigger "scan current page" (calls field_extractor on the live Selenium window). Detect "applied to this company before?".
2. **Review** — the core screen. For each detected field, show:
   - mapped standing preference (auto-filled), **or**
   - matching previous answers (selectable), **or**
   - a freshly generated answer.
   Each item supports: accept previous / accept generated / edit inline / regenerate. Show tailored resume preview + cover letter, both editable. A **"Push to form"** button fills the live Selenium window with the accepted values. No submit button here.
3. **Outcome logging** — list past applications; manually set status (callback / rejected / ghosted). Setting `callback` flips that application's answers to `is_reference`.

## 11. End-to-end flow

1. User opens the target job page in the Selenium-controlled browser.
2. User pastes the JD into Intake; app records company/title/URL and checks for prior applications to that company.
3. App scans the live page → extracted fields.
4. For each field: map to a standing preference, or surface previous answers (by company + by question), or generate a new answer/resume/cover letter from real history (+ reference exemplars if any).
5. User reviews in the Review screen — picks, edits, regenerates as needed.
6. Tailored resume JSON → PDF; cover letter finalized.
7. User clicks **Push to form** → Selenium fills the live window (resume PDF uploaded by absolute path).
8. User advances any multi-page steps and clicks **Submit on the real site** themselves.
9. App saves the application as `submitted` with its answers, resume version, and cover letter.
10. Later, user logs the outcome; callbacks promote answers to references.

## 12. Edge cases & gotchas (call these out in implementation)

- **iframes**: Greenhouse embeds and Workday use them; the extractor and filler must traverse frames.
- **Multi-page applications** (esp. Workday): fill per visible page; the human advances between pages.
- **Custom dropdowns**: many ATS use non-native combo boxes; native `<select>` logic won't work.
- **Dynamic IDs**: prefer label/aria/name selectors; volatile IDs break across loads.
- **File uploads**: `send_keys` an absolute path to `input[type=file]`.
- **Character limits**: capture `maxlength`; generation must respect it.
- **Required-but-unmapped fields**: flag to the human, never fabricate.
- **WebDriver persistence**: the driver lives across Flask requests — run single-process / `threaded=False` or guard it; don't recreate per request.
- **Bot fingerprinting**: visible real-Chrome + human pacing keeps risk low; avoid headless and avoid burst behavior.
- **Token cost**: cache master resume / preferences / STAR; only the JD, question, and reference exemplars vary per call.

## 13. Suggested layout

```
jobbot/
  app.py
  config.py                 # env, model name, paths, char-limit defaults
  data/
    master_resume.json
    preferences.json
    star.json
    jobbot.db
  db/
    schema.sql
    store.py                # SQLite access layer + migrations
  matching/
    question_match.py        # normalize, hash, fuzzy
  generation/
    client.py
    answers.py
    resume_tailor.py
    cover_letter.py
    prompts/
  resume/
    render.py
    template.html
  selenium_layer/
    driver.py
    field_extractor.py
    filler.py
    adapters/
      greenhouse.py
      lever.py
      ashby.py
  templates/                 # Jinja: intake, review, outcomes
  static/
```

## 14. Build phases (ship each one working before the next)

- **Phase 0 — scaffold**: project structure, `config.py`, `schema.sql`, SQLite layer, load + validate the three input JSON files.
- **Phase 1 — generation, CLI-testable**: Anthropic client + answers / resume_tailor / cover_letter modules. Verify quality from the command line before any UI exists.
- **Phase 2 — resume PDF**: tailored JSON → PDF render.
- **Phase 3 — Flask intake + review UI**: works with manual copy/paste (no Selenium yet).
- **Phase 4 — storage + reuse**: persist applications/answers; question matching surfaces previous answers (exact, then fuzzy).
- **Phase 5 — Selenium core**: persistent driver, generic field extractor, filler, "Push to form."
- **Phase 6 — ATS adapters + hard cases**: Greenhouse/Lever/Ashby refinements, iframe + multi-page handling.
- **Phase 7 — outcome loop**: manual status logging; callback → reference answers wired back into generation.

## 15. Open decisions to confirm before Phase 5

- Resume PDF renderer: WeasyPrint (default here) vs LaTeX.
- Whether to use `undetected-chromedriver` or a pinned real Chrome profile.
- Exact model strings + which tier for matching vs generation (pin from docs.claude.com).
