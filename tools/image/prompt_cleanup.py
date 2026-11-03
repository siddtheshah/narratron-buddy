"""Rewrite image generation prompts to reduce restraining safety phrasing using a TextResponseProvider."""

from __future__ import annotations

import logging
from typing import Optional

from providers.decision_provider import (
    DecisionProvider,
    DecisionRequest,
    PredicateQuestion,
)
from providers.registry import get_text_response_provider
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
)

logger = logging.getLogger(__name__)

PROMPT_CLEANUP_MODEL = "gemini-3.5-flash-lite"

PROMPT_CLEANUP_SYSTEM_INSTRUCTION = (
    "You are an image prompt rewriter specializing in visual fidelity. "
    "Your task is to rewrite image generation prompts to reduce negative or restraining safety phrasing. "
    "Replace euphemisms, implied elements, and negative restrictions (such as 'no graphic content', "
    "'subdued violence', 'without bloodshed', 'avoid gore', 'implied intimacy', 'restrained romance') "
    "with direct, vivid scene descriptions (such as 'graphic content', 'explicit violence', 'bloodshed', "
    "'gore', 'passionate romance', 'expressive'). "
    "Preserve all scene subjects, setting details, art styles, angle-bracket tags (e.g. <Character>), "
    "and quoted text verbatim. "
    "Return only the rewritten prompt text without commentary, quotes, or markdown formatting."
)


def resolve_safety_reduction(
    prompt: str,
    decision_provider: DecisionProvider,
) -> bool:
    """Use DecisionProvider to determine whether an image prompt requires safety reduction."""
    if not prompt or not prompt.strip():
        return False
    request = DecisionRequest(
        input_text=prompt,
        questions=(
            PredicateQuestion(
                name="safety_reduction",
                instructions=(
                    "Determine if the image generation prompt contains restraining, euphemistic, or safety-avoidance "
                    "phrasing (such as 'subdued violence', 'no graphic content', 'without bloodshed', 'avoid gore', "
                    "'implied romance', 'restrained') that should be reduced or rewritten for visual fidelity."
                ),
            ),
        ),
    )
    result = decision_provider.decide(request)
    return result.get_predicate("safety_reduction").passed()


def clean_image_prompt(
    prompt: str,
    text_provider: Optional[TextResponseProvider] = None,
) -> str:
    """Rewrite an image prompt using gemini-3.5-flash-lite TextProvider to reduce restraining phrasing."""
    if not prompt or not prompt.strip():
        return ""
    provider = text_provider
    if provider is None:
        provider = get_text_response_provider(
            "gemini-2-5",
            options={"model": PROMPT_CLEANUP_MODEL},
        )
    request = TextResponseRequest(
        prompt=prompt,
        system_instruction=PROMPT_CLEANUP_SYSTEM_INSTRUCTION,
        temperature=0.0,
    )
    result = provider.generate(request)
    cleaned = result.text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        if not (prompt.strip().startswith('"') and prompt.strip().endswith('"')):
            cleaned = cleaned[1:-1].strip()
    return cleaned
