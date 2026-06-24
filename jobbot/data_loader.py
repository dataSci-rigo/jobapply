"""Load and validate the three source-of-truth JSON files."""

import json
import logging
import sys
from pathlib import Path

import config

log = logging.getLogger(__name__)

_REQUIRED_RESUME_KEYS = {"name", "email", "experience", "education", "skills"}
_REQUIRED_PREFS_KEYS  = {"work_authorization", "sponsorship_needed", "location"}
_REQUIRED_STAR_KEYS   = {"situation", "task", "action", "result"}

_cache: dict = {}
_missing: list = []


class DataMissingError(RuntimeError):
    """Raised when required data files have not been set up yet."""


def _load(path: Path, label: str):
    if not path.exists():
        log.warning("[data_loader] MISSING: %s — copy data/*.example.json and fill in your info.", path)
        _missing.append(str(path.name))
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("[data_loader] INVALID JSON in %s: %s", path, exc)
        _missing.append(str(path.name))
        return None
    return data


def _validate_resume(data: dict) -> bool:
    missing = _REQUIRED_RESUME_KEYS - set(data.keys())
    if missing:
        log.error("[data_loader] master_resume.json missing keys: %s", missing)
        return False
    if not isinstance(data.get("experience"), list):
        log.error("[data_loader] master_resume.json: 'experience' must be a list")
        return False
    if not isinstance(data.get("education"), list):
        log.error("[data_loader] master_resume.json: 'education' must be a list")
        return False
    return True


def _validate_prefs(data: dict) -> bool:
    missing = _REQUIRED_PREFS_KEYS - set(data.keys())
    if missing:
        log.error("[data_loader] preferences.json missing keys: %s", missing)
        return False
    return True


def _validate_star(data: list) -> bool:
    if not isinstance(data, list):
        log.error("[data_loader] star.json must be a JSON array")
        return False
    for i, item in enumerate(data):
        missing = _REQUIRED_STAR_KEYS - set(item.keys())
        if missing:
            log.error("[data_loader] star.json item %d missing keys: %s", i, missing)
            return False
    return True


def load_all() -> dict:
    if _cache:
        return _cache

    _missing.clear()

    resume = _load(config.MASTER_RESUME_PATH, "master_resume.json")
    prefs  = _load(config.PREFERENCES_PATH,   "preferences.json")
    star   = _load(config.STAR_PATH,           "star.json")

    if resume is not None:
        _validate_resume(resume)
    if prefs is not None:
        _validate_prefs(prefs)
    if star is not None:
        _validate_star(star)

    _cache["resume"] = resume or {}
    _cache["prefs"]  = prefs  or {}
    _cache["star"]   = star   or []
    return _cache


def is_ready() -> tuple[bool, list[str]]:
    """Return (True, []) if all data files loaded OK, else (False, [list of missing filenames])."""
    load_all()
    return (len(_missing) == 0), list(_missing)


def get_resume() -> dict:
    data = load_all()["resume"]
    if not data:
        raise DataMissingError(
            "master_resume.json not found or empty. "
            "Copy jobbot/data/master_resume.example.json → jobbot/data/master_resume.json and fill it in."
        )
    return data


def get_prefs() -> dict:
    data = load_all()["prefs"]
    if not data:
        raise DataMissingError(
            "preferences.json not found or empty. "
            "Copy jobbot/data/preferences.example.json → jobbot/data/preferences.json and fill it in."
        )
    return data


def get_star() -> list:
    data = load_all()["star"]
    if not data:
        raise DataMissingError(
            "star.json not found or empty. "
            "Copy jobbot/data/star.example.json → jobbot/data/star.json and fill it in."
        )
    return data
