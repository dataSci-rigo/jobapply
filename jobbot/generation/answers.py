"""Generate answers for individual application form questions."""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from generation.client import generate_with_references

_TEMPLATE = (Path(__file__).parent / "prompts" / "answer.txt").read_text()


def generate_answer(
    question: str,
    jd_snippet: str = "",
    field_type: str = "textarea",
    char_limit: int = 0,
    reference_answers: list[dict] | None = None,
) -> str:
    effective_limit = char_limit or config.DEFAULT_CHAR_LIMIT

    reference_block = ""
    if reference_answers:
        examples = "\n\n".join(
            f"Q: {r['question_text']}\nA: {r['answer_text']}" for r in reference_answers
        )
        reference_block = (
            "REFERENCE ANSWERS (from applications that received callbacks — "
            "match this quality and tone):\n" + examples
        )

    prompt = _TEMPLATE.format(
        question=question,
        field_type=field_type,
        char_limit=effective_limit,
        jd_snippet=jd_snippet[:2000] if jd_snippet else "(not provided)",
        reference_block=reference_block,
    )

    answer = generate_with_references(
        prompt,
        reference_answers=reference_answers or [],
        max_tokens=min(effective_limit // 2 + 200, 2048),
    )

    # Hard-trim to char limit as a safety net
    if len(answer) > effective_limit:
        answer = answer[: effective_limit - 3] + "..."
    return answer
