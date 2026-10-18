"""Anthropic client: cached source context, streaming, server-side refusal fallback, JSON output."""

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

# A declined request is re-run server-side on Anthropic's recommended model for
# that refusal category, instead of coming back empty.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class GenerationError(RuntimeError):
    """The model refused, or its output was cut off before it finished."""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _cached_source_blocks() -> list[dict]:
    """The source blocks sent with prompt caching on every call (inventory only when it exists)."""
    data = data_loader.load_all()
    blocks = [
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
    if data.get("capabilities"):
        blocks.append({
            "type": "text",
            "text": "CAPABILITY INVENTORY (from his intake; each entry's level decides how it may be used):\n"
                    + json.dumps(data["capabilities"], indent=2),
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


def _complete(user_prompt: str, model: Optional[str], max_tokens: int,
              effort: Optional[str], output_format: Optional[dict] = None) -> str:
    """One streamed request with the cached sources in front; returns the response text."""
    output_config = {"effort": effort or config.GENERATION_EFFORT}
    if output_format:
        output_config["format"] = output_format

    # Streaming keeps long resume generations clear of HTTP timeouts.
    with get_client().beta.messages.stream(
        model=model or config.GENERATION_MODEL,
        max_tokens=max_tokens,
        betas=[_FALLBACK_BETA],
        fallbacks="default",
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": _cached_source_blocks() + [{"type": "text", "text": user_prompt}],
        }],
        output_config=output_config,
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise GenerationError(f"Model declined the request: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise GenerationError(f"Output hit max_tokens={max_tokens} before finishing.")
    return "".join(b.text for b in response.content if b.type == "text").strip()


def generate(user_prompt: str, model: Optional[str] = None,
             max_tokens: int = 16000, effort: Optional[str] = None) -> str:
    """Free-text generation (answers, cover letters) with the cached sources."""
    return _complete(user_prompt, model, max_tokens, effort)


def generate_json(user_prompt: str, schema: dict, model: Optional[str] = None,
                  max_tokens: int = 64000, effort: Optional[str] = None) -> dict:
    """Generation constrained to a JSON schema (structured outputs); returns the parsed object."""
    text = _complete(user_prompt, model, max_tokens, effort,
                     output_format={"type": "json_schema", "schema": schema})
    return json.loads(text)


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
