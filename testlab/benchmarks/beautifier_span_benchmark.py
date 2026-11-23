"""Candidate generation and probability ranking for the beautifier experiment."""

from dataclasses import dataclass
import re

from pydantic import JsonValue

from providers.decision_provider import (
    MAX_DECISION_QUESTIONS,
    DecisionQuestion,
    DecisionResult,
    PredicateQuestion,
)

HIGHLIGHT_THRESHOLD = 0.60


@dataclass(frozen=True)
class SpanCandidate:
    name: str
    text: str
    start: int
    end: int
    line: int


def build_span_candidates(text: str) -> tuple[SpanCandidate, ...]:
    """Preserve exact source offsets; never join words across line boundaries."""
    candidates: list[SpanCandidate] = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        words = list(re.finditer(r"\S+", line))
        ranges: set[tuple[int, int]] = set()
        for index, word in enumerate(words):
            ranges.add((word.start(), word.end()))
            if index + 1 < len(words):
                ranges.add((word.start(), words[index + 1].end()))
        if words:
            ranges.add((words[0].start(), words[-1].end()))
        for start, end in sorted(ranges):
            candidates.append(SpanCandidate(
                name=f"emphasis_{len(candidates) + 1}", text=line[start:end],
                start=offset + start, end=offset + end, line=line_number,
            ))
        offset += len(line)
    if len(candidates) > MAX_DECISION_QUESTIONS:
        raise ValueError(
            f"Text generates {len(candidates)} emphasis candidates; "
            f"the benchmark supports at most {MAX_DECISION_QUESTIONS}. Shorten the text."
        )
    return tuple(candidates)


def build_span_questions(text: str) -> tuple[DecisionQuestion, ...]:
    return tuple(PredicateQuestion(
        name=candidate.name,
        instructions=(
            "Does this exact candidate carry strong emotional, sensory, magical, or suspenseful "
            "impact that merits visual emphasis in the supplied narration? Evaluate its own "
            "suitability independently; do not distribute a shared probability budget. "
            "Ordinary connective or calm descriptive text should score low. Prefer a focused "
            "1-2 word highlight; a whole line merits emphasis only when its impact is sustained. "
            f"Candidate on line {candidate.line}, character range "
            f"[{candidate.start}, {candidate.end}): {candidate.text!r}"
        ),
    ) for candidate in build_span_candidates(text))


def rank_span_outcome(text: str, result: DecisionResult) -> dict[str, JsonValue]:
    """Greedily select at most two eligible, nonoverlapping candidates."""
    ranked = sorted(
        build_span_candidates(text),
        key=lambda candidate: (
            -result.get_predicate(candidate.name).probability,
            candidate.end - candidate.start, candidate.start,
        ),
    )
    selected: list[SpanCandidate] = []
    ranking: list[JsonValue] = []
    for candidate in ranked:
        probability = result.get_predicate(candidate.name).probability
        reason = "selected"
        if probability < HIGHLIGHT_THRESHOLD:
            reason = "below threshold"
        elif any(candidate.start < prior.end and prior.start < candidate.end for prior in selected):
            reason = "overlaps selected span"
        elif len(selected) == 2:
            reason = "two-span limit"
        else:
            selected.append(candidate)
        ranking.append({
            "name": candidate.name, "text": candidate.text,
            "start": candidate.start, "end": candidate.end, "line": candidate.line,
            "probability": probability, "selected": reason == "selected", "reason": reason,
        })
    selected.sort(key=lambda candidate: candidate.start)
    segments: list[JsonValue] = []
    cursor = 0
    for candidate in selected:
        segments.append({"text": text[cursor:candidate.start], "highlight": False})
        segments.append({"text": text[candidate.start:candidate.end], "highlight": True})
        cursor = candidate.end
    segments.append({"text": text[cursor:], "highlight": False})
    names = ", ".join(candidate.text for candidate in selected)
    return {
        "headline": f"Highlight: {names}" if selected else "Leave narration unstyled",
        "badge": "SPANS: " + (" | ".join(candidate.text for candidate in selected) or "NONE"),
        "status": "action" if selected else "skip",
        "explanation": (
            f"Ranked {len(ranked)} one-word, two-word, and whole-line candidates. "
            "Selected at most two nonoverlapping spans at probability >= 60%; "
            "ties prefer shorter spans, then source order. No styling decision is inferred."
        ),
        "trace": [
            f"Candidates: {len(ranked)}", "Suitability threshold: 60%",
            f"Selected spans: {len(selected)} / 2",
        ],
        "candidate_ranking": ranking,
        "preview_segments": segments,
    }
