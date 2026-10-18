"""
Capability inventory — what the candidate can do beyond what the resume says.

The master resume records what he has done, phrased for a resume. The inventory
(data/capabilities.json) records everything else an application can draw on,
each item at an honest level:

  professional  done in a paid role               -> claimable like a resume fact
  project       done in a personal/academic project -> claimable as project work
  familiar      working knowledge, not delivered  -> skills lists / "familiar with"
  learnable     not done yet, confident to ramp   -> never claimed as experience;
                fast, with adjacent proof            cover letters and answers may say
                                                     "haven't used X; did Y"
  none          asked, and he hasn't              -> never implied; not asked again

It is filled by the Profile page: gap questions mined from the postings he has
targeted (terms his history doesn't cover), and free-form notes structured by
the model for him to confirm.
"""

import json
import re
import uuid
from datetime import date

import config
import data_loader

LEVELS = ("professional", "project", "familiar", "learnable", "none")
CLAIMABLE = ("professional", "project", "familiar")   # may appear on a resume (familiar: skills only)
CATEGORIES = ("tool", "language", "domain", "method", "achievement", "responsibility",
              "certification", "other")

ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "level": {"type": "string", "enum": list(LEVELS)},
        "where": {"type": "string"},
        "when": {"type": "string"},
        "evidence": {"type": "string"},
        "adjacent": {"type": "string"},
    },
    "required": ["name", "category", "level", "where", "when", "evidence", "adjacent"],
    "additionalProperties": False,
}
STRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {"type": "array", "items": ENTRY_SCHEMA},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["entries", "follow_up_questions"],
    "additionalProperties": False,
}


# ── Storage ───────────────────────────────────────────────────────────────────

def load() -> list[dict]:
    path = config.CAPABILITIES_PATH
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("entries", [])


def save(entries: list[dict]) -> None:
    path = config.CAPABILITIES_PATH
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"entries": entries}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    data_loader.reset_cache()      # generation reads the inventory through data_loader


def _clean(entry: dict) -> dict:
    e = {k: str(entry.get(k, "") or "").strip() for k in ENTRY_SCHEMA["properties"]}
    if e["level"] not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    if e["category"] not in CATEGORIES:
        e["category"] = "other"
    if not e["name"]:
        raise ValueError("name is required")
    if e["level"] == "learnable" and not e["adjacent"]:
        raise ValueError("a 'learnable' entry needs the adjacent experience that backs it")
    return e


def upsert(entry: dict, source: str = "intake") -> dict:
    """Add or update (matched by id, else by case-insensitive name)."""
    entries = load()
    clean = _clean(entry)
    match = next((e for e in entries if entry.get("id") and e["id"] == entry["id"]), None) \
        or next((e for e in entries if e["name"].lower() == clean["name"].lower()), None)
    if match:
        match.update(clean, updated=date.today().isoformat())
        saved = match
    else:
        saved = {"id": uuid.uuid4().hex[:10], **clean, "source": source,
                 "updated": date.today().isoformat()}
        entries.append(saved)
    save(entries)
    return saved


def delete(entry_id: str) -> bool:
    entries = load()
    kept = [e for e in entries if e["id"] != entry_id]
    save(kept)
    return len(kept) != len(entries)


def claimable(entries: list[dict]) -> list[dict]:
    """Entries a resume may draw on (the validator treats these as sources)."""
    return [e for e in entries if e["level"] in CLAIMABLE]


# ── Proposals (model-extracted, awaiting his confirmation) ───────────────────

def _proposed_file() -> dict:
    path = config.PROPOSED_CAPABILITIES_PATH
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"entries": data.get("entries", []), "questions": data.get("questions", [])}


def _write_proposed_file(data: dict) -> None:
    path = config.PROPOSED_CAPABILITIES_PATH
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_proposed() -> list[dict]:
    return _proposed_file()["entries"]


def _save_proposed(entries: list[dict]) -> None:
    data = _proposed_file()
    data["entries"] = entries
    _write_proposed_file(data)


def load_questions() -> list[dict]:
    """Follow-up questions the model asked while reading his notes: [{id, source, question}]."""
    return _proposed_file()["questions"]


def add_questions(questions: list[str], source: str) -> None:
    data = _proposed_file()
    known = {q["question"] for q in data["questions"]}
    for q in questions:
        if q not in known:
            data["questions"].append({"id": uuid.uuid4().hex[:10], "source": source, "question": q})
            known.add(q)
    _write_proposed_file(data)


def dismiss_question(question_id: str) -> None:
    data = _proposed_file()
    data["questions"] = [q for q in data["questions"] if q["id"] != question_id]
    _write_proposed_file(data)


def add_proposed(entries: list[dict], source: str) -> int:
    """Stage proposals; skips names already in the inventory or already proposed."""
    proposed = load_proposed()
    taken = {e["name"].lower() for e in load()} | {e["name"].lower() for e in proposed}
    added = 0
    for e in entries:
        if not e.get("name") or e["name"].lower() in taken:
            continue
        proposed.append({"id": uuid.uuid4().hex[:10], **e, "source": source})
        taken.add(e["name"].lower())
        added += 1
    _save_proposed(proposed)
    return added


def drop_proposed(entry_id: str) -> None:
    _save_proposed([e for e in load_proposed() if e["id"] != entry_id])


def confirm_proposed(entry_id: str, edited: dict) -> dict:
    """Save his (possibly edited) version to the inventory and remove the proposal."""
    source = next((e.get("source", "proposal") for e in load_proposed() if e["id"] == entry_id), "proposal")
    saved = upsert({k: v for k, v in edited.items() if k != "id"}, source=f"confirmed: {source}")
    drop_proposed(entry_id)
    return saved


# ── Gap questions ─────────────────────────────────────────────────────────────

REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {"requirements": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": ["tool", "language", "domain", "method", "credential", "other"]},
            "importance": {"type": "string", "enum": ["required", "preferred"]},
            "quote": {"type": "string"},
            "covered": {"type": "string", "enum": ["yes", "partly", "no"]},
        },
        "required": ["name", "kind", "importance", "quote", "covered"],
        "additionalProperties": False,
    }}},
    "required": ["requirements"],
    "additionalProperties": False,
}

_REQUIREMENTS_PROMPT = """List the concrete capabilities this job posting asks for, and whether the candidate's sources (master resume, STAR stories, capability inventory) already show each one.

- name: a short canonical name a recruiter would search for ("OPC UA", "Power BI", "Kubernetes", "IEC 61131-3 Structured Text", "PhD", "ITAR compliance"). Split compound requirements into separate items.
- Only technical skills, tools, languages, domains, methods and credentials. Skip soft skills, benefits, company facts, locations and boilerplate.
- importance: required or preferred, as the posting frames it. quote: the posting's words, under 20 words.
- covered: yes = his sources show it plainly; partly = adjacent or shallower experience; no = nothing in his sources.

JOB POSTING:
{jd}
"""


def _jd_key(jd: str) -> str:
    import hashlib
    return hashlib.sha256(jd.encode("utf-8")).hexdigest()[:16]


def _load_requirements_cache() -> dict:
    path = config.JD_REQUIREMENTS_PATH
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def extract_requirements(jd_texts: list[str], refresh_missing: bool = True) -> dict:
    """
    {jd_key: [requirement, ...]} for each posting, cached on disk by content hash.
    With refresh_missing=False only cached postings are returned (no model calls).
    """
    from generation.client import generate_json
    cache = _load_requirements_cache()
    changed = False
    for jd in jd_texts:
        key = _jd_key(jd)
        if key in cache or not refresh_missing:
            continue
        result = generate_json(_REQUIREMENTS_PROMPT.format(jd=jd[:12000]), REQUIREMENTS_SCHEMA,
                               effort="low", max_tokens=16000)
        cache[key] = result["requirements"]
        changed = True
    if changed:
        tmp = config.JD_REQUIREMENTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(config.JD_REQUIREMENTS_PATH)
    return {k: v for k, v in cache.items() if k in {_jd_key(jd) for jd in jd_texts}}


def gap_questions(jd_texts: list[str], entries: list[dict], refresh_missing: bool = False,
                  limit: int = 100) -> tuple[list[dict], int]:
    """
    Requirements his sources don't (fully) cover and he hasn't answered yet, merged
    across postings: required before preferred, then by how many postings ask.
    Returns (questions, number of postings not yet analyzed).
    """
    reqs = extract_requirements(jd_texts, refresh_missing=refresh_missing)
    pending = len({_jd_key(jd) for jd in jd_texts}) - len(reqs)
    answered = [e["name"].lower() for e in entries]

    def is_answered(name: str) -> bool:
        n = name.lower()
        return any(n == a or (len(a) > 3 and (a in n or n in a)) for a in answered)

    merged: dict[str, dict] = {}
    for key, items in reqs.items():
        for r in items:
            if r["covered"] == "yes" or is_answered(r["name"]):
                continue
            m = merged.setdefault(r["name"].lower(), {
                "term": r["name"], "kind": r["kind"], "postings": set(), "required": False,
                "covered": r["covered"], "snippet": r["quote"]})
            m["postings"].add(key)
            m["required"] |= r["importance"] == "required"
    # Fold variants into the shortest name they contain: "Distributed systems / streaming
    # data" and "Distributed systems (high reliability)" are one question.
    folded: dict[str, dict] = {}
    for key in sorted(merged, key=len):
        m = merged[key]
        base = next((k for k in folded if len(k) >= 6 and k in key), None)
        if base:
            folded[base]["postings"] |= m["postings"]
            folded[base]["required"] |= m["required"]
        else:
            folded[key] = m
    ranked = sorted(folded.values(), key=lambda m: (not m["required"], -len(m["postings"]), m["term"].lower()))
    out = [{**m, "postings": len(m["postings"])} for m in ranked[:limit]]
    return out, pending


# ── Free-form notes -> entries ────────────────────────────────────────────────

_STRUCTURE_PROMPT = """Turn the candidate's own notes below into capability-inventory entries.

Levels (use his words, never upgrade): professional = he did it in a paid role; project = he did it in a personal or academic project; familiar = working knowledge he hasn't delivered with; learnable = he hasn't done it but is confident he can ramp quickly (fill `adjacent` with the closest thing he HAS done, from his notes or the master resume); none = he says he hasn't.

- One entry per distinct tool, skill, domain, achievement or responsibility. Keep his specifics (what he did, scale, numbers, employer or project, years) in `evidence`, `where` and `when`; leave a field empty rather than guess.
- Don't add anything his notes don't say. Facts already in the master resume need no entry unless the notes add detail.
- follow_up_questions: up to five short questions that would turn vague items into strong, specific evidence (what exactly he did, how much, with what result).

NOTES:
{notes}
"""


def structure_notes(notes: str) -> dict:
    """Model-proposed entries + follow-up questions; nothing is saved until he confirms."""
    from generation.client import generate_json
    result = generate_json(_STRUCTURE_PROMPT.format(notes=notes[:20000]), STRUCTURE_SCHEMA,
                           max_tokens=32000)
    return result
