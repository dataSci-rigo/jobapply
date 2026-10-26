"""
Adaptive intake interview — one question at a time, chosen from what he has said so far.

The Profile page's lists (gap questions mined from postings, proposals drafted
from his notes, follow-up questions) are too long to work through as forms.
The interview uses them as the agenda instead: each turn is one model call that

  1. files his last answer: inventory entries at the level his own words
     support, proposals confirmed / corrected / dropped, follow-up questions
     settled, interests (what he wants or wants to avoid) noted;
  2. picks the single most useful next question — a follow-up that pins down
     specifics of what he just said, a broad question that settles several
     posting gaps at once, a check of a group of proposals, or a question
     about what he wants to do next.

Everything saved lands in the inventory at once (source "interview") and is
shown with an undo; the transcript and interests live in data/interview.json.
"""

import json
import uuid
from datetime import datetime

import capabilities
import config

INTEREST_KINDS = ("want", "avoid", "curious")

_PROPOSAL_FIX = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "entry": capabilities.ENTRY_SCHEMA},
    "required": ["id", "entry"],
    "additionalProperties": False,
}
TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {"type": "array", "items": capabilities.ENTRY_SCHEMA},
        "confirm_proposals": {"type": "array", "items": _PROPOSAL_FIX},
        "drop_proposals": {"type": "array", "items": {"type": "string"}},
        "answered_questions": {"type": "array", "items": {"type": "string"}},
        "interests": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(INTEREST_KINDS)},
                           "text": {"type": "string"}},
            "required": ["kind", "text"],
            "additionalProperties": False,
        }},
        "next_question": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "why": {"type": "string"},
                           "options": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "why", "options"],
            "additionalProperties": False,
        },
        "done": {"type": "boolean"},
    },
    "required": ["entries", "confirm_proposals", "drop_proposals", "answered_questions",
                 "interests", "next_question", "done"],
    "additionalProperties": False,
}

_RECENT_TURNS = 16      # shown in full; older ones only as the question asked
_AGENDA_GAPS = 40

_PROMPT = """You are interviewing the candidate to fill his capability inventory, so job applications can show everything he can do. His resume, preferences, STAR stories and current inventory are above. Postings and resumes are imperfect: your job is to find what he has done, what he can credibly ramp on fast, and what he wants, by talking with him.

STEP 1 — file his last answer (if there is one; "(skipped)" means move to another area and file nothing).
- entries: one per distinct tool, skill, domain, achievement or responsibility his answer establishes. Level from his own words, never upgraded: professional = paid role; project = personal or academic project; familiar = working knowledge, not delivered with; learnable = not done yet but he is confident he can ramp fast (`adjacent` = the closest thing he HAS done, required); none = he says he hasn't (record it, so he is not asked again). Specifics (what, scale, numbers, result, years, employer or project) go in evidence/where/when; leave a field empty rather than guess. If he says he directed an AI assistant rather than writing code himself, say so in evidence.
- Update an existing inventory entry by reusing its exact name.
- confirm_proposals: proposals (ids below) his answer settles, with the entry corrected by what he said. drop_proposals: ids he says are wrong.
- answered_questions: follow-up question ids his answer settles.
- interests: what he says he wants to do, would avoid, or is curious about (roles, domains, problems, work style, pay, location). Only what he said.
- Don't file the same fact twice; an empty list is fine.

STEP 2 — ask the ONE next question with the most value for his applications.
- Short and conversational: one or two sentences, plain words, no preamble, no praise.
- When his last answer opened something valuable but vague, follow up for specifics: what exactly he did, how big, what result, what he personally wrote vs. directed. At most two follow-ups on one thread, then move on.
- Otherwise prefer broad questions that settle several agenda items at once ("Have you run anything in containers or on a cloud — Docker, Kubernetes, AWS, GCP? What for?") over one-term questions. Weigh gaps by how many postings ask and whether required.
- Check proposals in groups that share a source ("Your poker notes show MCCFR, Deep CFR and policy distillation — did you write that code, or direct an AI assistant, or both?").
- Every few questions, ask about what he wants (kinds of problems, roles, domains, things to avoid) and what he's confident he could learn fast and why.
- Never ask what the sources already answer or what the transcript already covered. Never lead him toward a claim.
- options: two to five short quick replies that fit the question (e.g. "Yes, at work", "In a project", "No"); empty when only a free answer fits.
- why: a few words on what the answer unlocks (e.g. "required by 4 postings").
- done = true only when nothing on the agenda is worth asking.

AGENDA — posting requirements not yet covered (term · postings asking · required?):
{gaps}

PROPOSALS drafted from his notes, awaiting his word (id · name · drafted level · where · evidence):
{proposals}

FOLLOW-UP QUESTIONS from his notes (id · question):
{questions}

INTERESTS he has stated:
{interests}

EARLIER QUESTIONS (already asked; don't repeat):
{earlier}

RECENT TRANSCRIPT:
{transcript}
"""


# ── Storage ───────────────────────────────────────────────────────────────────

def _load() -> dict:
    path = config.INTERVIEW_PATH
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"turns": data.get("turns", []), "interests": data.get("interests", [])}


def _save(data: dict) -> None:
    path = config.INTERVIEW_PATH
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    import data_loader
    data_loader.reset_cache()      # interests feed generation through data_loader


def load_turns() -> list[dict]:
    return _load()["turns"]


def load_interests() -> list[dict]:
    return _load()["interests"]


def delete_interest(interest_id: str) -> None:
    data = _load()
    data["interests"] = [i for i in data["interests"] if i["id"] != interest_id]
    _save(data)


def undo(turn_id: str, index: int) -> bool:
    """Reverse one item filed from an answer (a chip's ×): drop the entry or interest, restore a proposal."""
    data = _load()
    turn = next((t for t in data["turns"] if t["id"] == turn_id), None)
    items = (turn or {}).get("saved", [])
    if not 0 <= index < len(items) or items[index].get("undone"):
        return False
    item = items[index]
    if item["kind"] == "entry":
        capabilities.delete(item["id"])
    elif item["kind"] == "interest":
        data["interests"] = [i for i in data["interests"] if i["id"] != item["id"]]
    if item.get("proposal"):
        capabilities.restore_proposed(item["proposal"])
    item["undone"] = True
    _save(data)
    return True


def current_question() -> dict | None:
    """The asked-but-unanswered question, if any."""
    turns = load_turns()
    return turns[-1] if turns and turns[-1].get("answer") is None else None


# ── Prompt ────────────────────────────────────────────────────────────────────

def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _agenda(jd_texts: list[str]) -> dict:
    entries = capabilities.load()
    gaps, _ = capabilities.gap_questions(jd_texts, entries, limit=_AGENDA_GAPS)
    data = _load()
    turns = data["turns"]
    recent, earlier = turns[-_RECENT_TURNS:], turns[:-_RECENT_TURNS]
    return {
        "gaps": "\n".join(f"- {g['term']} · {g['postings']} · {'required' if g['required'] else 'preferred'}"
                          for g in gaps) or "(none)",
        "proposals": "\n".join(f"- {p['id']} · {p['name']} · {p.get('level', '')} · {p.get('where', '')} · "
                               f"{_clip(p.get('evidence', ''), 90)}" for p in capabilities.load_proposed())
                     or "(none)",
        "questions": "\n".join(f"- {q['id']} · {q['question']}" for q in capabilities.load_questions())
                     or "(none)",
        "interests": "\n".join(f"- {i['kind']}: {i['text']}" for i in data["interests"]) or "(none yet)",
        "earlier": "\n".join(f"- {t['question']}" for t in earlier) or "(none)",
        "transcript": "\n".join(f"Q: {t['question']}\nA: {t['answer'] if t.get('answer') is not None else '(awaiting)'}"
                                for t in recent) or "(this is the first question; there is no answer to file)",
    }


# ── One turn ──────────────────────────────────────────────────────────────────

def _apply(result: dict, data: dict) -> list[dict]:
    """File the model's reading of his answer; returns what was saved, for the undo chips."""
    saved = []
    for entry in result["entries"]:
        try:
            e = capabilities.upsert(entry, source="interview")
        except ValueError:
            continue                     # e.g. 'learnable' without adjacent proof
        saved.append({"kind": "entry", "id": e["id"], "name": e["name"], "level": e["level"]})
    proposals = {p["id"]: p for p in capabilities.load_proposed()}
    for fix in result["confirm_proposals"]:
        if fix["id"] not in proposals:
            continue
        try:
            e = capabilities.confirm_proposed(fix["id"], fix["entry"])
        except ValueError:
            continue
        saved.append({"kind": "entry", "id": e["id"], "name": e["name"], "level": e["level"],
                      "proposal": proposals.pop(fix["id"])})
    for pid in result["drop_proposals"]:
        if pid in proposals:
            capabilities.drop_proposed(pid)
            p = proposals.pop(pid)
            saved.append({"kind": "dropped", "id": pid, "name": p["name"], "level": "dropped", "proposal": p})
    for qid in result["answered_questions"]:
        capabilities.dismiss_question(qid)
    known = {i["text"].lower() for i in data["interests"]}
    for i in result["interests"]:
        if i["text"].lower() in known:
            continue
        item = {"id": uuid.uuid4().hex[:10], "kind": i["kind"], "text": i["text"],
                "at": datetime.now().isoformat(timespec="seconds")}
        data["interests"].append(item)
        known.add(i["text"].lower())
        saved.append({"kind": "interest", "id": item["id"], "name": i["text"], "level": i["kind"]})
    return saved


def _open_turn(data: dict) -> dict | None:
    return data["turns"][-1] if data["turns"] and data["turns"][-1].get("answer") is None else None


def _finish(answered: dict | None, result: dict) -> dict:
    """File the reading of the answer to `answered` (if any) and append the next question."""
    data = _load()
    saved = _apply(result, data) if answered else []
    if answered:
        turn = next(t for t in data["turns"] if t["id"] == answered["id"])
        turn["saved"] = saved
    question = None
    if not result["done"]:
        q = result["next_question"]
        question = {"id": uuid.uuid4().hex[:10], "question": q["text"].strip(), "why": q["why"].strip(),
                    "options": [o for o in q["options"] if o.strip()][:5], "answer": None,
                    "at": datetime.now().isoformat(timespec="seconds")}
        data["turns"].append(question)
    _save(data)
    return {"saved": saved, "answered_turn": answered["id"] if answered else None,
            "question": question, "done": result["done"]}


def step(answer: str | None, jd_texts: list[str]) -> dict:
    """
    Record his answer to the open question (None = just start/resume; "" = skip),
    file it, and ask the next question. Returns {saved, answered_turn, question, done}.
    """
    from generation.client import generate_json

    data = _load()
    open_turn = _open_turn(data)
    if open_turn and answer is None:
        return {"saved": [], "answered_turn": None, "question": open_turn, "done": False}   # resume
    if open_turn:
        open_turn["answer"] = answer.strip() or "(skipped)"
        _save(data)

    prompt = _PROMPT.format(**_agenda(jd_texts))
    result = generate_json(prompt, TURN_SCHEMA, effort=config.INTERVIEW_EFFORT, max_tokens=32000)
    return _finish(open_turn, result)


_TURN_DEFAULTS = {"entries": [], "confirm_proposals": [], "drop_proposals": [], "answered_questions": [],
                  "interests": [], "done": False}


def file_turn(answer: str, result: dict) -> dict:
    """
    File a turn read by someone other than the model call in step() — Claude reading his
    Telegram voice notes. `result` has TURN_SCHEMA's shape (missing lists default to empty).
    The answer goes to the open question; with none open it is kept as an unprompted note.
    """
    result = {**_TURN_DEFAULTS, **result}
    if not result["done"] and not (result.get("next_question") or {}).get("text"):
        raise ValueError("next_question.text is required unless done is true")
    result.setdefault("next_question", {"text": "", "why": "", "options": []})
    result["next_question"] = {"why": "", "options": [], **result["next_question"]}
    data = _load()
    turn = _open_turn(data)
    if turn is None:
        turn = {"id": uuid.uuid4().hex[:10], "question": "(unprompted note)", "why": "", "options": [],
                "answer": None, "at": datetime.now().isoformat(timespec="seconds")}
        data["turns"].append(turn)
    turn["answer"] = answer.strip() or "(skipped)"
    _save(data)
    return _finish(turn, result)


def file_note(answer: str, result: dict) -> dict:
    """
    File something he added to an earlier answer without answering the open question:
    it is kept as its own turn just before the open question, which stays open.
    """
    result = {**_TURN_DEFAULTS, **result, "done": True}
    data = _load()
    note = {"id": uuid.uuid4().hex[:10], "question": "(added to an earlier answer)", "why": "", "options": [],
            "answer": answer.strip(), "at": datetime.now().isoformat(timespec="seconds")}
    data["turns"].insert(len(data["turns"]) - (1 if _open_turn(data) else 0), note)
    _save(data)
    return _finish(note, result)
