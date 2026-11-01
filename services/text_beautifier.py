"""TextBeautifier service for applying visual and emotive text effects to adventure mode scenes.

Underneath, uses a TextResponseProvider (default: gemini-3.5-flash-lite)
or a DecisionProvider (when supplied)
to identify spans of high emotion, intensity, magic, or suspense, and apply
kinetic effects (vibrating, glitching, flame, pulse, glow, wave, drip)
and expressive fonts (cinematic, lacquer, bangers, medieval, glitch).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from typing import Any, Dict, List, Optional
from jinja2 import Template
from pydantic import BaseModel, Field

from providers.decision_provider import (
    ChoiceQuestion,
    DecisionProvider,
    DecisionRequest,
)
from providers.text_response_provider import (
    TextResponseRequest,
)
from providers.registry import get_text_response_provider

logger = logging.getLogger(__name__)

EFFECTS: Dict[str, str] = {
    "vibrate": "Trembling with terror, seismic rumbles, shouting, explosive impacts.",
    "glitch": "Reality tearing, cybernetic corruption, eerie distortion.",
    "flame": "Fiery wrath, burning infernos, scorching heat.",
    "pulse": "Throbbing dread, heartbeat suspense, slow surging power.",
    "glow": "Soft ethereal halos, divine blessings, luminescent runes.",
    "wave": "Hypnotic water ripples, ghostly whispers, eerie melodic singing.",
    "drip": "Viscous horror, messages written in blood, ominous oozing dread.",
    "none": "Neutral or normal delivery.",
}

FONTS: Dict[str, str] = {
    "cinematic": "Grand, regal, epic announcements.",
    "lacquer": "Horrifying, psychological dread, raw dripping scratch.",
    "bangers": "Loud comic punch, sudden shouting.",
    "medieval": "Ancient arcane scrolls, runes, fantasy.",
    "glitch": "Futuristic, corrupted digital text.",
    "default": "Standard typography.",
}

ALLOWED_EFFECTS = set(EFFECTS.keys())
ALLOWED_FONTS = set(FONTS.keys())


@dataclass(frozen=True)
class _CandidateSpan:
    name: str
    text: str
    start: int
    end: int


def _generate_candidate_spans(text: str) -> list[_CandidateSpan]:
    candidates: list[_CandidateSpan] = []

    # 1. Look for uppercase phrases of 2+ words/letters, or quoted text
    pattern = re.compile(
        r'\b[A-Z]{2,}(?:\s+[A-Z]{2,})*[!?]*|"[^"\n]+"|\'[^\'\n]+\''
    )
    matches: list[tuple[int, int]] = []
    for m in pattern.finditer(text):
        matches.append((m.start(), m.end()))

    if matches:
        cursor = 0
        for m_start, m_end in matches:
            if m_start > cursor:
                prefix_text = text[cursor:m_start]
                if prefix_text.strip():
                    candidates.append(
                        _CandidateSpan(
                            name=f"span_{len(candidates) + 1}",
                            text=prefix_text.strip(),
                            start=cursor,
                            end=m_start,
                        )
                    )
            match_text = text[m_start:m_end]
            if match_text.strip():
                candidates.append(
                    _CandidateSpan(
                        name=f"span_{len(candidates) + 1}",
                        text=match_text.strip(),
                        start=m_start,
                        end=m_end,
                    )
                )
            cursor = m_end
        if cursor < len(text):
            suffix_text = text[cursor:]
            if suffix_text.strip():
                candidates.append(
                    _CandidateSpan(
                        name=f"span_{len(candidates) + 1}",
                        text=suffix_text.strip(),
                        start=cursor,
                        end=len(text),
                    )
                )
    else:
        # 2. Try splitting by sentence or clause punctuation (. ? ! , ; : —)
        clause_pattern = re.compile(r'[^,;:.!?—\n]+[,;:.!?—\n]*')
        clause_matches = [
            (m.start(), m.end())
            for m in clause_pattern.finditer(text)
            if text[m.start():m.end()].strip()
        ]
        if len(clause_matches) > 1:
            for m_start, m_end in clause_matches:
                while m_start < m_end and text[m_start].isspace():
                    m_start += 1
                while m_end > m_start and text[m_end - 1].isspace():
                    m_end -= 1
                cand_text = text[m_start:m_end]
                if cand_text:
                    candidates.append(
                        _CandidateSpan(
                            name=f"span_{len(candidates) + 1}",
                            text=cand_text,
                            start=m_start,
                            end=m_end,
                        )
                    )

    if not candidates:
        candidates.append(
            _CandidateSpan(
                name="span_1",
                text=text.strip(),
                start=0,
                end=len(text),
            )
        )

    return candidates


class TextSpanEffect(BaseModel):
    text: str = Field(description="Exact substring of text for this span.")
    effect: Optional[str] = Field(
        default="none",
        description="Kinetic text effect name from available effects.",
    )
    font: Optional[str] = Field(
        default="default",
        description="Font typography style name from available fonts.",
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


_BEAUTIFIER_PROMPT_TEMPLATE = Template(
"""
# Objective
    You are a cinematic text effects director for an interactive adventure experience.
Your task is to break the text into sequential spans that EXACTLY reconstruct the input text without changing words or punctuation.

# Guidelines
Do not apply effects to more than two spans total. More than that is too chaotic.
For span length, choose either a short 1-2 word span, or cover the entire line. Intermediate ranges are
not appealing.

# Effects and Fonts

Available effects:
{% for name, desc in effects.items() -%}
- '{{ name }}': {{ desc }}
{% endfor %}
Available fonts:
{% for name, desc in fonts.items() -%}
- '{{ name }}': {{ desc }}
{% endfor %}
Color (optional): hex code for dramatic emphasis (e.g. #ef4444, #38bdf8, #fbbf24, #a855f7).
Apply effects selectively to the most poignant words or phrases.

{% if is_scene or mode == 'scene' -%}
Annotate the scene's narration and dialogue lines into sequential spans with effects and fonts:

Scene Elements to Beautify:
{% if narration -%}
NARRATION: {{ narration }}
{% endif -%}
{% for item in dialogue -%}
[{{ item.speaker or 'Narrator' }}] ({{ item.kind or 'speech' }}): {{ item.text or '' }}
{% endfor -%}
{% else -%}
Annotate the following text into sequential spans, assigning text effects and font styles to phrases that carry intense sensory or emotional weight:

{{ text }}
{% endif %}"""
)

# Aliases for compatibility
_BEAUTIFY_TEXT_PROMPT_TEMPLATE = _BEAUTIFIER_PROMPT_TEMPLATE
_BEAUTIFY_SCENE_PROMPT_TEMPLATE = _BEAUTIFIER_PROMPT_TEMPLATE


def build_beautify_prompt(
    is_scene: bool = False,
    mode: Optional[str] = None,
    text: Optional[str] = None,
    narration: Optional[str] = None,
    dialogue: Optional[List[Dict[str, Any]]] = None,
    effects: Optional[Dict[str, str]] = None,
    fonts: Optional[Dict[str, str]] = None,
) -> str:
    """Render beautification prompt selecting single text or scene stanza via conditional variable."""
    return _BEAUTIFIER_PROMPT_TEMPLATE.render(
        is_scene=is_scene,
        mode=mode or ("scene" if is_scene else "text"),
        text=text or "",
        narration=narration or "",
        dialogue=dialogue or [],
        effects=effects or EFFECTS,
        fonts=fonts or FONTS,
    ).strip()


def build_beautify_text_prompt(
    text: str,
    effects: Optional[Dict[str, str]] = None,
    fonts: Optional[Dict[str, str]] = None,
) -> str:
    """Render single text beautification prompt binding all variables at once."""
    return build_beautify_prompt(
        is_scene=False,
        text=text,
        effects=effects,
        fonts=fonts,
    )


def build_beautify_scene_prompt(
    narration: str,
    dialogue: List[Dict[str, Any]],
    effects: Optional[Dict[str, str]] = None,
    fonts: Optional[Dict[str, str]] = None,
) -> str:
    """Render adventure scene beautification prompt binding all variables at once."""
    return build_beautify_prompt(
        is_scene=True,
        narration=narration,
        dialogue=dialogue,
        effects=effects,
        fonts=fonts,
    )


class TextBeautifier:
    """Applies kinetic and typographical text effects to spans of story planner text."""

    def __init__(
        self,
        config: Optional[dict] = None,
        model: Optional[str] = None,
        decision_provider: Optional[DecisionProvider] = None,
    ) -> None:
        if config is not None and not isinstance(config, dict):
            raise TypeError(f"config must be a dict or None, got {type(config).__name__}")

        self.config: Dict[str, Any] = config or {}
        self.model = model or self.config.get("text_beautifier_model", "gemini-3.5-flash-lite")
        self._decision_provider = decision_provider
        self.text_provider = get_text_response_provider(
            "gemini-2-5", options={"model": self.model}
        )

    @property
    def decision_provider(self) -> Optional[DecisionProvider]:
        return self._decision_provider

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
        if font_str == "creepster":
            font_str = "lacquer"
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

    def _beautify_text_with_decision_provider(
        self, text: str
    ) -> List[Dict[str, Any]]:
        if self._decision_provider is None:
            return self._build_fallback_spans(text)

        candidates = _generate_candidate_spans(text)
        candidate_map = {c.name: c for c in candidates}
        span_names = tuple(c.name for c in candidates)

        candidate_lines = [f'- {c.name}: "{c.text}"' for c in candidates]
        evidence = "Candidate Narration Spans:\n" + "\n".join(candidate_lines)

        request = DecisionRequest(
            input_text=evidence,
            questions=(
                ChoiceQuestion(
                    name="highlight_span",
                    instructions="Which candidate span carries the peak kinetic/emotional impact and should receive visual text styling?",
                    choices=(*span_names, "none"),
                ),
                ChoiceQuestion(
                    name="kinetic_effect",
                    instructions="Select the kinetic text animation effect for the highlighted span.",
                    choices=("none", "vibrate", "glitch", "flame", "pulse", "glow", "wave", "drip"),
                ),
                ChoiceQuestion(
                    name="display_font",
                    instructions="Select the display font family for the highlighted span.",
                    choices=("default", "cinematic", "lacquer", "bangers", "medieval", "glitch"),
                ),
            ),
        )

        result = self._decision_provider.decide(request)
        highlight_span = result.get_choice("highlight_span")
        kinetic_effect = result.get_choice("kinetic_effect")
        display_font = result.get_choice("display_font")

        if highlight_span.choice != "none" and highlight_span.choice in candidate_map:
            chosen = candidate_map[highlight_span.choice]
            prefix = text[: chosen.start]
            span_text = text[chosen.start : chosen.end]
            suffix = text[chosen.end :]

            effect = (
                kinetic_effect.choice
                if kinetic_effect.choice in ALLOWED_EFFECTS
                else "none"
            )
            font = (
                display_font.choice
                if display_font.choice in ALLOWED_FONTS
                else "default"
            )

            spans: List[Dict[str, Any]] = []
            if prefix:
                spans.append({"text": prefix, "effect": "none", "font": "default", "color": None})
            spans.append(
                {
                    "text": span_text,
                    "effect": effect,
                    "font": font,
                    "color": None,
                }
            )
            if suffix:
                spans.append({"text": suffix, "effect": "none", "font": "default", "color": None})
            return spans

        return [{"text": text, "effect": "none", "font": "default", "color": None}]

    def beautify_text(self, text: str) -> List[Dict[str, Any]]:
        """Annotate a single text string into spans with text effects and fonts."""
        if not isinstance(text, str):
            raise TypeError(f"text must be a str, got {type(text).__name__}")

        trimmed = text.strip()
        if not trimmed:
            return []

        if self._decision_provider is not None:
            return self._beautify_text_with_decision_provider(text)

        start_t = time.time()
        logger.info(
            "[TextBeautifier] beautify_text starting (model=%s, %d chars): '%.60s'",
            self.model,
            len(text),
            text,
        )

        prompt = build_beautify_text_prompt(text)

        try:
            request = TextResponseRequest(
                prompt=prompt,
                response_schema=SingleTextBeautifyResponse,
                temperature=0.3,
            )
            response = self.text_provider.generate(request)

            parsed: Optional[SingleTextBeautifyResponse] = None
            if response.parsed is not None:
                if isinstance(response.parsed, SingleTextBeautifyResponse):
                    parsed = response.parsed
                elif isinstance(response.parsed, dict):
                    parsed = SingleTextBeautifyResponse.model_validate(response.parsed)
            if parsed is None and response.text:
                parsed = SingleTextBeautifyResponse.model_validate_json(response.text)

            if parsed and parsed.spans:
                cleaned_spans = [self._sanitize_span(s) for s in parsed.spans if s.text]
                if cleaned_spans:
                    elapsed = time.time() - start_t
                    applied = [
                        f"'{s['text']}' -> {s['effect']}/{s['font']}"
                        + (f"({s['color']})" if s.get("color") else "")
                        for s in cleaned_spans
                        if s.get("effect") != "none" or s.get("font") != "default"
                    ]
                    logger.info(
                        "[TextBeautifier] beautify_text succeeded in %.2fs returning %d span(s)%s",
                        elapsed,
                        len(cleaned_spans),
                        f" (applied: {', '.join(applied)})" if applied else " (all default/none)",
                    )
                    return cleaned_spans
            logger.warning(
                "[TextBeautifier] beautify_text: model returned no spans, falling back to plain span"
            )
        except Exception as exc:
            elapsed = time.time() - start_t
            logger.warning(
                "[TextBeautifier] beautify_text model call failed in %.2fs: %s (falling back to plain spans)",
                elapsed,
                exc,
            )

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

        if self._decision_provider is not None:
            narration_spans = self.beautify_text(narration) if clean_narration else []
            result_dialogue: List[Dict[str, Any]] = []
            for item in cleaned_dialogue:
                line_item = dict(item)
                line_text = str(line_item.get("text", "") or "")
                line_item["spans"] = self.beautify_text(line_text) if line_text.strip() else []
                result_dialogue.append(line_item)
            return {
                "narration": narration,
                "narration_spans": narration_spans,
                "dialogue": result_dialogue,
            }

        start_t = time.time()
        logger.info(
            "[TextBeautifier] beautify_scene starting (model=%s): narration_len=%d, dialogue_count=%d",
            self.model,
            len(clean_narration),
            len(cleaned_dialogue),
        )

        prompt = build_beautify_scene_prompt(
            narration=clean_narration,
            dialogue=cleaned_dialogue,
        )

        try:
            request = TextResponseRequest(
                prompt=prompt,
                response_schema=BeautifiedSceneResponse,
                temperature=0.3,
            )
            response = self.text_provider.generate(request)

            parsed: Optional[BeautifiedSceneResponse] = None
            if response.parsed is not None:
                if isinstance(response.parsed, BeautifiedSceneResponse):
                    parsed = response.parsed
                elif isinstance(response.parsed, dict):
                    parsed = BeautifiedSceneResponse.model_validate(response.parsed)
            if parsed is None and response.text:
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

                elapsed = time.time() - start_t
                logger.info(
                    "[TextBeautifier] beautify_scene succeeded in %.2fs: narration_spans=%d, dialogue_lines=%d",
                    elapsed,
                    len(narration_spans),
                    len(result_dialogue),
                )
                return {
                    "narration": narration,
                    "narration_spans": narration_spans,
                    "dialogue": result_dialogue,
                }
            logger.warning(
                "[TextBeautifier] beautify_scene: model response unparsed, falling back to plain spans"
            )

        except Exception as exc:
            elapsed = time.time() - start_t
            logger.warning(
                "[TextBeautifier] beautify_scene model call failed in %.2fs: %s (falling back to plain spans)",
                elapsed,
                exc,
            )

        # Fallback if call fails
        return {
            "narration": narration,
            "narration_spans": self._build_fallback_spans(clean_narration),
            "dialogue": [
                {**item, "spans": self._build_fallback_spans(item.get("text", ""))}
                for item in cleaned_dialogue
            ],
        }
