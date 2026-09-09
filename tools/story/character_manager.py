"""Shared character generation and session character state."""

from __future__ import annotations

from collections import OrderedDict
import json
import logging
import re
from threading import Lock
from typing import Any, Callable, Dict, Optional

from jinja2 import Template

from providers import TextResponseProvider, TextResponseRequest
from services.quirk_service import get_quirk_generator_service

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_CHARACTERS = 3
MAX_ACTIVE_CHARACTERS = 10
SUPPORTED_VOICE_TAGS = {"male", "female"}

_CHARACTER_GEN_PROMPT_TEMPLATE = Template(
    """Character name: {{ name }}
{% if description -%}
Character concept/description: {{ description }}
{% endif -%}
Your sticky notes:
{% if elements -%}
{% for elem in elements -%}
- {{ elem.topic or elem.name }}: {{ elem.info or elem.content }}
{% endfor -%}
{% else -%}
(No active sticky notes)
{% endif -%}

Generate a compelling personality description, core motivation, and voice tags for this character in an adventure story experience.
The only supported voice tags are 'male' or 'female'.
Return ONLY a JSON object with keys 'personality' (string), 'motivation' (string), and 'voice_tags' (list of strings with 'male' or 'female')."""
)


def normalize_voice_tags(tags: Any) -> list[str]:
    """Normalize input into a unique list of supported voice tags."""
    if not tags:
        return []
    if isinstance(tags, str):
        candidates = [tag.strip().lower() for tag in re.split(r"[,\s]+", tags) if tag.strip()]
    elif isinstance(tags, (list, tuple, set)):
        candidates = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    else:
        candidates = [str(tags).strip().lower()]

    seen = set()
    result = []
    for candidate in candidates:
        if candidate in SUPPORTED_VOICE_TAGS and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


class CharacterManager:
    """Own character generation, lookup, and session-scoped character state."""

    def __init__(
        self,
        text_response_provider: TextResponseProvider,
        config: Optional[Dict[str, Any]] = None,
        elements_provider: Optional[Callable[[], list[dict[str, str]]]] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")

        self.text_response_provider = text_response_provider
        self.config = config or {}
        self.elements_provider = elements_provider
        self.on_change = on_change
        self.max_active_characters = max(
            1,
            min(
                int(
                    self.config.get(
                        "max_active_characters", DEFAULT_MAX_ACTIVE_CHARACTERS
                    )
                ),
                MAX_ACTIVE_CHARACTERS,
            ),
        )
        self._characters: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._characters_lock = Lock()
        self.import_characters(self.config.get("initial_characters", {}))

    def count(self) -> int:
        with self._characters_lock:
            return len(self._characters)

    def get_present_characters(self) -> list[dict[str, Any]]:
        with self._characters_lock:
            return [
                dict(character)
                for character in list(self._characters.values())[-self.max_active_characters :]
            ]

    def lookup_character(self, query: str = "") -> str:
        """List all session characters or search by name or trait."""
        with self._characters_lock:
            characters = [dict(character) for character in self._characters.values()]

        if not characters:
            return "No characters have been encountered or introduced in this story yet."

        clean_query = str(query or "").strip().lower()
        matches = characters
        if clean_query:
            terms = re.findall(r"\w+", clean_query)
            matches = []
            for character in characters:
                searchable = " ".join(
                    [
                        character.get("name", ""),
                        character.get("description", ""),
                        character.get("personality", ""),
                        character.get("motivation", ""),
                        character.get("quirk", ""),
                        " ".join(character.get("voice_tags", [])),
                    ]
                ).lower()
                if any(term in searchable for term in terms):
                    matches.append(character)
            if not matches:
                return f"No characters matching '{query}' found in known session characters."

        heading = (
            f"Characters matching '{query}':"
            if clean_query
            else f"Characters encountered ({len(characters)} total):"
        )
        lines = [heading]
        for character in matches:
            tags = (
                f" [Voice: {', '.join(character['voice_tags'])}]"
                if character.get("voice_tags")
                else ""
            )
            description = (
                f" ({character['description']})" if character.get("description") else ""
            )
            lines.append(
                f"- {character['name']}{description}: "
                f"Personality: {character.get('personality', 'N/A')}, "
                f"Motivation: {character.get('motivation', 'N/A')}, "
                f"Quirk: {character.get('quirk', 'N/A')}{tags}"
            )
        return "\n".join(lines)

    def generate_character_profile(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
    ) -> Dict[str, Any]:
        """Generate a complete normalized NPC profile."""
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            return {"error": "Character name cannot be empty."}

        clean_description = str(description or "").strip()[:500]
        clean_personality = str(personality or "").strip()[:300]
        clean_motivation = str(motivation or "").strip()[:300]
        clean_quirk = str(quirk or "").strip()[:300]
        clean_tags = normalize_voice_tags(voice_tags)

        if not clean_personality or not clean_motivation or not clean_tags:
            elements = self.elements_provider() if self.elements_provider else []
            request = TextResponseRequest(
                prompt=_CHARACTER_GEN_PROMPT_TEMPLATE.render(
                    name=clean_name,
                    description=clean_description,
                    elements=elements,
                ),
                system_instruction=(
                    "You generate distinctive characters for interactive adventure stories."
                ),
                temperature=0.7,
            )
            try:
                response = self.text_response_provider.generate(request)
                raw_text = response.text.strip() if getattr(response, "text", None) else "{}"
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                generated = json.loads(raw_text)
                if not clean_personality:
                    clean_personality = str(generated.get("personality", "")).strip()[:300]
                if not clean_motivation:
                    clean_motivation = str(generated.get("motivation", "")).strip()[:300]
                if not clean_tags:
                    clean_tags = normalize_voice_tags(generated.get("voice_tags"))
            except Exception as exc:
                logger.warning("[CharacterManager] Character profile generation failed: %s", exc)

        clean_personality = clean_personality or "Enigmatic and watchful."
        clean_motivation = clean_motivation or "Survive and prosper in the current scene."
        clean_tags = clean_tags or ["female"]
        if not clean_quirk:
            clean_quirk = get_quirk_generator_service().generate_quirk(
                name=clean_name,
                personality=clean_personality,
                motivation=clean_motivation,
                description=clean_description,
            )

        return {
            "name": clean_name,
            "description": clean_description,
            "personality": clean_personality,
            "motivation": clean_motivation,
            "quirk": clean_quirk,
            "voice_tags": clean_tags,
        }

    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
    ) -> str:
        profile = self.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
        )
        if "error" in profile:
            return str(profile["error"])

        with self._characters_lock:
            self._characters[profile["name"]] = profile
        self._notify_change()
        tags = (
            f" [Voice: {', '.join(profile['voice_tags'])}]"
            if profile.get("voice_tags")
            else ""
        )
        return (
            f"Created character '{profile['name']}'. Personality: {profile['personality']}. "
            f"Motivation: {profile['motivation']}. Quirk: {profile['quirk']}{tags}."
        )

    def apply_character_updates(self, updates: Any) -> list[dict[str, Any]]:
        if not isinstance(updates, list):
            return []
        manifested = []
        for update in updates[:2]:
            if not isinstance(update, dict):
                continue
            name = str(update.get("name") or "").strip()
            if not name:
                continue
            self.generate_character(
                name=name,
                description=str(update.get("description") or "").strip(),
                personality=str(update.get("personality") or "").strip(),
                motivation=str(update.get("motivation") or "").strip(),
                quirk=str(update.get("quirk") or "").strip(),
                voice_tags=update.get("voice_tags", update.get("voice_type")),
            )
            manifested.extend(
                character
                for character in self.get_present_characters()
                if character["name"] == name
            )
        return manifested

    def clear_scene(self) -> int:
        """Clear active characters and return the number removed."""
        with self._characters_lock:
            count = len(self._characters)
            self._characters.clear()
        self._notify_change()
        return count

    def export_characters(self) -> list[dict[str, Any]]:
        with self._characters_lock:
            return [dict(character) for character in self._characters.values()]

    def import_characters(self, characters: Any) -> None:
        """Replace character state from either configured or persisted formats."""
        imported: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        if isinstance(characters, dict):
            values = [
                {**value, "name": name}
                for name, value in characters.items()
                if isinstance(value, dict)
            ]
        elif isinstance(characters, list):
            values = characters
        else:
            values = []

        for character in values:
            if not isinstance(character, dict) or "name" not in character:
                continue
            name = str(character["name"])
            imported[name] = {
                "name": name,
                "description": str(character.get("description", "")),
                "personality": str(character.get("personality", "")),
                "motivation": str(character.get("motivation", "")),
                "quirk": str(character.get("quirk", "")),
                "voice_tags": normalize_voice_tags(
                    character.get("voice_tags", character.get("voice_type"))
                ),
            }

        with self._characters_lock:
            self._characters = imported

    def _notify_change(self) -> None:
        if self.on_change:
            self.on_change()


__all__ = [
    "CharacterManager",
    "DEFAULT_MAX_ACTIVE_CHARACTERS",
    "MAX_ACTIVE_CHARACTERS",
    "SUPPORTED_VOICE_TAGS",
    "normalize_voice_tags",
]
