"""Prompt-aware image provider that routes difficult requests to a fallback."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from google import genai
from google.genai import types

from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider


CLASSIFIER_INSTRUCTIONS = """Classify this image-generation prompt. Return JSON only with:
{
  "route_to_fallback": boolean,
  "reasons": ["text_rendering" | "creature_object_interaction" | "creature_creature_interaction" | "contextual_disambiguation"],
  "ambiguous_terms": [string]
}

Set route_to_fallback to true only when the prompt needs readable inserted text,
precise creature/object contact, or precise interaction between two creatures.
Also route when a word has a materially different visual interpretation and the
prompt establishes its intended meaning through context. Examples: "floating
city in the sky" must not become floating on water, and a navigational compass
on a map must not become a geometric drawing compass. Add
"contextual_disambiguation" and the ambiguous word(s) in that case. Do not
route ordinary scenes that merely include an animal or person.
"""


class HybridImageProvider(ImageProvider):
    """Route selected prompts from a fast primary provider to a capable fallback.

    Classifier failures deliberately fail open to the primary provider, keeping a
    transient Gemini text failure from blocking image generation.
    """

    id = "hybrid-flux-gemini"
    display_name = "FLUX Klein 9B + Gemini router"

    def __init__(
        self,
        primary: ImageProvider,
        fallback: ImageProvider,
        classifier_model: str = "gemini-2.5-flash-lite",
        classifier: Callable[[str], dict[str, Any]] | None = None,
        client: Any = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.model = f"{primary.model} → {fallback.model}"
        self.classifier_model = classifier_model
        self._classifier = classifier
        if classifier is None:
            self.client = client or genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=os.getenv("GEMINI_CLASSIFIER_LOCATION", "global"),
            )

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        decision = self._classify(request.prompt)
        selected = self.fallback if decision["route_to_fallback"] else self.primary
        result = selected.generate(request)
        usage = dict(result.usage)
        usage["routing"] = {
            "classifier_model": self.classifier_model,
            "selected_provider": selected.id,
            "route_to_fallback": decision["route_to_fallback"],
            "reasons": decision["reasons"],
            "ambiguous_terms": decision["ambiguous_terms"],
            "classifier_failed": decision["classifier_failed"],
        }
        return ImageGenerationResult(
            image_bytes=result.image_bytes,
            mime_type=result.mime_type,
            provider=self.id,
            model=result.model,
            request_id=result.request_id,
            usage=usage,
        )

    def _classify(self, prompt: str) -> dict[str, Any]:
        try:
            raw = self._classifier(prompt) if self._classifier else self._classify_with_gemini(prompt)
            reasons = raw.get("reasons", []) if isinstance(raw, dict) else []
            ambiguous_terms = raw.get("ambiguous_terms", []) if isinstance(raw, dict) else []
            allowed = {"text_rendering", "creature_object_interaction", "creature_creature_interaction", "contextual_disambiguation"}
            reasons = [reason for reason in reasons if reason in allowed]
            return {
                "route_to_fallback": bool(raw.get("route_to_fallback")),
                "reasons": reasons,
                "ambiguous_terms": [term for term in ambiguous_terms if isinstance(term, str)][:5],
                "classifier_failed": False,
            }
        except Exception:
            return {"route_to_fallback": False, "reasons": [], "ambiguous_terms": [], "classifier_failed": True}

    def _classify_with_gemini(self, prompt: str) -> dict[str, Any]:
        response = self.client.models.generate_content(
            model=self.classifier_model,
            contents=f"{CLASSIFIER_INSTRUCTIONS}\n\nPrompt:\n{prompt}",
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("Gemini classifier returned no text.")
        return json.loads(text)
