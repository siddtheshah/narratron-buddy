"""Provider-neutral contract for low-latency structured decisions (predicate, choice, score)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

MAX_DECISION_QUESTIONS = 400
MAX_CHOICE_OPTIONS = 255


class DecisionProviderError(RuntimeError):
    """A provider-normalized decision failure safe to show in diagnostics."""


@dataclass(frozen=True)
class DecisionAttachment:
    """Binary evidence supplied alongside a decision input."""

    data: bytes
    mime_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("Decision attachment data cannot be empty.")
        if not self.mime_type or not self.mime_type.strip():
            raise ValueError("Decision attachment mime_type cannot be empty.")


class DecisionWireAnswer(BaseModel):
    """Normalized wire representation of a single answer from a decisions endpoint."""

    type: Literal["predicate", "choice", "score"]
    name: str
    probability: float | None = None
    choice: str | None = None
    score: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class DecisionAnswer(ABC):
    """Base class for typed decision answers with polymorphic accessors."""

    name: str
    answer_type: Literal["predicate", "choice", "score"]

    def as_predicate(self) -> PredicateAnswer:
        raise DecisionProviderError(
            f"Question '{self.name}' returned a '{self.answer_type}' answer, expected 'predicate'."
        )

    def as_choice(self) -> ChoiceAnswer:
        raise DecisionProviderError(
            f"Question '{self.name}' returned a '{self.answer_type}' answer, expected 'choice'."
        )

    def as_score(self) -> ScoreAnswer:
        raise DecisionProviderError(
            f"Question '{self.name}' returned a '{self.answer_type}' answer, expected 'score'."
        )


@dataclass(frozen=True)
class PredicateAnswer(DecisionAnswer):
    """Probability estimate in [0.0, 1.0] for whether a condition holds."""

    name: str
    probability: float
    answer_type: Literal["predicate", "choice", "score"] = field(default="predicate", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("PredicateAnswer name cannot be empty.")
        if self.probability < 0.0 or self.probability > 1.0:
            raise ValueError(
                f"PredicateAnswer probability for '{self.name}' must be between 0.0 and 1.0, got {self.probability}."
            )

    def passed(self, threshold: float = 0.5) -> bool:
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError("Predicate threshold must be between 0.0 and 1.0.")
        return self.probability >= threshold

    def as_predicate(self) -> PredicateAnswer:
        return self


@dataclass(frozen=True)
class ChoiceAnswer(DecisionAnswer):
    """Selected option and probability distribution over discrete choices."""

    name: str
    choice: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None
    answer_type: Literal["predicate", "choice", "score"] = field(default="choice", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ChoiceAnswer name cannot be empty.")
        if not self.choice or not self.choice.strip():
            raise ValueError(f"ChoiceAnswer choice for '{self.name}' cannot be empty.")
        if self.confidence is not None and (self.confidence < 0.0 or self.confidence > 1.0):
            raise ValueError(
                f"ChoiceAnswer confidence for '{self.name}' must be between 0.0 and 1.0, got {self.confidence}."
            )
        for option, prob in self.probabilities.items():
            if prob < 0.0 or prob > 1.0:
                raise ValueError(
                    f"ChoiceAnswer probability for option '{option}' in '{self.name}' must be between 0.0 and 1.0."
                )

    def as_choice(self) -> ChoiceAnswer:
        return self


@dataclass(frozen=True)
class ScoreAnswer(DecisionAnswer):
    """Probability-weighted score across ordered rubric levels."""

    name: str
    score: float
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None
    answer_type: Literal["predicate", "choice", "score"] = field(default="score", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ScoreAnswer name cannot be empty.")
        if self.confidence is not None and (self.confidence < 0.0 or self.confidence > 1.0):
            raise ValueError(
                f"ScoreAnswer confidence for '{self.name}' must be between 0.0 and 1.0, got {self.confidence}."
            )
        for level, prob in self.probabilities.items():
            if prob < 0.0 or prob > 1.0:
                raise ValueError(
                    f"ScoreAnswer probability for level '{level}' in '{self.name}' must be between 0.0 and 1.0."
                )

    def as_score(self) -> ScoreAnswer:
        return self


class DecisionQuestion(ABC):
    """A typed question evaluated against shared decision evidence."""

    name: str
    instructions: str
    question_type: Literal["predicate", "choice", "score"]

    @abstractmethod
    def to_wire_dict(self) -> dict[str, JsonValue]:
        """Serialize question to the Decisions API wire format."""

    @abstractmethod
    def parse_wire_answer(self, raw: DecisionWireAnswer) -> DecisionAnswer:
        """Validate and convert a wire answer into a typed DecisionAnswer."""


@dataclass(frozen=True)
class PredicateQuestion(DecisionQuestion):
    """Evaluates whether a condition holds and returns a probability in [0.0, 1.0]."""

    name: str
    instructions: str
    question_type: Literal["predicate", "choice", "score"] = field(default="predicate", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("PredicateQuestion name cannot be empty.")
        if not self.instructions or not self.instructions.strip():
            raise ValueError(f"PredicateQuestion '{self.name}' instructions cannot be empty.")

    def to_wire_dict(self) -> dict[str, JsonValue]:
        return {
            "type": "predicate",
            "name": self.name,
            "instructions": self.instructions,
        }

    def parse_wire_answer(self, raw: DecisionWireAnswer) -> PredicateAnswer:
        if raw.type != "predicate":
            raise DecisionProviderError(
                f"Expected 'predicate' answer for question '{self.name}', got '{raw.type}'."
            )
        if raw.probability is None:
            raise DecisionProviderError(
                f"Predicate answer for question '{self.name}' is missing 'probability'."
            )
        try:
            return PredicateAnswer(name=self.name, probability=raw.probability)
        except ValueError as exc:
            raise DecisionProviderError(str(exc)) from exc


@dataclass(frozen=True)
class ChoiceQuestion(DecisionQuestion):
    """Selects one option from a bounded set of discrete choices."""

    name: str
    instructions: str
    choices: Sequence[str]
    question_type: Literal["predicate", "choice", "score"] = field(default="choice", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ChoiceQuestion name cannot be empty.")
        if not self.instructions or not self.instructions.strip():
            raise ValueError(f"ChoiceQuestion '{self.name}' instructions cannot be empty.")
        if len(self.choices) < 2:
            raise ValueError(f"ChoiceQuestion '{self.name}' requires at least 2 choices.")
        if len(self.choices) > MAX_CHOICE_OPTIONS:
            raise ValueError(
                f"ChoiceQuestion '{self.name}' supports at most {MAX_CHOICE_OPTIONS} choices."
            )
        normalized = [choice.strip() for choice in self.choices]
        if any(not choice for choice in normalized):
            raise ValueError(f"ChoiceQuestion '{self.name}' cannot contain empty choice strings.")
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"ChoiceQuestion '{self.name}' choices must be unique.")
        object.__setattr__(self, "choices", tuple(normalized))

    def to_wire_dict(self) -> dict[str, JsonValue]:
        return {
            "type": "choice",
            "name": self.name,
            "instructions": self.instructions,
            "choices": list(self.choices),
        }

    def parse_wire_answer(self, raw: DecisionWireAnswer) -> ChoiceAnswer:
        if raw.type != "choice":
            raise DecisionProviderError(
                f"Expected 'choice' answer for question '{self.name}', got '{raw.type}'."
            )
        if raw.choice is None or not raw.choice.strip():
            raise DecisionProviderError(
                f"Choice answer for question '{self.name}' is missing 'choice'."
            )
        if raw.choice not in self.choices:
            raise DecisionProviderError(
                f"Choice answer '{raw.choice}' for question '{self.name}' is not in allowed choices {list(self.choices)}."
            )
        try:
            return ChoiceAnswer(
                name=self.name,
                choice=raw.choice,
                probabilities=dict(raw.probabilities),
                confidence=raw.confidence,
            )
        except ValueError as exc:
            raise DecisionProviderError(str(exc)) from exc


@dataclass(frozen=True)
class ScoreQuestion(DecisionQuestion):
    """Rates an input against an ordered rubric of levels."""

    name: str
    instructions: str
    levels: Sequence[str]
    level_descriptions: Mapping[str, str] | None = None
    question_type: Literal["predicate", "choice", "score"] = field(default="score", init=False)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("ScoreQuestion name cannot be empty.")
        if not self.instructions or not self.instructions.strip():
            raise ValueError(f"ScoreQuestion '{self.name}' instructions cannot be empty.")
        if len(self.levels) < 2:
            raise ValueError(f"ScoreQuestion '{self.name}' requires at least 2 rubric levels.")
        normalized = [level.strip() for level in self.levels]
        if any(not level for level in normalized):
            raise ValueError(f"ScoreQuestion '{self.name}' cannot contain empty level names.")
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"ScoreQuestion '{self.name}' levels must be unique.")
        object.__setattr__(self, "levels", tuple(normalized))

        if self.level_descriptions is not None:
            allowed = set(normalized)
            cleaned_descriptions: dict[str, str] = {}
            for key, desc in self.level_descriptions.items():
                clean_key = key.strip()
                clean_desc = desc.strip()
                if clean_key not in allowed:
                    raise ValueError(
                        f"ScoreQuestion '{self.name}' level_descriptions key '{clean_key}' is not in levels."
                    )
                if not clean_desc:
                    raise ValueError(
                        f"ScoreQuestion '{self.name}' description for level '{clean_key}' cannot be empty."
                    )
                cleaned_descriptions[clean_key] = clean_desc
            object.__setattr__(self, "level_descriptions", cleaned_descriptions)

    def to_wire_dict(self) -> dict[str, JsonValue]:
        if self.level_descriptions is not None:
            wire_levels: list[JsonValue] = [
                {
                    "value": level,
                    "description": self.level_descriptions.get(level, ""),
                }
                for level in self.levels
            ]
        else:
            wire_levels = list(self.levels)
        return {
            "type": "score",
            "name": self.name,
            "instructions": self.instructions,
            "levels": wire_levels,
        }

    def parse_wire_answer(self, raw: DecisionWireAnswer) -> ScoreAnswer:
        if raw.type != "score":
            raise DecisionProviderError(
                f"Expected 'score' answer for question '{self.name}', got '{raw.type}'."
            )
        if raw.score is None:
            raise DecisionProviderError(
                f"Score answer for question '{self.name}' is missing 'score'."
            )
        try:
            return ScoreAnswer(
                name=self.name,
                score=raw.score,
                probabilities=dict(raw.probabilities),
                confidence=raw.confidence,
            )
        except ValueError as exc:
            raise DecisionProviderError(str(exc)) from exc


@dataclass(frozen=True)
class DecisionRequest:
    """Batch of 1 to 400 typed questions evaluated against shared input evidence."""

    input_text: str
    questions: Sequence[DecisionQuestion]
    model: str | None = None
    attachments: Sequence[DecisionAttachment] = ()

    def __post_init__(self) -> None:
        if not self.input_text or not self.input_text.strip():
            raise ValueError("DecisionRequest input_text cannot be empty.")
        if not self.questions:
            raise ValueError("DecisionRequest requires at least one question.")
        if len(self.questions) > MAX_DECISION_QUESTIONS:
            raise ValueError(
                f"DecisionRequest supports at most {MAX_DECISION_QUESTIONS} questions per request."
            )
        seen_names: set[str] = set()
        for question in self.questions:
            if question.name in seen_names:
                raise ValueError(f"Duplicate question name in DecisionRequest: '{question.name}'.")
            seen_names.add(question.name)
        object.__setattr__(self, "questions", tuple(self.questions))
        object.__setattr__(self, "attachments", tuple(self.attachments))


@dataclass(frozen=True)
class DecisionResult:
    """Typed answers keyed by question name from a single decision evaluation."""

    provider: str
    model: str
    answers: Mapping[str, DecisionAnswer]
    request_id: str | None = None
    usage: Mapping[str, JsonValue] = field(default_factory=dict)

    def get_answer(self, name: str) -> DecisionAnswer:
        if name not in self.answers:
            raise DecisionProviderError(f"DecisionResult does not contain an answer for '{name}'.")
        return self.answers[name]

    def get_predicate(self, name: str) -> PredicateAnswer:
        return self.get_answer(name).as_predicate()

    def get_choice(self, name: str) -> ChoiceAnswer:
        return self.get_answer(name).as_choice()

    def get_score(self, name: str) -> ScoreAnswer:
        return self.get_answer(name).as_score()


class DecisionProvider(ABC):
    """An adapter that evaluates structured decision questions against shared evidence."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def decide(self, request: DecisionRequest) -> DecisionResult:
        """Evaluate all questions in ``request`` or raise :class:`DecisionProviderError`."""

    def check_predicate(
        self,
        input_text: str,
        instructions: str,
        *,
        name: str = "decision",
        model: str | None = None,
        attachments: Sequence[DecisionAttachment] = (),
    ) -> PredicateAnswer:
        """Convenience helper to evaluate a single predicate question."""
        result = self.decide(
            DecisionRequest(
                input_text=input_text,
                questions=(PredicateQuestion(name=name, instructions=instructions),),
                model=model,
                attachments=attachments,
            )
        )
        return result.get_predicate(name)

    def choose(
        self,
        input_text: str,
        instructions: str,
        choices: Sequence[str],
        *,
        name: str = "decision",
        model: str | None = None,
        attachments: Sequence[DecisionAttachment] = (),
    ) -> ChoiceAnswer:
        """Convenience helper to evaluate a single multiple-choice question."""
        result = self.decide(
            DecisionRequest(
                input_text=input_text,
                questions=(ChoiceQuestion(name=name, instructions=instructions, choices=choices),),
                model=model,
                attachments=attachments,
            )
        )
        return result.get_choice(name)

    def score(
        self,
        input_text: str,
        instructions: str,
        levels: Sequence[str],
        *,
        level_descriptions: Mapping[str, str] | None = None,
        name: str = "decision",
        model: str | None = None,
        attachments: Sequence[DecisionAttachment] = (),
    ) -> ScoreAnswer:
        """Convenience helper to evaluate a single rubric score question."""
        result = self.decide(
            DecisionRequest(
                input_text=input_text,
                questions=(
                    ScoreQuestion(
                        name=name,
                        instructions=instructions,
                        levels=levels,
                        level_descriptions=level_descriptions,
                    ),
                ),
                model=model,
                attachments=attachments,
            )
        )
        return result.get_score(name)
