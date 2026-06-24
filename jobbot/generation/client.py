"""Anthropic client with prompt caching for master resume, preferences, and STAR data."""

import json
from pathlib import Path
from typing import Optional

import anthropic

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
import data_loader

_client: Optional[anthropic.Anthropic] = None
_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_context.txt").read_text()


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _cached_source_blocks() -> list[dict]:
    """Build the three large blocks that are sent with prompt caching on every call."""
    data = data_loader.load_all()
    return [
        {
            "type": "text",
            "text": "MASTER RESUME (source of truth — do not fabricate beyond this):\n"
                    + json.dumps(data["resume"], indent=2),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "PREFERENCES (standing answers):\n"
                    + json.dumps(data["prefs"], indent=2),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "STAR STORIES (concrete behavioral examples):\n"
                    + json.dumps(data["star"], indent=2),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def generate(user_prompt: str, model: Optional[str] = None,
             max_tokens: int = 2048) -> str:
    """Send a generation request with cached source context. Returns the text response."""
    client = get_client()
    selected_model = model or config.GENERATION_MODEL

    source_blocks = _cached_source_blocks()

    response = client.messages.create(
        model=selected_model,
        max_tokens=max_tokens,
        system=[
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {
                "role": "user",
                "content": source_blocks + [{"type": "text", "text": user_prompt}],
            }
        ],
    )
    return response.content[0].text.strip()


def generate_with_references(user_prompt: str, reference_answers: list[dict],
                              model: Optional[str] = None, max_tokens: int = 1024) -> str:
    """Like generate() but injects reference answers as exemplars."""
    ref_block = ""
    if reference_answers:
        examples = "\n\n".join(
            f"Q: {r['question_text']}\nA: {r['answer_text']}" for r in reference_answers
        )
        ref_block = f"\nREFERENCE ANSWERS (from past applications that got callbacks — match this quality and style):\n{examples}\n"

    return generate(ref_block + user_prompt, model=model, max_tokens=max_tokens)
