"""Fast, conservative in-process routing for the hybrid image provider.

This deliberately uses a small, inspectable feature scorer instead of a network
model.  The labelled examples document the distilled routing policy and are
also useful as a regression corpus when the rules are adjusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import joblib


@dataclass(frozen=True)
class PromptRoutingExample:
    prompt: str
    scene_type: Literal["pure_environment", "single_character", "complex"]
    route_to_fallback: bool


# Forty hand-rated examples distilled from the router policy.  FLUX is allowed
# only for the unambiguous, low-composition cases represented here.
DISTILLED_ROUTING_EXAMPLES: tuple[PromptRoutingExample, ...] = (
    PromptRoutingExample("Mist over a quiet mountain valley", "pure_environment", False),
    PromptRoutingExample("A calm sunrise over misty hills", "pure_environment", False),
    PromptRoutingExample("A lonely desert road under stars", "pure_environment", False),
    PromptRoutingExample("Emerald waterfall in a jungle valley", "pure_environment", False),
    PromptRoutingExample("Moonlight on an empty snowy forest", "pure_environment", False),
    PromptRoutingExample("Storm clouds over a rocky coastline", "pure_environment", False),
    PromptRoutingExample("An empty cobblestone street at dawn", "pure_environment", False),
    PromptRoutingExample("Sunbeams through a quiet redwood grove", "pure_environment", False),
    PromptRoutingExample("A tranquil lake surrounded by autumn mountains", "pure_environment", False),
    PromptRoutingExample("An abandoned lighthouse on a foggy shore", "pure_environment", False),
    PromptRoutingExample("A lone astronaut standing on a sand dune", "single_character", False),
    PromptRoutingExample("One fox sitting in fresh snow", "single_character", False),
    PromptRoutingExample("A solitary knight beneath a moonlit sky", "single_character", False),
    PromptRoutingExample("A portrait of a woman against a plain backdrop", "single_character", False),
    PromptRoutingExample("A single dragon flying above clouds", "single_character", False),
    PromptRoutingExample("A lone robot in an empty warehouse", "single_character", False),
    PromptRoutingExample("One sailor on a quiet pier", "single_character", False),
    PromptRoutingExample("A lone wanderer in a desert", "single_character", False),
    PromptRoutingExample("A dog carries a bag", "complex", True),
    PromptRoutingExample("A cat holding a lantern", "complex", True),
    PromptRoutingExample("A wizard reading a book", "complex", True),
    PromptRoutingExample("A child riding a horse", "complex", True),
    PromptRoutingExample("Two wolves fighting", "complex", True),
    PromptRoutingExample("A couple dancing in a ballroom", "complex", True),
    PromptRoutingExample("Three astronauts on Mars", "complex", True),
    PromptRoutingExample("A mother and child walking together", "complex", True),
    PromptRoutingExample("A storefront with the words OPEN NOW", "complex", True),
    PromptRoutingExample("A poster with readable text", "complex", True),
    PromptRoutingExample("A book cover with a title", "complex", True),
    PromptRoutingExample("A floating city in the sky", "pure_environment", False),
    PromptRoutingExample("A navigational compass on a map", "pure_environment", False),
    PromptRoutingExample("A split-screen view of a city and forest", "pure_environment", False),
    PromptRoutingExample("A detailed battle scene", "pure_environment", False),
    PromptRoutingExample("A collage of travel photographs", "pure_environment", False),
    PromptRoutingExample("A busy marketplace at noon", "pure_environment", False),
    PromptRoutingExample("A chef preparing dinner", "complex", True),
    PromptRoutingExample("A painter creating a mural", "complex", True),
    PromptRoutingExample("A detective examining clues", "complex", True),
    PromptRoutingExample("A spaceship landing beside a robot", "single_character", False),
    PromptRoutingExample("A castle with knights and dragons", "complex", True),
)


class HybridImageClassifier:
    """Hard policy rules plus a trained, conservative in-process text model."""

    model = "tfidf-char-word-ovr-logreg-v3"
    _MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "hybrid_image_classifier.joblib"

    _CHARACTER = re.compile(r"\b(?:person|man|woman|child|astronaut|knight|wizard|sailor|wanderer|robot|fox|dog|cat|dragon|hero|detective|chef|painter)\b", re.I)
    _SINGLE_CHARACTER_CUE = re.compile(r"\b(?:one|single|lone|solitary)\b", re.I)
    _ENVIRONMENT = re.compile(r"\b(?:mountain|valley|hills?|desert|road|waterfall|jungle|forest|coast(?:line)?|lake|shore|street|grove|lighthouse|sky|clouds?|ocean|sea|river|beach|snow|dune|landscape|empty|mist|fog)\b", re.I)
    # A quoted phrase is a strong prompt-writing convention for text intended
    # to appear in the image. It remains part of text rendering rather than a
    # separate routing category.
    _TEXT_DISPLAYED = re.compile(r"\b(?:readable\s+)?(?:text|words?|letters?|lettering|caption|subtitle|logo|sign|poster|label|title|titled|typography|slogan)\b|(?:\"[^\"\r\n]{2,}\"|“[^”\r\n]{2,}”)", re.I)
    _OBJECT_INTERACTION = re.compile(r"\b(?:carry|carries|carrying|hold|holds|holding|touch|touches|touching|grab|grabs|grabbing|pick(?:s|ed|ing)?\s+up|read(?:s|ing)?|writ(?:e|es|ing)|cook(?:s|ing)?|play(?:s|ing)?|use(?:s|ing)?|wield(?:s|ing)|drink(?:s|ing)?|eat(?:s|ing)?|examin(?:e|es|ing))\b", re.I)
    _CREATURE_INTERACTION = re.compile(r"\b(?:fight(?:s|ing)?|dance(?:s|ing)?|rid(?:e|es|ing)|chase(?:s|ing)?|hug(?:s|ging)?|kiss(?:es|ing)?|embrac(?:e|es|ing)|attack(?:s|ing)?|meet(?:s|ing)?|talk(?:s|ing)?\s+(?:with|to))\b", re.I)
    _MULTIPLE_CHARACTERS = re.compile(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|several|many|multiple)\s+(?:people|men|women|children|characters|figures|creatures|animals|dogs|cats|wolves|dragons|robots|astronauts|knights|soldiers)\b|\b(?:pair|couple|crowd|group|famil(?:y|ies)|people|men|women|children|characters|figures|creatures|animals|dogs|cats|wolves|dragons|robots|astronauts|knights|soldiers|souls|demons)\b|\b(?:mother|father)\s+and\s+child\b", re.I)

    @classmethod
    def label_prompt(cls, prompt: str) -> dict[str, int]:
        """Return corpus labels and the derived complex target for a prompt."""
        labels = {
            "multiple_characters": int(bool(cls._MULTIPLE_CHARACTERS.search(prompt))),
            "creature_creature_interaction": int(bool(cls._CREATURE_INTERACTION.search(prompt))),
            "creature_object_interaction": int(bool(cls._OBJECT_INTERACTION.search(prompt))),
            "text_displayed": int(bool(cls._TEXT_DISPLAYED.search(prompt))),
        }
        labels["complex"] = int(any(labels.values()))
        return labels

    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or self._MODEL_PATH
        self._artifact: dict[str, Any] | None = None
        try:
            self._artifact = joblib.load(self.model_path)
            self.model = str(self._artifact["model_version"])
        except Exception as error:
            # Includes an unavailable sklearn/joblib dependency during a bad
            # deployment. Fail closed instead of preventing generation.
            self.load_error = f"{type(error).__name__}: {error}"
        else:
            self.load_error = None

    def classify(self, prompt: str) -> dict[str, Any]:
        labels = self.label_prompt(prompt)
        reasons = [
            reason
            for label, reason in (
                ("multiple_characters", "multiple_subjects"),
                ("creature_object_interaction", "creature_object_interaction"),
                ("text_displayed", "text_rendering"),
            )
            if labels[label]
        ]

        if reasons:
            return self._complex(reasons, [])

        if self._SINGLE_CHARACTER_CUE.search(prompt) and self._CHARACTER.search(prompt):
            return self._decision(False, "single_character", [], [])

        if self._artifact is None:
            return self._decision(True, "complex", ["classifier_unavailable"], []) | {"classifier_failed": True}

        features = self._artifact["vectorizer"].transform([prompt])
        classifier_reasons = {
            "multiple_creatures": "multiple_subjects",
            "creature_object_interaction": "creature_object_interaction",
            "text_displayed": "text_rendering",
        }
        learned_reasons = [
            classifier_reasons[name]
            for name, classifier in self._artifact["classifiers"].items()
            if float(classifier.predict_proba(features)[0][1]) >= float(self._artifact["thresholds"][name])
        ]
        if learned_reasons:
            return self._complex(learned_reasons, [])

        # The model has already made a strong non-complex prediction.  Use the
        # lexical scene type when available, but do not reject unfamiliar
        # proper names, plant names, or simple objects merely because they are
        # outside the tiny hand-maintained vocabulary.
        if self._CHARACTER.search(prompt):
            return self._decision(False, "single_character", [], [])
        if self._ENVIRONMENT.search(prompt):
            return self._decision(False, "pure_environment", [], [])
        return self._decision(False, "pure_environment", [], [])

    @staticmethod
    def _decision(route_to_fallback: bool, scene_type: str, reasons: list[str], ambiguous_terms: list[str]) -> dict[str, Any]:
        return {
            "route_to_fallback": route_to_fallback,
            "scene_type": scene_type,
            "reasons": reasons,
            "ambiguous_terms": ambiguous_terms,
            "classifier_failed": False,
        }

    def _complex(self, reasons: list[str], ambiguous_terms: list[str]) -> dict[str, Any]:
        return self._decision(True, "complex", list(dict.fromkeys(reasons)), ambiguous_terms)


# Compatibility alias for callers added while the first rule-only scorer was
# in use. New callers should use HybridImageClassifier.
LocalImagePromptClassifier = HybridImageClassifier
