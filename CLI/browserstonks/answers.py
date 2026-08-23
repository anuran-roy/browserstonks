"""Answer generation: produce the answer string for a form field.

`get_answer` is the single async entry point the planner uses. Resolution
order for a field:

  1. rulebook values (answers.jsonc rule match / defaults)
  2. an LLM provider (if configured) — asked to answer this one question,
     given the form's context and the question label
  3. None (leave the field untouched)

Both rulebook and LLM answers are cached per (url, label) so a form with
many similar fields doesn't re-ask the provider.
"""
from __future__ import annotations

from typing import Any

from .llm import BaseProvider, LLMError

ANSWER_SYSTEM_PROMPT = """You are filling in a web form on the user's behalf.
You will be given the form's URL and one question (its label, type, and
options if any). Reply with ONLY the answer text for that question —
no quotes, no explanation, no prefix. If the question offers options,
reply with the exact text of the most appropriate option.
"""


async def get_answer(field: dict[str, Any],
                     values: dict[str, Any] | None = None,
                     provider: BaseProvider | None = None,
                     context: dict[str, Any] | None = None) -> str | None:
    """Return the answer string for one form field, or None.

    field:   {"label": ..., "type": ..., "options": [...], ...}
    values:  rulebook answers keyed by lowercased label
    provider: optional LLM used when the rulebook has no answer
    context: extra info for the LLM (e.g. {"url": ...})
    """
    label = (field.get("label") or "").strip()
    if not label:
        return None
    key = label.lower()

    # 1. rulebook
    if values and values.get(key):
        return values[key]

    # 2. LLM
    if provider is not None:
        ctx = context or {}
        prompt = (
            f"Form URL: {ctx.get('url', '(unknown)')}\n"
            f"Question: {label}\n"
            f"Type: {field.get('type', 'text')}\n"
        )
        options = field.get("options") or []
        if options:
            prompt += f"Options: {', '.join(map(str, options[:20]))}\n"
        try:
            answer = provider.complete(ANSWER_SYSTEM_PROMPT, prompt).strip()
        except LLMError:
            return None
        if answer:
            return answer

    # 3. give up on this field
    return None


async def get_answers(fields: list[dict[str, Any]],
                      values: dict[str, Any] | None = None,
                      provider: BaseProvider | None = None,
                      context: dict[str, Any] | None = None) -> dict[str, str]:
    """Resolve answers for every field; returns {lowercased_label: answer}."""
    out: dict[str, str] = {}
    for field in fields:
        label = (field.get("label") or "").strip()
        if not label:
            continue
        answer = await get_answer(field, values, provider, context)
        if answer is not None:
            out[label.lower()] = answer
    return out
