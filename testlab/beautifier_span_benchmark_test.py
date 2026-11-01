"""Behavior checks for constrained span generation and probability selection."""

import pytest
from pydantic import TypeAdapter, JsonValue

from providers.decision_provider import DecisionAnswer, DecisionResult, PredicateAnswer
from testlab.beautifier_span_benchmark import build_span_candidates, build_span_questions, rank_span_outcome


def result_for(text: str, probabilities: dict[str, float]) -> DecisionResult:
    answers: dict[str, DecisionAnswer] = {
        candidate.name: PredicateAnswer(
            name=candidate.name, probability=probabilities.get(candidate.text, 0.1),
        ) for candidate in build_span_candidates(text)
    }
    return DecisionResult(provider="test", model="test", answers=answers)


def highlighted_texts(outcome: dict[str, JsonValue]) -> list[str]:
    segments = TypeAdapter(list[dict[str, JsonValue]]).validate_python(outcome["preview_segments"])
    return [str(segment["text"]) for segment in segments if segment["highlight"]]


def test_candidates_respect_lengths_lines_and_exact_offsets() -> None:
    text = "  One\tbright star!\r\nThen silence.  "
    candidates = build_span_candidates(text)
    assert len(candidates) == 9  # 3 words -> 6 candidates; 2 words -> 3, deduplicated.
    for candidate in candidates:
        assert text[candidate.start:candidate.end] == candidate.text
        assert "\n" not in candidate.text and "\r" not in candidate.text
        assert len(candidate.text.split()) <= 2 or candidate.text == "One\tbright star!"
    assert build_span_candidates(" \n\t") == ()


def test_candidates_grow_linearly_and_reject_oversize_requests() -> None:
    text = " ".join(f"word{i}" for i in range(25))
    assert len(build_span_candidates(text)) == 50  # No categorical 'none' needed.
    with pytest.raises(ValueError, match="at most 400"):
        build_span_questions(" ".join(f"word{i}" for i in range(201)))


def test_rank_selects_best_nonoverlapping_spans_and_preserves_source() -> None:
    text = "A DEAFENING CRACK.\nWe must RUN NOW!"
    outcome = rank_span_outcome(text, result_for(text, {
        "DEAFENING CRACK.": 0.99, "DEAFENING": 0.98,
        "RUN NOW!": 0.96, "NOW!": 0.95, "A": 0.9,
    }))
    assert highlighted_texts(outcome) == ["DEAFENING CRACK.", "RUN NOW!"]
    segments = TypeAdapter(list[dict[str, JsonValue]]).validate_python(outcome["preview_segments"])
    assert "".join(str(segment["text"]) for segment in segments) == text
    ranking = TypeAdapter(list[dict[str, JsonValue]]).validate_python(outcome["candidate_ranking"])
    assert next(row for row in ranking if row["text"] == "DEAFENING")["reason"] == "overlaps selected span"
    assert next(row for row in ranking if row["text"] == "A")["reason"] == "two-span limit"


def test_whole_line_excludes_its_words_but_allows_another_line() -> None:
    text = "YOU SHALL NOT PASS!\nRUN NOW!"
    outcome = rank_span_outcome(text, result_for(text, {
        "YOU SHALL NOT PASS!": 0.99, "NOT PASS!": 0.98, "RUN NOW!": 0.9,
    }))
    assert highlighted_texts(outcome) == ["YOU SHALL NOT PASS!", "RUN NOW!"]


def test_quiet_text_does_not_force_highlights() -> None:
    text = "A quiet morning."
    assert highlighted_texts(rank_span_outcome(text, result_for(text, {}))) == []


def test_probability_ties_prefer_shorter_then_earlier_spans() -> None:
    text = "red sun blue sky"
    outcome = rank_span_outcome(text, result_for(text, {
        "red": 0.6, "sun": 0.6, "red sun": 0.6,
    }))
    assert highlighted_texts(outcome) == ["red", "sun"]
