"""TextBeautifier component for applying visual and emotive text effects to adventure mode scenes.

Underneath, uses a fast lightweight language model (default: gemini-3.5-flash-lite)
to identify spans of high emotion, intensity, magic, or suspense, and apply
kinetic effects (vibrating, scintillating, glitching, flame, pulse, glow, wave)
and expressive fonts (cinematic, creepster, bangers, medieval, glitch).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

DEFAULT_BEAUTIFIER_MODEL = "gemini-3.5-flash-lite"

ALLOWED_EFFECTS = {
    "vibrate",
    "scintillate",
    "glitch",
    "flame",
    "pulse",
    "glow",
    "wave",
    "none",
}

ALLOWED_FONTS = {
    "cinematic",
    "creepster",
    "bangers",
    "medieval",
    "glitch",
    "default",
}


class TextSpanEffect(BaseModel):
    text: str = Field(description="Exact substring of text for this span.")
    effect: Optional[str] = Field(
        default="none",
        description="One of: 'vibrate', 'scintillate', 'glitch', 'flame', 'pulse', 'glow', 'wave', or 'none'.",
    )
    font: Optional[str] = Field(
        default="default",
        description="One of: 'cinematic', 'creepster', 'bangers', 'medieval', 'glitch', or 'default'.",
    )
    color: Optional[str] = Field(
        default=None,
        description="Optional hex color code (e.g. #ef4444, #38bdf8, #fbbf24, #a855f7) for dramatic emphasis.",
    )


class BeautifiedDialogueLine(BaseModel):
    speaker: str = Field(description="Name of the character speaking or thinking.")
    text: str = Field(description="Full text of the dialogue or thought line.")
    kind: str = Field(default="speech", description="Speech or thought.")
    spans: List[TextSpanEffect] = Field(
        default_factory=list,
        description="Sequential spans that together reconstruct the line's text with effects and fonts applied.",
    )


class BeautifiedSceneResponse(BaseModel):
    narration_spans: List[TextSpanEffect] = Field(
        default_factory=list,
        description="Sequential spans that reconstruct the narration text with effects and fonts applied.",
    )
    dialogue: List[BeautifiedDialogueLine] = Field(
        default_factory=list,
        description="List of dialogue lines with spans populated.",
    )


class SingleTextBeautifyResponse(BaseModel):
    spans: List[TextSpanEffect] = Field(
        default_factory=list,
        description="Sequential spans that reconstruct the text with effects and fonts applied.",
    )


class TextBeautifier:
    """Applies kinetic and typographical text effects to spans of story planner text."""

    def __init__(
        self,
        config: Optional[dict] = None,
        model: Optional[str] = None,
        client: Optional[Any] = None,
    ) -> None:
        self.config = config or {}
        self.model = (
            model
            or self.config.get("text_beautifier_model")
            or self.config.get("beautifier_model")
            or DEFAULT_BEAUTIFIER_MODEL
        )
        self._client: Optional[Any] = client

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client

        project_id = (
            self.config.get("vertex_project")
            or self.config.get("gcloud", {}).get("project_id")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
        )
        location = (
            self.config.get("vertex_location")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or "us-central1"
        )
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        has_creds = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        if not project_id and not api_key and not has_creds:
            raise RuntimeError("Gemini credentials not configured for TextBeautifier.")

        if project_id:
            self._client = genai.Client(vertexai=True, project=project_id, location=location)
        elif api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = genai.Client(vertexai=True)
        return self._client

    @staticmethod
    def _sanitize_span(span: TextSpanEffect | dict) -> Dict[str, Any]:
        if isinstance(span, TextSpanEffect):
            text = span.text
            effect = span.effect
            font = span.font
            color = span.color
        elif isinstance(span, dict):
            text = str(span.get("text") or "")
            effect = span.get("effect")
            font = span.get("font")
            color = span.get("color")
        else:
            return {"text": "", "effect": "none", "font": "default", "color": None}

        effect_str = str(effect or "none").lower().strip()
        if effect_str not in ALLOWED_EFFECTS:
            effect_str = "none"

        font_str = str(font or "default").lower().strip()
        if font_str not in ALLOWED_FONTS:
            font_str = "default"

        color_str = str(color).strip() if color else None

        return {
            "text": text,
            "effect": effect_str,
            "font": font_str,
            "color": color_str,
        }

    @staticmethod
    def _build_fallback_spans(text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        return [{"text": text, "effect": "none", "font": "default", "color": None}]

    def beautify_text(self, text: str) -> List[Dict[str, Any]]:
        """Annotate a single text string into spans with text effects and fonts."""
        if not isinstance(text, str):
            raise TypeError(f"text must be a str, got {type(text).__name__}")

        trimmed = text.strip()
        if not trimmed:
            return []

        prompt = (
            f"Annotate the following text into sequential spans, assigning text effects and font styles "
            f"to phrases that carry intense sensory or emotional weight:\n\n{text}"
        )
        system_instruction = (
            "You are a cinematic text effects director for an interactive adventure experience. "
            "Your task is to break the text into sequential spans that EXACTLY reconstruct the input text. "
            "Available effects:\n"
            "- 'vibrate': Trembling with terror, seismic rumbles, shouting, explosive impacts.\n"
            "- 'scintillate': Magical radiance, sparkling starlight, gleaming crystals or treasures.\n"
            "- 'glitch': Reality tearing, cybernetic corruption, eerie distortion.\n"
            "- 'flame': Fiery wrath, burning infernos, scorching heat.\n"
            "- 'pulse': Throbbing dread, heartbeat suspense, slow surging power.\n"
            "- 'glow': Soft ethereal halos, divine blessings, luminescent runes.\n"
            "- 'wave': Hypnotic water ripples, ghostly whispers, eerie melodic singing.\n"
            "- 'none': Neutral or normal narration/speech.\n\n"
            "Available fonts:\n"
            "- 'cinematic': Grand, regal, epic announcements.\n"
            "- 'creepster': Horrifying, spooky, monstrous dread.\n"
            "- 'bangers': Loud comic punch, sudden shouting.\n"
            "- 'medieval': Ancient arcane scrolls, runes, fantasy.\n"
            "- 'glitch': Futuristic, corrupted digital text.\n"
            "- 'default': Standard text.\n\n"
            "Apply effects selectively to the most poignant words or phrases. Do not alter words or punctuation."
        )

        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=SingleTextBeautifyResponse,
                    temperature=0.3,
                ),
            )
            parsed: Optional[SingleTextBeautifyResponse] = None
            if hasattr(response, "parsed") and response.parsed is not None:
                if isinstance(response.parsed, SingleTextBeautifyResponse):
                    parsed = response.parsed
                elif isinstance(response.parsed, dict):
                    parsed = SingleTextBeautifyResponse.model_validate(response.parsed)
            if parsed is None and hasattr(response, "text") and response.text:
                parsed = SingleTextBeautifyResponse.model_validate_json(response.text)

            if parsed and parsed.spans:
                cleaned_spans = [self._sanitize_span(s) for s in parsed.spans if s.text]
                if cleaned_spans:
                    return cleaned_spans
        except Exception as exc:
            logger.warning("[TextBeautifier] beautify_text model call failed: %s", exc)

        return self._build_fallback_spans(text)

    def beautify_scene(
        self, narration: str, dialogue: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Annotate narration and dialogue lines for an adventure scene in a single batch call.

        Returns:
            {
                "narration": str,
                "narration_spans": List[Dict[str, Any]],
                "dialogue": List[Dict[str, Any]], # with 'spans' added to each item
            }
        """
        if narration is None:
            narration = ""
        elif not isinstance(narration, str):
            raise TypeError(f"narration must be a str or None, got {type(narration).__name__}")

        if not isinstance(dialogue, list):
            raise TypeError(f"dialogue must be a list, got {type(dialogue).__name__}")

        for idx, item in enumerate(dialogue):
            if not isinstance(item, dict):
                raise TypeError(f"dialogue[{idx}] must be a dict, got {type(item).__name__}")

        clean_narration = narration.strip()
        cleaned_dialogue = [dict(item) for item in dialogue]

        # If empty scene, return immediately
        if not clean_narration and not cleaned_dialogue:
            return {
                "narration": narration,
                "narration_spans": [],
                "dialogue": cleaned_dialogue,
            }

        prompt_lines = ["Scene Elements to Beautify:"]
        if clean_narration:
            prompt_lines.append(f"NARRATION: {clean_narration}")
        for d in cleaned_dialogue:
            speaker = d.get("speaker") or "Narrator"
            kind = d.get("kind") or "speech"
            text = d.get("text") or ""
            prompt_lines.append(f"[{speaker}] ({kind}): {text}")

        prompt = "\n".join(prompt_lines)
        system_instruction = (
            "You are a cinematic text effects director for an interactive adventure experience. "
            "Annotate the scene's narration and dialogue lines into sequential spans with effects and fonts.\n"
            "Every line's spans MUST concatenate to reproduce the exact line text without changing words or punctuation.\n\n"
            "Effects:\n"
            "- 'vibrate': Extreme tremor, explosions, seismic terror, screaming.\n"
            "- 'scintillate': Magical radiance, twinkling crystals, celestial power.\n"
            "- 'glitch': Anomalies, digital corruption, eldritch fracture.\n"
            "- 'flame': Fiery inferno, burning rage, scorching heat.\n"
            "- 'pulse': Throbbing dread, heartbeat, rhythmic power surge.\n"
            "- 'glow': Radiant aura, mystical glow, holy blessing.\n"
            "- 'wave': Eerie float, water currents, hypnotic whisper.\n"
            "- 'none': Neutral / normal delivery.\n\n"
            "Fonts:\n"
            "- 'cinematic': Grand, dramatic, regal, serif.\n"
            "- 'creepster': Horrifying, spooky, grotesque.\n"
            "- 'bangers': Loud comic shouting, impact actions.\n"
            "- 'medieval': Ancient fantasy runes, grimoires.\n"
            "- 'glitch': Corrupted reality, futuristic tech.\n"
            "- 'default': Standard typography.\n\n"
            "Color (optional): e.g. '#ef4444' (fire/danger), '#38bdf8' (frost/magic), '#fbbf24' (radiance), '#a855f7' (arcane/shadow)."
        )

        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=BeautifiedSceneResponse,
                    temperature=0.3,
                ),
            )
            parsed: Optional[BeautifiedSceneResponse] = None
            if hasattr(response, "parsed") and response.parsed is not None:
                if isinstance(response.parsed, BeautifiedSceneResponse):
                    parsed = response.parsed
                elif isinstance(response.parsed, dict):
                    parsed = BeautifiedSceneResponse.model_validate(response.parsed)
            if parsed is None and hasattr(response, "text") and response.text:
                parsed = BeautifiedSceneResponse.model_validate_json(response.text)

            if parsed:
                narration_spans = [
                    self._sanitize_span(s) for s in parsed.narration_spans if s.text
                ]
                if not narration_spans and clean_narration:
                    narration_spans = self._build_fallback_spans(clean_narration)

                # Map dialogue spans
                result_dialogue = []
                for idx, orig in enumerate(cleaned_dialogue):
                    item = dict(orig)
                    if idx < len(parsed.dialogue) and parsed.dialogue[idx].spans:
                        item["spans"] = [
                            self._sanitize_span(s)
                            for s in parsed.dialogue[idx].spans
                            if s.text
                        ]
                    else:
                        item["spans"] = self._build_fallback_spans(orig.get("text", ""))
                    result_dialogue.append(item)

                return {
                    "narration": narration,
                    "narration_spans": narration_spans,
                    "dialogue": result_dialogue,
                }

        except Exception as exc:
            logger.warning("[TextBeautifier] beautify_scene model call failed: %s", exc)

        # Fallback if call fails
        return {
            "narration": narration,
            "narration_spans": self._build_fallback_spans(clean_narration),
            "dialogue": [
                {**item, "spans": self._build_fallback_spans(item.get("text", ""))}
                for item in cleaned_dialogue
            ],
        }
