"""Unit tests for prompt cleanup and safety reduction decision provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from providers.decision_provider import (
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
    PredicateAnswer,
)
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    TextResponseResult,
)
from tools.image.prompt_cleanup import (
    PROMPT_CLEANUP_MODEL,
    PROMPT_CLEANUP_SYSTEM_INSTRUCTION,
    clean_image_prompt,
    resolve_safety_reduction,
)


def _make_decision_result(passed: bool, prob: float = 0.9) -> DecisionResult:
    probability = prob if passed else 0.1
    return DecisionResult(
        provider="test-provider",
        model="test-model",
        answers={
            "safety_reduction": PredicateAnswer(
                name="safety_reduction",
                probability=probability,
            ),
        },
    )


def test_resolve_safety_reduction_calls_decision_provider_and_passes() -> None:
    mock_provider = MagicMock(spec=DecisionProvider)
    mock_provider.decide.return_value = _make_decision_result(passed=True, prob=0.88)

    prompt = "A duel, subdued violence, without bloodshed."
    result = resolve_safety_reduction(prompt, mock_provider)

    assert result is True
    mock_provider.decide.assert_called_once()
    decision_request: DecisionRequest = mock_provider.decide.call_args[0][0]
    assert decision_request.input_text == prompt
    assert len(decision_request.questions) == 1
    question = decision_request.questions[0]
    assert question.name == "safety_reduction"
    assert question.question_type == "predicate"


def test_resolve_safety_reduction_rejects_when_predicate_fails() -> None:
    mock_provider = MagicMock(spec=DecisionProvider)
    mock_provider.decide.return_value = _make_decision_result(passed=False, prob=0.15)

    prompt = "A tranquil mountain sunrise."
    result = resolve_safety_reduction(prompt, mock_provider)

    assert result is False
    mock_provider.decide.assert_called_once()


@pytest.mark.parametrize("empty_prompt", ["", "   ", "\n\t"])
def test_resolve_safety_reduction_empty_prompt_returns_false_without_deciding(empty_prompt: str) -> None:
    mock_provider = MagicMock(spec=DecisionProvider)

    result = resolve_safety_reduction(empty_prompt, mock_provider)

    assert result is False
    mock_provider.decide.assert_not_called()


def test_clean_image_prompt_uses_text_provider() -> None:
    mock_provider = MagicMock(spec=TextResponseProvider)
    mock_provider.generate.return_value = TextResponseResult(
        text="A duel, graphic content, explicit violence, sparks fly.",
        provider="gemini-2-5",
        model=PROMPT_CLEANUP_MODEL,
    )

    original_prompt = "A duel, no graphic content, subdued violence, sparks fly."
    cleaned = clean_image_prompt(original_prompt, text_provider=mock_provider)

    assert cleaned == "A duel, graphic content, explicit violence, sparks fly."
    mock_provider.generate.assert_called_once()
    request: TextResponseRequest = mock_provider.generate.call_args[0][0]
    assert request.prompt == original_prompt
    assert request.system_instruction == PROMPT_CLEANUP_SYSTEM_INSTRUCTION
    assert request.temperature == 0.0


@pytest.mark.parametrize("empty_prompt", ["", "   ", "\n\t"])
def test_clean_image_prompt_empty_returns_empty_string(empty_prompt: str) -> None:
    mock_provider = MagicMock(spec=TextResponseProvider)

    result = clean_image_prompt(empty_prompt, text_provider=mock_provider)

    assert result == ""
    mock_provider.generate.assert_not_called()


def test_clean_image_prompt_strips_markdown_code_fences() -> None:
    mock_provider = MagicMock(spec=TextResponseProvider)
    mock_provider.generate.return_value = TextResponseResult(
        text="```text\nA dramatic duel with explicit violence.\n```",
        provider="gemini-2-5",
        model=PROMPT_CLEANUP_MODEL,
    )

    cleaned = clean_image_prompt("A duel, subdued violence.", text_provider=mock_provider)
    assert cleaned == "A dramatic duel with explicit violence."


def test_clean_image_prompt_strips_extraneous_quotes() -> None:
    mock_provider = MagicMock(spec=TextResponseProvider)
    mock_provider.generate.return_value = TextResponseResult(
        text='"A passionate embrace in the rain."',
        provider="gemini-2-5",
        model=PROMPT_CLEANUP_MODEL,
    )

    cleaned = clean_image_prompt("An implied embrace in the rain.", text_provider=mock_provider)
    assert cleaned == "A passionate embrace in the rain."


@patch("tools.image.prompt_cleanup.get_text_response_provider")
def test_clean_image_prompt_resolves_default_text_provider(mock_get_text_provider: MagicMock) -> None:
    mock_provider = MagicMock(spec=TextResponseProvider)
    mock_provider.generate.return_value = TextResponseResult(
        text="Cleaned prompt.",
        provider="gemini-2-5",
        model=PROMPT_CLEANUP_MODEL,
    )
    mock_get_text_provider.return_value = mock_provider

    cleaned = clean_image_prompt("Prompt with subdued violence.")

    assert cleaned == "Cleaned prompt."
    mock_get_text_provider.assert_called_once_with(
        "gemini-2-5",
        options={"model": PROMPT_CLEANUP_MODEL},
    )
    mock_provider.generate.assert_called_once()
