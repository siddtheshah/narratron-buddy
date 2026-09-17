"""Shared character generation and session character state."""

from __future__ import annotations

from collections import OrderedDict
import json
import logging
import re
from threading import Lock
from typing import Any, Dict, Optional

from jinja2 import Template

from providers import TextResponseProvider, TextResponseRequest
from services.quirk_service import get_quirk_generator_service
from tools.story.notepad import Notepad

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_CHARACTERS = 3
MAX_ACTIVE_CHARACTERS = 10
SUPPORTED_VOICE_TAGS = {"male", "female", "nonbinary"}

_CHARACTER_GEN_PROMPT_TEMPLATE = Template(
    """Character name: {{ name }}
{% if gender -%}
Character gender: {{ gender }}
{% endif -%}
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

Generate a compelling personality description, core motivation, explicit gender, and voice tags for this character in an adventure story experience.
The character's gender must be explicitly assigned: 'male', 'female', or 'nonbinary'.
Return ONLY a JSON object with keys 'personality' (string), 'motivation' (string), 'gender' (string: 'male', 'female', or 'nonbinary'), and 'voice_tags' (list of strings containing 'male', 'female', or 'nonbinary')."""
)


def normalize_voice_tags(tags: Any) -> list[str]:
    """Normalize input into a unique list of supported voice tags."""
    if not tags:
        return []
    if isinstance(tags, str):
        candidates = [tag.strip().lower().replace("-", "") for tag in re.split(r"[,\s]+", tags) if tag.strip()]
    elif isinstance(tags, (list, tuple, set)):
        candidates = [str(tag).strip().lower().replace("-", "") for tag in tags if str(tag).strip()]
    else:
        candidates = [str(tags).strip().lower().replace("-", "")]

    alias_map = {"nb": "nonbinary", "nonbinary": "nonbinary", "male": "male", "female": "female"}
    seen = set()
    result = []
    for candidate in candidates:
        mapped = alias_map.get(candidate)
        if mapped and mapped in SUPPORTED_VOICE_TAGS and mapped not in seen:
            seen.add(mapped)
            result.append(mapped)
    return result


class CharacterManager:
    """Own character generation, lookup, and session-scoped character state."""

    def __init__(
        self,
        text_response_provider: TextResponseProvider,
        notepad: Notepad,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")
        if notepad is None:
            raise ValueError("notepad is required.")

        self.text_response_provider = text_response_provider
        self.notepad = notepad
        self.config = config or {}
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
        gender: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a complete normalized NPC profile with explicit gender."""
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            return {"error": "Character name cannot be empty."}

        clean_description = str(description or "").strip()[:500]
        clean_personality = str(personality or "").strip()[:300]
        clean_motivation = str(motivation or "").strip()[:300]
        clean_quirk = str(quirk or "").strip()[:300]
        clean_gender = ""
        if gender:
            norm_g = normalize_voice_tags(gender)
            if norm_g:
                clean_gender = norm_g[0]

        clean_tags = normalize_voice_tags(voice_tags)
        if not clean_gender and clean_tags:
            clean_gender = clean_tags[0]
        elif clean_gender and clean_gender not in clean_tags:
            clean_tags.insert(0, clean_gender)

        if not clean_personality or not clean_motivation or not clean_tags or not clean_gender:
            elements = self.notepad.get_present_elements()
            request = TextResponseRequest(
                prompt=_CHARACTER_GEN_PROMPT_TEMPLATE.render(
                    name=clean_name,
                    gender=clean_gender,
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
                if not clean_gender:
                    gen_g = normalize_voice_tags(generated.get("gender"))
                    if gen_g:
                        clean_gender = gen_g[0]
                if not clean_tags:
                    clean_tags = normalize_voice_tags(generated.get("voice_tags"))
            except Exception as exc:
                logger.warning("[CharacterManager] Character profile generation failed: %s", exc)

        clean_personality = clean_personality or "Enigmatic and watchful."
        clean_motivation = clean_motivation or "Survive and prosper in the current scene."
        if not clean_gender:
            clean_gender = clean_tags[0] if clean_tags else "female"
        if not clean_tags:
            clean_tags = [clean_gender]
        elif clean_gender not in clean_tags:
            clean_tags.insert(0, clean_gender)

        if not clean_quirk:
            existing_quirks = [
                character.get("quirk", "")
                for character in self.get_present_characters()
                if character.get("quirk")
            ]
            clean_quirk = get_quirk_generator_service().get_random_quirk(
                exclude=existing_quirks,
            )

        return {
            "name": clean_name,
            "gender": clean_gender,
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
        gender: Optional[str] = None,
    ) -> str:
        profile = self.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
            gender=gender,
        )
        if "error" in profile:
            return str(profile["error"])

        with self._characters_lock:
            self._characters[profile["name"]] = profile
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
            gender = update.get("gender")
            voice_tags = update.get("voice_tags", update.get("voice_type") or gender)
            self.generate_character(
                name=name,
                description=str(update.get("description") or "").strip(),
                personality=str(update.get("personality") or "").strip(),
                motivation=str(update.get("motivation") or "").strip(),
                quirk=str(update.get("quirk") or "").strip(),
                voice_tags=voice_tags,
                gender=gender,
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
            clean_tags = normalize_voice_tags(
                character.get("voice_tags", character.get("voice_type") or character.get("gender"))
            )
            char_data = {
                "name": name,
                "description": str(character.get("description", "")),
                "personality": str(character.get("personality", "")),
                "motivation": str(character.get("motivation", "")),
                "quirk": str(character.get("quirk", "")),
                "voice_tags": clean_tags,
            }
            if character.get("gender"):
                char_data["gender"] = str(character["gender"])
            imported[name] = char_data

        with self._characters_lock:
            self._characters = imported

    def get_character_voice_tags(self, name: str) -> list[str]:
        """Look up normalized voice tags for a character by name."""
        with self._characters_lock:
            char = self._characters.get(name)
            if not char:
                normalized = str(name or "").strip().lower()
                char = next((c for k, c in self._characters.items() if k.strip().lower() == normalized), None)
            if char:
                tags = char.get("voice_tags")
                if tags:
                    return list(tags)
                gender = char.get("gender")
                if gender:
                    return [gender]
            return []


__all__ = [
    "CharacterManager",
    "DEFAULT_MAX_ACTIVE_CHARACTERS",
    "MAX_ACTIVE_CHARACTERS",
    "SUPPORTED_VOICE_TAGS",
    "normalize_voice_tags",
]
