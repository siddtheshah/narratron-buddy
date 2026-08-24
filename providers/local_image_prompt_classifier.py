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

from providers.prompt_routing_rules import label_prompt, routing_reasons


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
    PromptRoutingExample("A floating city in the sky", "complex", True),
    PromptRoutingExample("A navigational compass on a map", "complex", True),
    PromptRoutingExample("A split-screen view of a city and forest", "complex", True),
    PromptRoutingExample("A detailed battle scene", "complex", True),
    PromptRoutingExample("A collage of travel photographs", "complex", True),
    PromptRoutingExample("A busy marketplace at noon", "complex", True),
    PromptRoutingExample("A chef preparing dinner", "complex", True),
    PromptRoutingExample("A painter creating a mural", "complex", True),
    PromptRoutingExample("A detective examining clues", "complex", True),
    PromptRoutingExample("A spaceship landing beside a robot", "complex", True),
    PromptRoutingExample("A castle with knights and dragons", "complex", True),
)


class HybridImageClassifier:
    """Hard policy rules plus a trained, conservative in-process text model."""

    model = "tfidf-char-logreg-v1"
    _MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "hybrid_image_classifier.joblib"

    _COMPLEX = re.compile(r"\b(?:split[ -]?screen|collage|battle|busy|panoramic|montage)\b|\b(?:spaceship|starship)\b.*\b(?:robot|astronaut)\b", re.I)
    _CHARACTER = re.compile(r"\b(?:person|man|woman|child|astronaut|knight|wizard|sailor|wanderer|robot|fox|dog|cat|dragon|hero|detective|chef|painter)\b", re.I)
    _SINGLE_CHARACTER_CUE = re.compile(r"\b(?:one|single|lone|solitary)\b", re.I)
    _ENVIRONMENT = re.compile(r"\b(?:mountain|valley|hills?|desert|road|waterfall|jungle|forest|coast(?:line)?|lake|shore|street|grove|lighthouse|sky|clouds?|ocean|sea|river|beach|snow|dune|landscape|empty|mist|fog)\b", re.I)
    _AMBIGUOUS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("floating", re.compile(r"\bfloating\s+city\b", re.I)),
        ("compass", re.compile(r"\b(?:navigational\s+)?compass\s+(?:on|over|beside)\s+(?:a\s+)?map\b", re.I)),
    )

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
        labels = label_prompt(prompt)
        reasons = routing_reasons(labels)
        ambiguous_terms = [term for term, pattern in self._AMBIGUOUS if pattern.search(prompt)]
        if self._COMPLEX.search(prompt):
            reasons.append("complex_composition")
        if ambiguous_terms:
            reasons.append("contextual_disambiguation")

        if reasons:
            return self._complex(reasons, ambiguous_terms)

        if self._SINGLE_CHARACTER_CUE.search(prompt) and self._CHARACTER.search(prompt):
            return self._decision(False, "single_character", [], [])

        if self._artifact is None:
            return self._decision(True, "complex", ["complex_composition"], []) | {"classifier_failed": True}

        probability = float(self._artifact["model"].predict_proba([prompt])[0][1])
        primary_max = float(self._artifact["primary_max_complex_probability"])
        if probability > primary_max:
            return self._complex(["complex_composition"], [])

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
