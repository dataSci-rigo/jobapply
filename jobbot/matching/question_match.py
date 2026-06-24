"""Question normalization, hashing, and fuzzy matching."""

import hashlib
import re
from typing import Optional

from rapidfuzz import fuzz

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from db import store

# Boilerplate prefixes stripped before normalization
_BOILERPLATE = re.compile(
    r"^(please (describe|explain|tell us|share|provide)|describe|explain|tell us about|"
    r"what (is|are|was|were|has been)|how (do|did|would|have)|in your (own )?words,?)\s*",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    t = text.lower()
    t = _BOILERPLATE.sub("", t)
    t = re.sub(r"[^\w\s]", " ", t)      # strip punctuation
    t = re.sub(r"\s+", " ", t).strip()  # collapse whitespace
    return t


def question_hash(normalized_text: str) -> str:
    return hashlib.sha256(normalized_text.encode()).hexdigest()


def find_or_create_question(question_text: str, field_type: str = "text") -> int:
    norm = normalize(question_text)
    qhash = question_hash(norm)
    return store.upsert_question(question_text, norm, qhash, field_type)


def find_matching_question_id(question_text: str) -> Optional[int]:
    """Return existing question_id via exact hash, then fuzzy fallback."""
    norm = normalize(question_text)
    qhash = question_hash(norm)

    row = store.get_question_by_hash(qhash)
    if row:
        return row["id"]

    # Fuzzy pass — check all stored normalized texts
    all_qs = store.list_all_questions()
    best_score = 0
    best_id = None
    for q in all_qs:
        score = fuzz.token_sort_ratio(norm, q["normalized_text"])
        if score > best_score:
            best_score = score
            best_id = q["id"]

    if best_score >= config.FUZZY_THRESHOLD:
        return best_id
    return None


def get_previous_answers(question_text: str, field_type: str = "text") -> list[dict]:
    """
    Return previous answers for a question, creating the question record if new.
    Result dicts include question_text, answer_text, company_name, job_title, is_reference.
    """
    question_id = find_matching_question_id(question_text)
    if question_id is None:
        return []

    rows = store.get_previous_answers_for_question(question_id)
    return [dict(r) for r in rows]


def normalize_company_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b(inc|llc|ltd|corp|co|company|the)\b\.?", "", n)
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n
