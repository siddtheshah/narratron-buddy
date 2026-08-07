"""Service for generating structured story and scene suggestions using a fast Gemini model based on NamedElementTool context."""

import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def compute_elements_fingerprint(named_elements: List[Dict[str, str]]) -> str:
    """Compute a deterministic MD5 fingerprint for a list of named elements."""
    normalized = [
        {"name": str(elem.get("name", "")).strip(), "content": str(elem.get("content", "")).strip()}
        for elem in (named_elements or [])
    ]
    # Sort elements by name to produce a stable fingerprint
    normalized.sort(key=lambda x: x["name"])
    raw_str = json.dumps(normalized, sort_keys=True)
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


class SuggestionItem(BaseModel):
    title: str = Field(description="Short, punchy title for the story or scene suggestion")
    description: str = Field(description="Actionable or creative suggestion description")
    category: str = Field(description="Category of suggestion (e.g. Action, Setting, Plot Twist, Character)")


class SuggestionsResponse(BaseModel):
    suggestions: List[SuggestionItem] = Field(
        description="List of structured suggestions for the current scene"
    )


class SuggestionService:
    """Generates structured scene suggestions using a fast Gemini model based on NamedElementTool context."""

    _client_cache: Optional[genai.Client] = None

    def __init__(self, config: Optional[dict] = None, model: Optional[str] = None):
        self.config = config or {}
        # Default to cheap fast Gemini model (e.g. gemini-2.5-flash)
        self.model = model or self.config.get("suggestion_model", "gemini-2.5-flash")
        # In-memory cache mapping theater_id -> (fingerprint, SuggestionsResponse)
        self._cache: Dict[str, Tuple[str, SuggestionsResponse]] = {}

    def _get_client(self) -> genai.Client:
        if SuggestionService._client_cache is None:
            project_id = self.config.get("gcloud", {}).get("project_id", os.getenv("GOOGLE_CLOUD_PROJECT"))
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            SuggestionService._client_cache = genai.Client(vertexai=True, project=project_id, location=location)
        return SuggestionService._client_cache

    def generate_suggestions(
        self,
        named_elements: List[Dict[str, str]],
        theater_id: str = "default",
        force_refresh: bool = False,
        client_override: Optional[Any] = None,
    ) -> Tuple[SuggestionsResponse, str]:
        """Generate structured scene suggestions based on present named elements.
        
        Caches suggestions per theater until named elements are updated.
        Returns a tuple of (SuggestionsResponse, elements_fingerprint).
        """
        fingerprint = compute_elements_fingerprint(named_elements)

        if not force_refresh and theater_id in self._cache:
            cached_fp, cached_resp = self._cache[theater_id]
            if cached_fp == fingerprint:
                logger.debug(
                    "Returning cached suggestions for theater '%s' (fingerprint: %s)",
                    theater_id,
                    fingerprint,
                )
                return cached_resp, fingerprint

        client = client_override or self._get_client()

        prompt_lines = ["Current scene elements:"]
        if not named_elements:
            prompt_lines.append("(No named elements recorded yet in the current scene)")
        else:
            for elem in named_elements:
                name = elem.get("name", "Unknown")
                content = elem.get("content", "")
                prompt_lines.append(f"- {name}: {content}")

        prompt = "\n".join(prompt_lines)
        system_instruction = (
            "You are a fast narrative assistant for live storytelling. "
            "Based on the given scene elements (characters, locations, items, tone), generate 3 concise, creative suggestions."
            "Your suggestions MUST include a new scene element, such as a setting shift, plot twist, character, or action on screen."
            "Your suggestions should not be longer than 10 words. Avoid long prose, convey ideas directly."
        )

        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=SuggestionsResponse,
                temperature=0.7,
            ),
        )

        suggestions_res: Optional[SuggestionsResponse] = None

        if hasattr(response, "parsed") and response.parsed is not None:
            if isinstance(response.parsed, SuggestionsResponse):
                suggestions_res = response.parsed
            elif isinstance(response.parsed, dict):
                suggestions_res = SuggestionsResponse.model_validate(response.parsed)

        if suggestions_res is None and hasattr(response, "text") and response.text:
            suggestions_res = SuggestionsResponse.model_validate_json(response.text)

        if suggestions_res is None:
            raise ValueError("Failed to generate structured suggestions: Empty or invalid response from model.")

        # Cache the result for this theater and fingerprint
        self._cache[theater_id] = (fingerprint, suggestions_res)
        logger.info(
            "Generated fresh suggestions for theater '%s' (fingerprint: %s)",
            theater_id,
            fingerprint,
        )
        return suggestions_res, fingerprint

    def clear_cache(self, theater_id: Optional[str] = None) -> None:
        """Clear cached suggestions for a theater or all theaters."""
        if theater_id:
            self._cache.pop(theater_id, None)
        else:
            self._cache.clear()
