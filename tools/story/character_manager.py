"""Shared character generation and session character state."""

from __future__ import annotations

import json
import logging
import re
from threading import Lock
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional

from PIL import Image

from jinja2 import Template
from pydantic import BaseModel, Field, field_validator, model_validator

from providers import (
    ImageGenerationRequest,
    ImageProvider,
    ImageProviderError,
    SpeechProvider,
    TextResponseProvider,
    TextResponseRequest,
)
from services.quirk_service import get_quirk_generator_service
from tools.image.image_library import ImageLibrary
from tools.story.notepad import Notepad
from utils.image_utils import embed_image_metadata

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_CHARACTERS = 3
MAX_ACTIVE_CHARACTERS = 10
SUPPORTED_VOICE_TAGS = {"male", "female", "nonbinary"}


class Character(BaseModel):
    """Canonical persisted identity record for an adventure NPC.

    The manager owns this schema because voice and visual identity are
    lifecycle concerns, not details of one particular story responder.
    ``voice_id`` and image binding fields are assigned by CharacterManager and
    survive subsequent character updates and session restoration.
    """

    name: str = Field(min_length=1, max_length=80)
    alias: str = ""
    gender: Literal["male", "female", "nonbinary"] = Field(
        description="Explicit voice-selection gender for this NPC."
    )
    description: str = ""
    personality: str = ""
    motivation: str = ""
    quirk: str = ""
    voice_type: str | None = None
    voice_tags: list[str] = Field(default_factory=list)
    voice_id: str | None = None
    image_reference: str | None = None
    image_reference_path: str | None = None
    image_reference_source: Literal["existing", "generated"] | None = None

    # Transitional mapping ergonomics for legacy callers. The object remains
    # the canonical Pydantic value; consumers should prefer attributes.
    def __getitem__(self, field: str) -> Any:
        return getattr(self, field)

    def __contains__(self, value: object) -> bool:
        if isinstance(value, str) and (value in type(self).model_fields or hasattr(self, value)):
            return True
        return str(value) in self.describe()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return self.model_dump(exclude_none=True) == other
        return super().__eq__(other)

    def get(self, field: str, default: Any = None) -> Any:
        if hasattr(self, field):
            val = getattr(self, field)
            return default if val is None and default is not None else val
        return default

    def items(self):
        return self.model_dump(exclude_none=True).items()

    def keys(self):
        return self.model_dump(exclude_none=True).keys()

    def values(self):
        return self.model_dump(exclude_none=True).values()

    def __iter__(self):
        return iter(self.model_dump(exclude_none=True))

    def __len__(self) -> int:
        return len(self.model_dump(exclude_none=True))

    def describe(self) -> str:
        tags = f" [Voice: {', '.join(self.voice_tags)}]" if self.voice_tags else ""
        return f"Created character '{self.name}'. Personality: {self.personality}. Motivation: {self.motivation}. Quirk: {self.quirk}{tags}."

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        clean = re.sub(r"\s+", " ", str(value or "").strip())
        if not clean:
            raise ValueError("Character name cannot be empty.")
        return clean

    @field_validator("gender", mode="before")
    @classmethod
    def normalize_gender(cls, value: Any) -> str | None:
        if value is None or not str(value).strip():
            raise ValueError("Character gender must be male, female, or nonbinary.")
        normalized = str(value).strip().lower().replace("-", "")
        aliases = {"m": "male", "man": "male", "f": "female", "woman": "female", "nb": "nonbinary"}
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def include_gender_voice_tag(self) -> "Character":
        if not self.voice_tags:
            self.voice_tags = [self.gender]
        elif self.gender not in self.voice_tags:
            self.voice_tags.insert(0, self.gender)
        return self

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
Voice tags select a speech voice. Use tags in the form `type=value`, choosing only from this provider's supported values:
{{ voice_tag_options }}
Always include `gender=<gender>` in voice_tags. You may include additional applicable tags.
Return ONLY a JSON object with keys 'personality' (string), 'motivation' (string), 'gender' (string: 'male', 'female', or 'nonbinary'), and 'voice_tags' (list of strings)."""
)


def normalize_voice_tags(
    tags: Any,
    supported_voice_tags: Mapping[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Normalize input into a unique list of supported voice tags."""
    if not tags:
        return []
    if isinstance(tags, str):
        candidates = []
        for tag in tags.split(","):
            clean_tag = tag.strip()
            if clean_tag:
                candidates.extend([clean_tag] if "=" in clean_tag else clean_tag.split())
    elif isinstance(tags, (list, tuple, set)):
        candidates = [str(tag).strip() for tag in tags if str(tag).strip()]
    else:
        candidates = [str(tags).strip()]

    alias_map = {"nb": "nonbinary", "nonbinary": "nonbinary", "male": "male", "female": "female"}
    seen = set()
    result = []
    supported = supported_voice_tags or {}
    for candidate in candidates:
        key, separator, value = candidate.partition("=")
        if separator:
            values = supported.get(key.strip())
            if values:
                match = next((item for item in values if item.lower() == value.strip().lower()), None)
                if match:
                    normalized = f"{key.strip()}={match}"
                    if normalized not in seen:
                        seen.add(normalized)
                        result.append(normalized)
            continue
        mapped = alias_map.get(candidate.lower().replace("-", ""))
        if mapped and mapped in SUPPORTED_VOICE_TAGS and mapped not in seen:
            seen.add(mapped)
            result.append(mapped)
    return result


def _voice_tag_gender(tags: list[str]) -> str:
    """Return the explicit or legacy gender represented by voice tags."""
    for tag in tags:
        if tag in SUPPORTED_VOICE_TAGS:
            return tag
        if tag.startswith("gender=") and tag.removeprefix("gender=") in SUPPORTED_VOICE_TAGS:
            return tag.removeprefix("gender=")
    return ""


class CharacterManager:
    """Own character generation, lookup, and session-scoped character state."""

    def __init__(
        self,
        text_response_provider: TextResponseProvider,
        notepad: Notepad,
        image_library: Optional[ImageLibrary] = None,
        image_provider: Optional[ImageProvider] = None,
        speech_provider: Optional[SpeechProvider] = None,
        config: Optional[Dict[str, Any]] = None,
        character_image_style: str = "",
    ) -> None:
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")
        if notepad is None:
            raise ValueError("notepad is required.")

        self.text_response_provider = text_response_provider
        self.notepad = notepad
        self.config = config or {}
        self.image_library: Optional[ImageLibrary] = image_library
        self.image_provider: Optional[ImageProvider] = image_provider
        self.speech_provider: Optional[SpeechProvider] = speech_provider
        self.character_image_style = str(character_image_style or "").strip()
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
        self._characters: dict[str, Character] = {}
        self._characters_lock = Lock()
        self.import_characters(self.config.get("initial_characters", {}))

    @staticmethod
    def _character_key(name: str) -> str:
        return re.sub(r"\s+", " ", str(name or "").strip()).casefold()

    @staticmethod
    def _alias_for_name(name: str) -> str:
        alias = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
        if not alias:
            raise ValueError("Character alias cannot be empty.")
        return alias

    def _existing_character(self, name_or_alias: str) -> Character | None:
        key = self._character_key(name_or_alias)
        with self._characters_lock:
            character = self._characters.get(str(name_or_alias))
            if character is not None:
                return character
            return next((character for alias, character in self._characters.items()
                         if self._character_key(alias) == key or self._character_key(character.name) == key), None)

    def _reference_entry(self, reference: Any) -> dict[str, str] | None:
        """Resolve a reference only by an exact stable identifier.

        Fuzzy matching is intentionally prohibited here: choosing a wrong
        portrait is worse than creating a new one for character cohesion.
        """
        if not reference or self.image_library is None:
            return None
        requested = str(reference).strip()
        if not requested:
            return None
        normalized = self._character_key(requested)
        filename = self._character_key(Path(requested).name)
        for entry in self.image_library.find_image_names():
            values = (entry.get("path", ""), entry.get("name", ""), entry.get("alias", ""))
            if any(self._character_key(value) == normalized for value in values):
                return entry
            if self._character_key(Path(entry.get("path", "")).name) == filename:
                return entry
        return None

    def _generate_character_reference(self, character: Character) -> dict[str, str] | None:
        if self.image_provider is None or self.image_library is None or not getattr(self.image_library, "reference_dir", None):
            return None
        prompt = (
            f"Single-character reference portrait of {character.name}. "
            f"Appearance: {character.description or character.personality or 'distinctive adventure character'}. "
            "Square head-and-shoulders portrait, clear face and silhouette, neutral background, "
            "consistent costume details, no text, no collage."
        )
        if self.character_image_style:
            prompt += f" Style: {self.character_image_style}."
        try:
            result = self.image_provider.generate(ImageGenerationRequest(prompt=prompt, aspect_ratio="1:1"))
            image = Image.open(BytesIO(result.image_bytes)).convert("RGB")
            alias = re.sub(r"[^a-zA-Z0-9_-]", "_", character.name).strip("_") or "character"
            output = Path(self.image_library.reference_dir) / f"{alias}_character.png"
            exif = image.getexif()
            embed_image_metadata(exif, f"Character reference for {character.name}. {prompt}")
            output.parent.mkdir(parents=True, exist_ok=True)
            image.save(output, "PNG", exif=exif)
            if hasattr(self.image_library, "_load_references"):
                self.image_library._load_references()
            return self._reference_entry(str(output))
        except (ImageProviderError, OSError, ValueError) as exc:
            logger.warning("[CharacterManager] Could not create portrait for %s: %s", character.name, exc)
        except Exception:
            logger.exception("[CharacterManager] Unexpected portrait failure for %s", character.name)
        return None

    def _bind_character_image(self, character: Character) -> None:
        if character.image_reference_path:
            return
        requested = character.image_reference
        # A character name is a safe fallback only when an image's name/alias
        # matches exactly; this never guesses from a semantic description.
        entry = self._reference_entry(requested) or self._reference_entry(character.name)
        source = "existing" if entry else "generated"
        if entry is None:
            entry = self._generate_character_reference(character)
        if entry is None:
            return
        character.image_reference = entry.get("alias", "")
        character.image_reference_path = entry.get("path", "")
        character.image_reference_source = source

    def _bind_character_voice(self, character: Character) -> None:
        if character.voice_id or self.speech_provider is None:
            return
        with self._characters_lock:
            used = {
                str(candidate.voice_id) for candidate in self._characters.values()
                if candidate.voice_id and self._character_key(candidate.name) != self._character_key(character.name)
            }
        try:
            character.voice_id = self.speech_provider.select_voice(
                {"voice_tags": character.voice_tags, "description": self._profile_description(character)},
                exclude=used,
            )
        except Exception as exc:
            logger.warning("[CharacterManager] Could not select voice for %s: %s", character.name, exc)

    @staticmethod
    def _profile_description(character: Character) -> str:
        return " ".join((
            character.name, character.description, character.personality,
            character.motivation, character.quirk,
        )).strip()

    def _ensure_character_bindings(self, character: Character) -> None:
        self._bind_character_image(character)
        self._bind_character_voice(character)

    def count(self) -> int:
        with self._characters_lock:
            return len(self._characters)

    def get_present_characters(self) -> list[Character]:
        with self._characters_lock:
            return [
                character
                for character in list(self._characters.values())[-self.max_active_characters :]
            ]

    def lookup_character(self, query: str = "") -> str:
        """List all session characters or search by name or trait."""
        with self._characters_lock:
            characters = list(self._characters.values())

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
                        character.name, character.description, character.personality,
                        character.motivation, character.quirk, " ".join(character.voice_tags),
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
                f" [Voice: {', '.join(character.voice_tags)}]"
                if character.voice_tags
                else ""
            )
            description = (
                f" ({character.description})" if character.description else ""
            )
            lines.append(
                f"- {character.name}{description}: Personality: {character.personality or 'N/A'}, "
                f"Motivation: {character.motivation or 'N/A'}, Quirk: {character.quirk or 'N/A'}{tags}"
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
    ) -> Character | None:
        """Generate a complete normalized NPC profile with explicit gender."""
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            return None

        clean_description = str(description or "").strip()[:500]
        clean_personality = str(personality or "").strip()[:300]
        clean_motivation = str(motivation or "").strip()[:300]
        clean_quirk = str(quirk or "").strip()[:300]
        clean_gender = ""
        if gender:
            norm_g = normalize_voice_tags(gender)
            if norm_g:
                clean_gender = norm_g[0]

        supported_tags = self._supported_voice_tags()
        clean_tags = normalize_voice_tags(voice_tags, supported_tags)
        tag_gender = _voice_tag_gender(clean_tags)
        if not clean_gender and tag_gender:
            clean_gender = tag_gender
        elif clean_gender and not tag_gender:
            clean_tags.insert(0, clean_gender)

        if not clean_personality or not clean_motivation or not clean_tags or not clean_gender:
            elements = self.notepad.get_present_elements()
            request = TextResponseRequest(
                prompt=_CHARACTER_GEN_PROMPT_TEMPLATE.render(
                    name=clean_name,
                    gender=clean_gender,
                    description=clean_description,
                    elements=elements,
                    voice_tag_options=self._format_voice_tag_options(supported_tags),
                ),
                system_instruction=(
                    "You generate distinctive characters for interactive adventure stories."
                ),
                temperature=0.7,
            )
            try:
                response = self.text_response_provider.generate(request)
                raw_text = response.text.strip() if response.text else "{}"
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
                    clean_tags = normalize_voice_tags(generated.get("voice_tags"), supported_tags)
            except Exception as exc:
                logger.warning("[CharacterManager] Character profile generation failed: %s", exc)

        clean_personality = clean_personality or "Enigmatic and watchful."
        clean_motivation = clean_motivation or "Survive and prosper in the current scene."
        if not clean_gender:
            clean_gender = clean_tags[0] if clean_tags else "female"
        if not clean_tags:
            clean_tags = [clean_gender]
        elif not _voice_tag_gender(clean_tags):
            clean_tags.insert(0, clean_gender)

        if not clean_quirk:
            existing_quirks = [
                character.quirk
                for character in self.get_present_characters()
                if character.quirk
            ]
            clean_quirk = get_quirk_generator_service().get_random_quirk(
                exclude=existing_quirks,
            )

        return Character(name=clean_name, alias=self._alias_for_name(clean_name), gender=clean_gender,
                         description=clean_description, personality=clean_personality,
                         motivation=clean_motivation, quirk=clean_quirk, voice_tags=clean_tags)

    def _supported_voice_tags(self) -> Mapping[str, tuple[str, ...]]:
        if self.speech_provider is None:
            return {"gender": ("female", "male", "nonbinary")}
        try:
            return self.speech_provider.get_supported_voice_tags()
        except Exception as exc:
            logger.warning("[CharacterManager] Unable to list speech voice tags: %s", exc)
            return {"gender": ("female", "male", "nonbinary")}

    @staticmethod
    def _format_voice_tag_options(tags: Mapping[str, tuple[str, ...]]) -> str:
        options = [f"- {field}: {', '.join(values)}" for field, values in sorted(tags.items()) if values]
        return "\n".join(options) or "- gender: female, male, nonbinary"

    def generate_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
        gender: Optional[str] = None,
        image_reference: str = "",
    ) -> Character | None:
        character = self.generate_character_profile(
            name=name,
            description=description,
            personality=personality,
            motivation=motivation,
            quirk=quirk,
            voice_tags=voice_tags,
            gender=gender,
        )
        if character is None:
            return None

        # A profile update must retain identity bindings unless it explicitly
        # supplies a replacement reference.  This prevents a later scene turn
        # from silently changing a character's face or voice.
        existing = self._existing_character(character.name)
        if existing:
            for field in ("voice_id", "image_reference", "image_reference_path", "image_reference_source"):
                value = getattr(existing, field)
                if value:
                    setattr(character, field, value)
        if image_reference:
            character.image_reference = str(image_reference).strip()
            character.image_reference_path = None
            character.image_reference_source = None
        self._ensure_character_bindings(character)
        with self._characters_lock:
            self._characters[character.alias] = character
        return character

    def apply_character_updates(self, updates: Any) -> list[Character]:
        if not isinstance(updates, list):
            return []
        manifested = []
        for update in updates[:2]:
            if isinstance(update, Character):
                update = update.model_dump(exclude_none=True)
            if not isinstance(update, dict):
                continue
            name = str(update.get("name") or "").strip()
            if not name:
                continue
            gender = update.get("gender")
            voice_tags = update.get("voice_tags", update.get("voice_type") or gender)
            character = self.generate_character(
                name=name,
                description=str(update.get("description") or "").strip(),
                personality=str(update.get("personality") or "").strip(),
                motivation=str(update.get("motivation") or "").strip(),
                quirk=str(update.get("quirk") or "").strip(),
                voice_tags=voice_tags,
                gender=gender,
                image_reference=str(update.get("image_reference") or update.get("reference_image") or "").strip(),
            )
            if character is not None:
                manifested.append(character)
        return manifested

    def clear_scene(self) -> int:
        """Clear active characters and return the number removed."""
        with self._characters_lock:
            count = len(self._characters)
            self._characters.clear()
        return count

    def export_characters(self) -> list[Character]:
        with self._characters_lock:
            return [character.model_copy(deep=True) for character in self._characters.values()]

    def import_characters(self, characters: Any) -> None:
        """Replace character state from either configured or persisted formats."""
        imported: dict[str, Character] = {}
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
                "gender": str(character.get("gender") or _voice_tag_gender(clean_tags) or "female"),
                "description": str(character.get("description", "")),
                "personality": str(character.get("personality", "")),
                "motivation": str(character.get("motivation", "")),
                "quirk": str(character.get("quirk", "")),
                "voice_tags": clean_tags,
            }
            for field in ("voice_id", "image_reference", "image_reference_path", "image_reference_source"):
                if character.get(field):
                    char_data[field] = str(character[field])
            char_data["alias"] = str(character.get("alias") or self._alias_for_name(name))
            character_model = Character.model_validate(char_data)
            self._ensure_character_bindings(character_model)
            imported[character_model.alias] = character_model

        with self._characters_lock:
            self._characters = imported

    def get_character_voice_tags(self, name: str) -> list[str]:
        """Look up normalized voice tags for a character by name."""
        with self._characters_lock:
            char = self._characters.get(str(name))
            if not char:
                normalized = str(name or "").strip().lower()
                char = next((c for alias, c in self._characters.items() if alias.lower() == normalized or c.name.strip().lower() == normalized), None)
            if char:
                tags = char.voice_tags
                if tags:
                    return list(tags)
                gender = char.gender
                if gender:
                    return [gender]
            return []

    def get_character_voice_id(self, name: str) -> str | None:
        """Return the manager-owned durable voice identifier for a character."""
        character = self._existing_character(name)
        voice_id = character.voice_id if character else None
        return str(voice_id) if voice_id else None


__all__ = [
    "Character",
    "CharacterManager",
    "DEFAULT_MAX_ACTIVE_CHARACTERS",
    "MAX_ACTIVE_CHARACTERS",
    "SUPPORTED_VOICE_TAGS",
    "normalize_voice_tags",
]
