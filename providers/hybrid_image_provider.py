"""Prompt-aware image provider with a narrow FLUX fast path."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider
from providers.text_response_provider import TextResponseProvider, TextResponseRequest

logger = logging.getLogger(__name__)
LOG_PREFIX = "[HybridImageProvider]"


class ImageClassifierResponse(BaseModel):
    """Structured response schema for image routing classifier."""

    route_to_fallback: bool = Field(
        default=True,
        description="Whether to route the prompt to the fallback provider (Gemini).",
    )
    scene_type: Literal["pure_environment", "single_character", "complex"] = Field(
        default="complex",
        description="Scene classification: 'pure_environment', 'single_character', or 'complex'.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Reasons for routing to fallback (e.g., text_rendering, creature_object_interaction, creature_creature_interaction, contextual_disambiguation, multiple_subjects, complex_composition).",
    )
    ambiguous_terms: list[str] = Field(
        default_factory=list,
        description="Ambiguous terms requiring contextual disambiguation.",
    )


CLASSIFIER_INSTRUCTIONS = """Classify this image-generation prompt for routing.

FLUX is the exception, not the default. Set route_to_fallback=false only for a
pure environment with no characters or a scene with exactly one
character that avoids the following:

1. readable text in scene
2. multiple creatures in the scene.
3. creatures manipulating objects through touch.
4. context sensitive word meanings
  - floating city in the sky could mean floating in water or air
  - navigational compass on a map must not become a geometric
drawing compass
  - Add "contextual_disambiguation" and the ambiguous word(s) in
that case.

If any of these apply, set route_to_fallback=true and
scene_type="complex". You MUST set a reason if rerouting.
"""


class HybridImageProvider(ImageProvider):
    """Use FLUX only for simple scenes; send everything else to Gemini."""

    id = "hybrid-flux-gemini"
    display_name = "FLUX Klein 9B + Gemini router"

    def __init__(
        self,
        primary: ImageProvider,
        fallback: ImageProvider,
        text_provider: TextResponseProvider | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self.model = f"{primary.model} → {fallback.model}"
        if text_provider is None:
            from providers.gemini_text_response_provider import GeminiTextResponseProvider

            text_provider = GeminiTextResponseProvider()
        self.text_provider = text_provider

        logger.debug(
            "%s Initialized (primary=%s, fallback=%s, text_provider=%s)",
            LOG_PREFIX,
            primary.id,
            fallback.id,
            getattr(self.text_provider, "id", None),
        )

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        prompt_preview = request.prompt[:120].replace("\n", " ")
        logger.info(
            "%s generate() called: prompt='%s', references=%d, aspect_ratio=%s",
            LOG_PREFIX,
            prompt_preview,
            len(request.references) if request.references else 0,
            request.aspect_ratio,
        )
        decision = self._reference_decision() if request.references else self._classify(request.prompt)
        selected = self.fallback if decision["route_to_fallback"] else self.primary
        logger.info(
            "%s Routing decision: selected='%s' (fallback=%s, scene_type=%s, reasons=%s, ambiguous_terms=%s, classifier_failed=%s)",
            LOG_PREFIX,
            selected.id,
            decision["route_to_fallback"],
            decision["scene_type"],
            decision["reasons"],
            decision["ambiguous_terms"],
            decision["classifier_failed"],
        )
        logger.debug("%s Delegating generation to provider '%s' (model='%s')", LOG_PREFIX, selected.id, selected.model)
        result = selected.generate(request)
        logger.info(
            "%s Generation completed via provider '%s' (model='%s', mime_type=%s, bytes=%d)",
            LOG_PREFIX,
            selected.id,
            result.model,
            result.mime_type,
            len(result.image_bytes) if result.image_bytes else 0,
        )
        usage = dict(result.usage)
        usage["routing"] = {
            "classifier_model": getattr(self.text_provider, "model", getattr(self.text_provider, "id", "unknown")),
            "selected_provider": selected.id,
            "route_to_fallback": decision["route_to_fallback"],
            "reasons": decision["reasons"],
            "ambiguous_terms": decision["ambiguous_terms"],
            "classifier_failed": decision["classifier_failed"],
            "scene_type": decision["scene_type"],
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
        prompt_preview = prompt[:100].replace("\n", " ")
        logger.debug("%s Classifying prompt for routing: '%s'", LOG_PREFIX, prompt_preview)
        try:
            raw = self._classify_with_text_provider(prompt)
            logger.debug("%s Raw classifier response: %s", LOG_PREFIX, raw)
            reasons = raw.get("reasons", []) if isinstance(raw, dict) else []
            ambiguous_terms = raw.get("ambiguous_terms", []) if isinstance(raw, dict) else []
            allowed = {
                "text_rendering",
                "creature_object_interaction",
                "creature_creature_interaction",
                "contextual_disambiguation",
                "multiple_subjects",
                "complex_composition",
            }
            reasons = [reason for reason in reasons if reason in allowed]
            scene_type = raw.get("scene_type") if isinstance(raw, dict) else None
            primary_eligible = scene_type in {"pure_environment", "single_character"}
            decision = {
                # A malformed or uncertain classifier response must preserve
                # quality by choosing Gemini, not the FLUX fast path.
                "route_to_fallback": bool(raw.get("route_to_fallback")) or not primary_eligible,
                "reasons": reasons,
                "ambiguous_terms": [term for term in ambiguous_terms if isinstance(term, str)][:5],
                "classifier_failed": False,
                "scene_type": scene_type if primary_eligible else "complex",
            }
            logger.debug("%s Processed classification decision: %s", LOG_PREFIX, decision)
            return decision
        except Exception as e:
            logger.warning(
                "%s Classifier error (%s: %s). Failing closed to fallback.",
                LOG_PREFIX,
                type(e).__name__,
                e,
            )
            return {
                "route_to_fallback": True,
                "reasons": ["complex_composition"],
                "ambiguous_terms": [],
                "classifier_failed": True,
                "scene_type": "complex",
            }

    @staticmethod
    def _reference_decision() -> dict[str, Any]:
        """Reference-guided generation always uses Gemini for visual fidelity."""
        logger.debug("%s Reference images present; bypassing classifier and routing to fallback", LOG_PREFIX)
        return {
            "route_to_fallback": True,
            "reasons": ["reference_images"],
            "ambiguous_terms": [],
            "classifier_failed": False,
            "scene_type": "complex",
        }

    def _classify_with_text_provider(self, prompt: str) -> dict[str, Any]:
        provider_id = getattr(self.text_provider, "id", "unknown")
        provider_model = getattr(self.text_provider, "model", "unknown")
        logger.debug("%s Querying classifier text provider '%s' (model='%s')...", LOG_PREFIX, provider_id, provider_model)
        request = TextResponseRequest(
            prompt=f"Prompt:\n{prompt}",
            system_instruction=CLASSIFIER_INSTRUCTIONS,
            temperature=0.0,
            response_schema=ImageClassifierResponse,
        )
        response = self.text_provider.generate(request)
        if getattr(response, "parsed", None) is not None:
            if isinstance(response.parsed, BaseModel):
                return response.parsed.model_dump()
            if isinstance(response.parsed, dict):
                return response.parsed
        text = getattr(response, "text", None)
        if not text:
            logger.error("%s Classifier text provider returned no text in response", LOG_PREFIX)
            raise ValueError("Classifier returned no text.")
        logger.debug("%s Classifier text provider raw response text: %s", LOG_PREFIX, text)
        clean_text = text.strip()
        if clean_text.startswith("```"):
            lines = clean_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()
        data = json.loads(clean_text)
        if isinstance(data, dict):
            return data
        raise ValueError(f"Expected dict from classifier, got {type(data)}")



