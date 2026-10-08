"""Shared character generation and session character state."""

from __future__ import annotations

import logging
import os
import re
import shutil
from threading import Lock
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

from PIL import Image

from jinja2 import Template
from pydantic import BaseModel, Field, field_validator, model_validator

from providers import (
    ImageGenerationRequest,
    ImageProvider,
    ImageProviderError,
    ImageReference,
    SpeechProvider,
    TextResponseProvider,
    TextResponseRequest,
)
from components.canvas.story_state import CharacterState, PlayerCharacterState, StoryState
from services.quirk_service import get_quirk_generator_service
from components.notepad import Notepad
from components.theater_manager import Theater
from utils.image_utils import embed_image_metadata

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_CHARACTERS = 5
MAX_ACTIVE_CHARACTERS = 10
SUPPORTED_VOICE_TAGS = {"male", "female", "nonbinary"}
IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".webp"}


def parse_iteration_number(stem_or_filename: str) -> int | None:
    """Parse numeric iteration from a filename or stem (e.g. '1', '02', 'iteration_3')."""
    stem = Path(stem_or_filename).stem.strip()
    if stem.isdigit():
        return int(stem)
    match = re.search(r"(?:^|[\D_])(\d+)(?:$|[\D_])", stem)
    if match is not None:
        return int(match.group(1))
    return None


def get_character_folder_name(name: str) -> str:
    """Produce a filesystem-safe folder name for character references."""
    clean = re.sub(r'[<>:"/\\|?*]', '_', str(name or "")).strip()
    clean = re.sub(r"\s+", "_", clean).strip("_")
    return clean or "character"


def get_character_reference_dir(reference_dir: Path | str, name: str) -> Path:
    """Return the directory under reference_dir for the character's reference iterations."""
    ref_path = Path(reference_dir)
    folder_name = get_character_folder_name(name)
    raw_name = str(name or "").strip()
    for candidate in (folder_name, raw_name):
        if candidate:
            candidate_dir = ref_path / candidate
            if candidate_dir.is_dir():
                return candidate_dir
    return ref_path / folder_name


def get_character_iteration_files(char_dir: Path) -> list[tuple[int, Path]]:
    """Return all image iteration files in char_dir sorted by iteration number."""
    if not char_dir.is_dir():
        return []
    iterations: list[tuple[int, Path]] = []
    for item in char_dir.iterdir():
        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
            num = parse_iteration_number(item.name)
            if num is not None:
                iterations.append((num, item))
            else:
                iterations.append((0, item))
    iterations.sort(key=lambda t: (t[0], t[1].stat().st_mtime if t[1].exists() else 0.0))
    return iterations


def get_latest_iteration_file(char_dir: Path) -> Path | None:
    """Return the file path of the highest iteration in char_dir, if any."""
    iterations = get_character_iteration_files(char_dir)
    return iterations[-1][1] if iterations else None


def get_next_iteration_number(char_dir: Path) -> int:
    """Return the next numeric iteration index for char_dir."""
    iterations = get_character_iteration_files(char_dir)
    if not iterations:
        return 1
    max_num = max(num for num, _ in iterations)
    return max_num + 1 if max_num >= 1 else 1


class Character(BaseModel):
    """Canonical persisted identity record for an adventure NPC.

    The manager owns this schema because voice and visual identity are
    lifecycle concerns, not details of one particular story responder.
    ``voice_id`` and image binding fields are assigned by ReferenceManager and
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

    def for_model_context(self) -> Character:
        """Expose character traits while keeping image bindings internal."""
        return self.model_copy(update={
            "image_reference": None, "image_reference_path": None, "image_reference_source": None,
        })

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

    @model_validator(mode="before")
    @classmethod
    def _coerce_character_inputs(cls, data: object) -> object:
        if isinstance(data, dict):
            d = dict(data)
            name = str(d.get("name") or "").strip()
            if not d.get("alias") and name:
                d["alias"] = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower()).strip("_")
            if "voice_tags" in d and isinstance(d["voice_tags"], str):
                d["voice_tags"] = [t.strip() for t in d["voice_tags"].split(",") if t.strip()]
            if not d.get("gender"):
                raw_tags = d.get("voice_tags", d.get("voice_type"))
                clean_tags = normalize_voice_tags(raw_tags)
                tag_g = _voice_tag_gender(clean_tags)
                if tag_g:
                    d["gender"] = tag_g
            if not d.get("image_reference") and d.get("reference_image"):
                d["image_reference"] = d.get("reference_image")
            d["voice_type"] = None
            return d
        return data

    @model_validator(mode="after")
    def include_gender_voice_tag(self) -> "Character":
        if not self.voice_tags:
            self.voice_tags = [self.gender]
        elif self.gender not in self.voice_tags:
            self.voice_tags.insert(0, self.gender)
        return self


class PlayerCharacter(BaseModel):
    """Canonical persisted identity and visual reference for the player character.

    The manager owns this schema to canonically manage the image reference and
    identity of the player character across story modules.
    The schema consists of image description, reference, and name.
    """

    name: str = Field(default="", max_length=80)
    reference: str | None = Field(
        default=None,
        description="Canonical image reference alias or path for the player character.",
    )
    image_description: str = Field(
        default="",
        description="Visual appearance description used to generate or match image references.",
    )
    reference_path: str | None = None
    reference_source: Literal["existing", "generated"] | None = None

    # Transitional mapping ergonomics for legacy / dict callers
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

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "image_description" not in d and "description" in d:
                d["image_description"] = str(d.get("description") or "")
            if "reference" not in d and "image_reference" in d:
                d["reference"] = d.get("image_reference")
            return d
        return data

    @property
    def description(self) -> str:
        return self.image_description

    @property
    def image_reference(self) -> str | None:
        return self.reference

    def describe(self) -> str:
        parts = [f"Player Character: '{self.name or 'Unnamed Explorer'}'"]
        if self.image_description:
            parts.append(f"Visual: {self.image_description}")
        return " | ".join(parts)

    def for_model_context(self) -> PlayerCharacter:
        """Expose player identity while keeping image bindings internal."""
        return self.model_copy(update={
            "reference": None, "reference_path": None, "reference_source": None,
        })


class GeneratedCharacterProfile(BaseModel):
    personality: str = ""
    motivation: str = ""
    gender: Optional[str] = None
    voice_tags: list[str] = Field(default_factory=list)


class CharacterLookupResult(BaseModel):
    """Structured result returned by character lookup queries."""

    query: str = ""
    characters: list[Character] = Field(default_factory=list)
    player: PlayerCharacter | None = None

    def for_model_context(self) -> CharacterLookupResult:
        """Return a model-facing result without portrait paths or aliases."""
        return self.model_copy(update={
            "characters": [character.for_model_context() for character in self.characters],
            "player": self.player.for_model_context() if self.player is not None else None,
        })

    @property
    def total_count(self) -> int:
        return len(self.characters) + (1 if self.player is not None else 0)

    def get_character_references(self) -> list[str]:
        """Return unique image references for all matched characters and player."""
        references: list[str] = []
        if self.player is not None:
            if self.player.reference and str(self.player.reference).strip():
                references.append(str(self.player.reference).strip())
            if self.player.reference_path and str(self.player.reference_path).strip():
                references.append(str(self.player.reference_path).strip())

        for character in self.characters:
            if character.image_reference and str(character.image_reference).strip():
                references.append(str(character.image_reference).strip())
            if character.image_reference_path and str(character.image_reference_path).strip():
                references.append(str(character.image_reference_path).strip())
        return references

    def describe(self) -> str:
        """Formatted human-readable description of lookup results."""
        if not self.characters and self.player is None:
            if self.query:
                return f"No characters matching '{self.query}' found in known session characters."
            return "No characters have been encountered or introduced in this story yet."

        heading = (
            f"Characters matching '{self.query}':"
            if self.query
            else f"Characters encountered ({self.total_count} total):"
        )
        lines: list[str] = [heading]
        if self.player is not None:
            vis_info = f" Visual: {self.player.image_description}" if self.player.image_description else ""
            lines.append(f"- [Player Character] {self.player.name or 'Unnamed Explorer'}:{vis_info}")
        for character in self.characters:
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

    def __str__(self) -> str:
        return self.describe()

    def __contains__(self, item: str) -> bool:
        return item in self.describe()


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


class ReferenceManager:
    """Own character generation, lookup, and session-scoped character state."""

    def __init__(
        self,
        theater: Theater,
        text_response_provider: TextResponseProvider,
        notepad: Optional[Notepad] = None,
        story_state: Optional[StoryState] = None,
        image_provider: Optional[ImageProvider] = None,
        speech_provider: Optional[SpeechProvider] = None,
        character_image_style: str = "",
    ) -> None:
        if theater is None:
            raise ValueError("theater is required.")
        self.theater: Theater = theater
        self.text_response_provider = text_response_provider
        self.notepad: Optional[Notepad] = notepad
        self._story_state = story_state if story_state is not None else StoryState()
        self.image_provider: Optional[ImageProvider] = image_provider
        self.speech_provider: Optional[SpeechProvider] = speech_provider
        self.character_image_style = str(character_image_style or "").strip()
        self.max_active_characters = DEFAULT_MAX_ACTIVE_CHARACTERS
        self._characters: dict[str, Character] = {}
        self._characters_lock = Lock()
        self._player_character: Optional[PlayerCharacter] = None
        self._player_character_lock = Lock()
        self._seed_character_images()
        self._sync_story_state()

    @property
    def characters_dir(self) -> Path | None:
        val = self.theater.updated_characters_dir()
        if val is None or not str(val).strip():
            return None
        return Path(val)

    def _seed_character_images(self) -> None:
        """Copy authored portraits without overwriting existing session iterations."""
        source = self.theater.characters_dir()
        destination = self.characters_dir
        if destination is None or not source.is_dir():
            return
        existing = {
            directory.name
            for directory in destination.iterdir()
            if directory.is_dir() and get_latest_iteration_file(directory) is not None
        } if destination.is_dir() else set()
        for image in sorted(source.rglob("*")):
            if not image.is_file() or image.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if not image.resolve().is_relative_to(source.resolve()):
                continue
            relative = image.relative_to(source)
            if len(relative.parts) < 2:
                continue
            name = relative.parts[0]
            target_dir = get_character_reference_dir(destination, name)
            # Once a character has session images, the session owns its identity.
            if target_dir.name in existing:
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image, target_dir / image.name)

    def available_character_images(self) -> dict[str, str]:
        """List named session portraits, including characters outside the active scene."""
        root = self.characters_dir
        if root is None or not root.is_dir():
            return {}
        images: dict[str, str] = {}
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            latest = get_latest_iteration_file(directory)
            if latest is not None:
                character = self._existing_character(directory.name)
                name = character.name if character is not None else directory.name.replace("_", " ")
                images[name] = str(latest)
        player = self.get_player_character()
        if player is not None and player.reference_path and Path(player.reference_path).is_file():
            images[player.name or "Player"] = self.get_latest_reference_path_for_character(player.name or "player") or player.reference_path
        return images

    def get_character_visual_path(self, name: str) -> str | None:
        """Resolve an explicit prompt tag by exact name or alias, never a partial name."""
        key = self._slug_key(name)
        if not key:
            return None
        character = self._existing_character(name)
        if character is not None:
            return self.get_latest_reference_path_for_character(character.name)
        player = self.get_player_character()
        if player is not None and key in {self._slug_key(player.name), "player"}:
            return self.get_latest_reference_path_for_character(player.name or "player")
        for available_name, path in self.available_character_images().items():
            if key == self._slug_key(available_name):
                return path
        return None

    @property
    def references_dir(self) -> Path | None:
        val = self.theater.references_dir()
        if val is None or not str(val).strip():
            return None
        return Path(val)

    def _parse_initial_characters(self, raw: object) -> list[Character]:
        if not raw:
            return []
        try:
            items = raw.items()  # type: ignore[union-attr]
            char_list = [{"name": name, **val} for name, val in items]
        except (AttributeError, TypeError):
            try:
                char_list = list(raw)  # type: ignore[arg-type]
            except TypeError:
                char_list = []

        characters: list[Character] = []
        for item in char_list:
            try:
                try:
                    if not item.get("gender"):
                        clean_tags = normalize_voice_tags(item.get("voice_tags", item.get("voice_type")))
                        item["gender"] = _voice_tag_gender(clean_tags) or "female"
                except (AttributeError, TypeError):
                    pass
                characters.append(Character.model_validate(item))
            except Exception as exc:
                logger.warning("[ReferenceManager] Skipping invalid initial character: %s (%s)", item, exc)
        return characters

    @property
    def story_state(self) -> StoryState:
        return self._story_state

    @story_state.setter
    def story_state(self, value: StoryState) -> None:
        if value is None:
            raise ValueError("story_state cannot be None.")
        self._story_state = value
        self._sync_story_state()

    def _sync_story_state(self) -> None:
        if self._story_state is None:
            return

        pc = self.get_player_character()
        pc_state: Optional[PlayerCharacterState] = None
        if pc is not None:
            pc_state = PlayerCharacterState(
                name=pc.name,
                image_description=pc.image_description,
                reference=pc.reference,
            )

        chars = self.get_present_characters()
        chars_state: dict[str, CharacterState] = {
            c.name: CharacterState(
                name=c.name,
                personality=c.personality,
                motivation=c.motivation,
                quirk=c.quirk,
                image_reference=c.image_reference,
            )
            for c in chars
        }

        refs = self.get_character_references()

        self._story_state.set_player_character(pc_state)
        self._story_state.set_characters(chars_state)
        self._story_state.set_character_references(refs)

    @staticmethod
    def _character_key(name: str) -> str:
        return re.sub(r"\s+", " ", str(name or "").strip()).casefold()

    @staticmethod
    def _slug_key(name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")

    @staticmethod
    def _alias_for_name(name: str) -> str:
        alias = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
        if not alias:
            raise ValueError("Character alias cannot be empty.")
        return alias

    def _existing_character(self, name_or_alias: str) -> Character | None:
        key = self._character_key(name_or_alias)
        slug = self._slug_key(name_or_alias)
        with self._characters_lock:
            character = self._characters.get(str(name_or_alias))
            if character is not None:
                return character
            return next((
                character for alias, character in self._characters.items()
                if self._character_key(alias) == key
                or self._character_key(character.name) == key
                or (slug and (self._slug_key(alias) == slug or self._slug_key(character.name) == slug))
            ), None)

    def _serialized_character(self, name_or_alias: str) -> Character | None:
        key = self._character_key(name_or_alias)
        slug = self._slug_key(name_or_alias)
        story_state = self.story_state

        # Check characters in story planning state
        planning = story_state.get_story_planning_state()
        raw_characters = planning.get("characters", [])

        for item in (raw_characters or ()):
            try:
                character = Character.model_validate(item)
                if (
                    self._character_key(character.name) == key
                    or (character.alias and self._character_key(character.alias) == key)
                    or (slug and (self._slug_key(character.name) == slug or (character.alias and self._slug_key(character.alias) == slug)))
                ):
                    return character
            except Exception:
                continue

        # Check canvas story_state._character
        raw_char = story_state._character(name_or_alias)
        if raw_char is not None:
            try:
                return Character.model_validate(raw_char)
            except Exception:
                pass

        tags_res = story_state.get_character_voice_tags(name_or_alias)
        voice_tags = [str(t) for t in tags_res]
        tag_gender = _voice_tag_gender(voice_tags)

        voice_res = story_state.get_character_voice(name_or_alias)
        voice_id = str(voice_res).strip() if voice_res and str(voice_res).strip() else None

        desc_res = story_state.get_character_description(name_or_alias)
        description = desc_res.strip() if desc_res and desc_res.strip().lower() != key else ""

        if voice_tags or voice_id or description:
            try:
                return Character.model_validate({
                    "name": name_or_alias,
                    "gender": tag_gender or "female",
                    "description": description,
                    "voice_tags": voice_tags,
                    "voice_id": voice_id,
                })
            except Exception:
                pass

        return None

    def _characters_base_dir(self) -> Path | None:
        chars_dir = self.characters_dir
        if chars_dir is not None:
            return chars_dir
        return None

    def _character_reference_dir(self, name: str) -> Path | None:
        base_dir = self._characters_base_dir()
        if base_dir is None:
            return None
        key = self._slug_key(name)
        if base_dir.is_dir():
            for directory in sorted(base_dir.iterdir()):
                if directory.is_dir() and self._slug_key(directory.name) == key:
                    return directory
        return base_dir / get_character_folder_name(name)

    def get_latest_reference_path_for_character(self, query: str) -> str | None:
        """Return the filesystem path to the latest iteration reference for query."""
        clean = str(query or "").strip()
        if not clean:
            return None

        # 1. Check existing character in memory by name/alias
        char = self._existing_character(clean)
        if char is not None:
            char_dir = self._character_reference_dir(char.name)
            if char_dir is not None and char_dir.is_dir():
                latest = get_latest_iteration_file(char_dir)
                if latest is not None and latest.is_file():
                    return str(latest)
            if char.image_reference_path and Path(char.image_reference_path).is_file():
                return char.image_reference_path

        player = self.get_player_character()
        if player is not None:
            p_name = (player.name or "").strip()
            p_ref = (player.reference or "").strip()
            p_ref_path = (player.reference_path or "").strip()
            if (
                clean.casefold() in (p_name.casefold(), p_ref.casefold(), p_ref_path.casefold(), "player")
                or (p_name and self._character_key(p_name) == self._character_key(clean))
            ):
                p_dir = self._character_reference_dir(p_name or "player")
                if p_dir is not None and p_dir.is_dir():
                    latest = get_latest_iteration_file(p_dir)
                    if latest is not None and latest.is_file():
                        return str(latest)
                if player.reference_path and Path(player.reference_path).is_file():
                    return player.reference_path

        # 2. Resolve names and iteration aliases within the session character library.
        base_dir = self._characters_base_dir()
        req_path = Path(clean)
        candidates = [req_path.stem, req_path.name]
        if req_path.parent.name and req_path.parent.name not in (".", ""):
            candidates.append(req_path.parent.name)

        for cand in candidates:
            cand_clean = cand.strip()
            if not cand_clean or cand_clean.lower() in ("references", "images", "artifacts", "output", "characters"):
                continue

            matched_char = self._existing_character(cand_clean)
            if matched_char is not None:
                matched_dir = self._character_reference_dir(matched_char.name)
                if matched_dir is not None and matched_dir.is_dir():
                    latest = get_latest_iteration_file(matched_dir)
                    if latest is not None and latest.is_file():
                        return str(latest)
                if matched_char.image_reference_path and Path(matched_char.image_reference_path).is_file():
                    return matched_char.image_reference_path

            if player is not None:
                p_name = (player.name or "").strip()
                if cand_clean.casefold() in (p_name.casefold(), "player") or (
                    p_name and self._character_key(p_name) == self._character_key(cand_clean)
                ):
                    p_dir = self._character_reference_dir(p_name or "player")
                    if p_dir is not None and p_dir.is_dir():
                        latest = get_latest_iteration_file(p_dir)
                        if latest is not None and latest.is_file():
                            return str(latest)
                    if player.reference_path and Path(player.reference_path).is_file():
                        return player.reference_path

            if base_dir is not None and base_dir.is_dir():
                cand_dir = self._character_reference_dir(cand_clean)
                if cand_dir is not None and cand_dir.is_dir():
                    latest = get_latest_iteration_file(cand_dir)
                    if latest is not None and latest.is_file():
                        return str(latest)

        return None

    def _reference_entry(self, reference: str | None) -> dict[str, str] | None:
        """Resolve a reference by an exact stable identifier or canonical slug.

        Fuzzy matching is intentionally prohibited here: choosing a wrong
        portrait is worse than creating a new one for character cohesion.
        Matches:
        1. Character iteration folder latest iteration.
        2. Exact path, filename, name, alias, or stem.
        3. Normalized slug matching (e.g. 'Lady Lux' <-> 'lady_lux.jpg').
        4. Fallback match to an already-generated character portrait ({slug}_character).
        """
        if not reference:
            return None
        requested = str(reference).strip()
        if not requested:
            return None

        # Resolve the latest session iteration first.
        char_dir = self._character_reference_dir(requested)
        if char_dir is not None and char_dir.is_dir():
            latest = get_latest_iteration_file(char_dir)
            if latest is not None and latest.is_file():
                alias = f"output/artifacts/updated_references/characters/{char_dir.name}/{latest.name}"
                return {
                    "name": f"{char_dir.name}_{latest.stem}",
                    "alias": alias,
                    "path": str(latest),
                    "title": f"Reference: {requested}",
                    "description": f"Character reference for {requested}.",
                }

        # Check if requested is an exact file path that exists
        req_p = Path(requested)
        if req_p.is_file():
            if req_p.parent.resolve() == self.theater.characters_dir().resolve():
                return None
            alias = req_p.stem
            return {
                "name": req_p.stem,
                "alias": alias,
                "path": str(req_p),
                "title": req_p.stem,
                "description": f"Reference image {req_p.name}",
            }

        ref_dir = self.references_dir
        if ref_dir is None or not os.path.isdir(ref_dir):
            return None

        entries: list[dict[str, str]] = []
        for root, _, files in os.walk(ref_dir):
            for filename in files:
                p_file = Path(filename)
                if p_file.suffix.lower() in IMAGE_EXTENSIONS:
                    full_p = os.path.join(root, filename)
                    p_obj = Path(full_p)
                    entries.append({
                        "name": p_obj.stem,
                        "alias": p_obj.stem,
                        "path": full_p,
                        "title": p_obj.stem,
                        "description": f"Reference image {filename}",
                    })

        normalized = self._character_key(requested)
        req_path_obj = Path(requested)
        filename = self._character_key(req_path_obj.name)
        stem = self._character_key(req_path_obj.stem)
        norm_req_path = os.path.normcase(os.path.normpath(requested))

        # Pass 1: Exact matches (path, filename, stem, name, alias, character_key)
        for entry in entries:
            entry_path = entry.get("path", "")
            entry_name = entry.get("name", "")
            entry_alias = entry.get("alias", "")
            entry_filename = Path(entry_path).name
            entry_stem = Path(entry_path).stem

            if entry_path and os.path.normcase(os.path.normpath(entry_path)) == norm_req_path:
                return entry

            values = (entry_path, entry_name, entry_alias)
            if any(self._character_key(value) == normalized for value in values):
                return entry

            if self._character_key(entry_filename) == filename:
                return entry

            if entry_stem and (
                self._character_key(entry_stem) == stem
                or self._character_key(entry_stem) == normalized
            ):
                return entry

        # Pass 2: Normalized slug identifier match (e.g., 'Lady Lux' <-> 'lady_lux.jpg')
        req_slug = self._slug_key(req_path_obj.stem) or self._slug_key(requested)
        if req_slug:
            for entry in entries:
                entry_path = entry.get("path", "")
                entry_stem = Path(entry_path).stem if entry_path else ""
                entry_slugs = {
                    self._slug_key(entry_stem),
                    self._slug_key(entry.get("alias", "")),
                    self._slug_key(entry.get("name", "")),
                }
                entry_slugs.discard("")
                if req_slug in entry_slugs:
                    return entry

            # Pass 3: Fallback match to an already-generated character portrait
            generated_char_slug = f"{req_slug}_character"
            for entry in entries:
                entry_path = entry.get("path", "")
                entry_stem = Path(entry_path).stem if entry_path else ""
                entry_slugs = {
                    self._slug_key(entry_stem),
                    self._slug_key(entry.get("alias", "")),
                    self._slug_key(entry.get("name", "")),
                }
                entry_slugs.discard("")
                if generated_char_slug in entry_slugs:
                    return entry

        return None

    def _generate_character_reference(
        self,
        character: Character,
        previous_reference_path: str | None = None,
    ) -> dict[str, str] | None:
        if self.image_provider is None:
            return None

        char_dir = self._character_reference_dir(character.name)
        if char_dir is None:
            return None

        prev_file: Path | None = None
        if previous_reference_path and Path(previous_reference_path).is_file():
            prev_file = Path(previous_reference_path)
        else:
            prev_file = get_latest_iteration_file(char_dir)

        request_references: list[ImageReference] = []
        if prev_file is not None and prev_file.is_file():
            try:
                prev_data = prev_file.read_bytes()
                prev_suffix = prev_file.suffix.lower()
                prev_mime = "image/png" if prev_suffix == ".png" else "image/webp" if prev_suffix == ".webp" else "image/jpeg"
                request_references.append(
                    ImageReference(
                        name=prev_file.name,
                        data=prev_data,
                        mime_type=prev_mime,
                        label=character.name,
                    )
                )
            except OSError as exc:
                logger.warning("[ReferenceManager] Could not read previous reference %s: %s", prev_file, exc)

        prompt = (
            f"Single-character reference portrait of {character.name}. "
            f"Appearance: {character.description or character.personality or 'distinctive adventure character'}. "
            "Square head-and-shoulders portrait, clear face and silhouette, neutral background, "
            "consistent costume details, no text, no collage."
        )
        if self.character_image_style:
            prompt += f" Style: {self.character_image_style}."

        try:
            result = self.image_provider.generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    references=request_references,
                    aspect_ratio="1:1",
                )
            )
            image = Image.open(BytesIO(result.image_bytes)).convert("RGB")
            char_dir.mkdir(parents=True, exist_ok=True)
            next_num = get_next_iteration_number(char_dir)
            ext = ".webp" if result.mime_type == "image/webp" else ".jpg" if result.mime_type in ("image/jpeg", "image/jpg") else ".png"
            output = char_dir / f"{next_num}{ext}"
            exif = image.getexif()
            embed_image_metadata(exif, f"Character reference for {character.name} (iteration {next_num}). {prompt}")
            image.save(output, "PNG" if ext == ".png" else "WEBP" if ext == ".webp" else "JPEG", exif=exif)
            alias = f"output/artifacts/updated_references/characters/{char_dir.name}/{output.name}"
            return {
                "name": f"{char_dir.name}_{next_num}",
                "alias": alias,
                "path": str(output),
                "title": f"Reference: {character.name} (iteration {next_num})",
                "description": f"Character reference portrait for {character.name} (iteration {next_num}).",
            }
        except (ImageProviderError, OSError, ValueError) as exc:
            logger.warning("[ReferenceManager] Could not create portrait for %s: %s", character.name, exc)
        except Exception:
            logger.exception("[ReferenceManager] Unexpected portrait failure for %s", character.name)
        return None

    def _bind_character_image(self, character: Character) -> None:
        if character.image_reference_path and Path(character.image_reference_path).is_file():
            entry = self._session_reference_entry(character.name, Path(character.image_reference_path))
            character.image_reference = entry["alias"]
            character.image_reference_path = entry["path"]
            return
        requested = character.image_reference
        # A character name is a safe fallback only when an image's name/alias
        # matches exactly or by canonical slug; this never guesses from a semantic description.
        entry = self._reference_entry(requested) or self._reference_entry(character.name)
        source = "existing" if entry else "generated"
        if entry is None:
            entry = self._generate_character_reference(character)
        if entry is None:
            return
        entry = self._session_reference_entry(character.name, Path(entry["path"]))
        character.image_reference = entry.get("alias", "")
        character.image_reference_path = entry.get("path", "")
        character.image_reference_source = source

    def _session_reference_entry(self, name: str, image: Path) -> dict[str, str]:
        """Bind imported references to a writable session copy."""
        if image.parent.resolve() == self.theater.characters_dir().resolve():
            raise ValueError("Character portraits must be inside references/characters/<Character Name>/.")
        directory = self._character_reference_dir(name)
        if directory is None:
            raise ValueError("Session character directory is required.")
        root = self.characters_dir
        if root is not None and image.resolve().is_relative_to(root.resolve()):
            directory = image.parent
        if image.parent.resolve() != directory.resolve():
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{get_next_iteration_number(directory)}{image.suffix.lower()}"
            shutil.copy2(image, destination)
            image = destination
        return {
            "alias": f"output/artifacts/updated_references/characters/{directory.name}/{image.name}",
            "path": str(image),
        }

    def _generate_player_reference(
        self,
        player: PlayerCharacter,
        previous_reference_path: str | None = None,
    ) -> dict[str, str] | None:
        if self.image_provider is None:
            return None
        name_label = player.name or "the protagonist"
        char_dir = self._character_reference_dir(player.name or "player")
        if char_dir is None:
            return None

        prev_file: Path | None = None
        if previous_reference_path and Path(previous_reference_path).is_file():
            prev_file = Path(previous_reference_path)
        else:
            prev_file = get_latest_iteration_file(char_dir)

        root = self.characters_dir
        if prev_file is not None and root is not None and prev_file.resolve().is_relative_to(root.resolve()):
            char_dir = prev_file.parent

        request_references: list[ImageReference] = []
        if prev_file is not None and prev_file.is_file():
            try:
                prev_data = prev_file.read_bytes()
                prev_suffix = prev_file.suffix.lower()
                prev_mime = "image/png" if prev_suffix == ".png" else "image/webp" if prev_suffix == ".webp" else "image/jpeg"
                request_references.append(
                    ImageReference(
                        name=prev_file.name,
                        data=prev_data,
                        mime_type=prev_mime,
                        label=player.name or "Player",
                    )
                )
            except OSError as exc:
                logger.warning("[ReferenceManager] Could not read previous reference %s: %s", prev_file, exc)

        prompt = (
            f"Single-character reference portrait of player character {name_label}. "
            f"Appearance: {player.image_description or 'distinctive adventure protagonist'}. "
            "Square head-and-shoulders portrait, clear face and silhouette, neutral background, "
            "consistent costume details, no text, no collage."
        )
        if self.character_image_style:
            prompt += f" Style: {self.character_image_style}."
        try:
            result = self.image_provider.generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    references=request_references,
                    aspect_ratio="1:1",
                )
            )
            image = Image.open(BytesIO(result.image_bytes)).convert("RGB")
            char_dir.mkdir(parents=True, exist_ok=True)
            next_num = get_next_iteration_number(char_dir)
            ext = ".webp" if result.mime_type == "image/webp" else ".jpg" if result.mime_type in ("image/jpeg", "image/jpg") else ".png"
            output = char_dir / f"{next_num}{ext}"
            exif = image.getexif()
            embed_image_metadata(exif, f"Player character reference for {name_label} (iteration {next_num}). {prompt}")
            image.save(output, "PNG" if ext == ".png" else "WEBP" if ext == ".webp" else "JPEG", exif=exif)
            alias = f"output/artifacts/updated_references/characters/{char_dir.name}/{output.name}"
            return {
                "name": f"{char_dir.name}_{next_num}",
                "alias": alias,
                "path": str(output),
                "title": f"Reference: {name_label} (iteration {next_num})",
                "description": f"Player character reference portrait for {name_label} (iteration {next_num}).",
            }
        except (ImageProviderError, OSError, ValueError) as exc:
            logger.warning("[ReferenceManager] Could not create portrait for player %s: %s", name_label, exc)
        except Exception:
            logger.exception("[ReferenceManager] Unexpected player portrait failure for %s", name_label)
        return None

    def _bind_player_image(self, player: PlayerCharacter) -> None:
        if player.reference_path and Path(player.reference_path).is_file():
            entry = self._session_reference_entry(player.name or "player", Path(player.reference_path))
            player.reference = entry["alias"]
            player.reference_path = entry["path"]
            return
        requested = player.reference
        entry = self._reference_entry(requested) or (self._reference_entry(player.name) if player.name else None)
        source = "existing" if entry else "generated"
        if entry is None and (player.image_description or player.name):
            entry = self._generate_player_reference(player)
        if entry is None:
            return
        entry = self._session_reference_entry(player.name or "player", Path(entry["path"]))
        player.reference = entry.get("alias") or entry.get("name") or str(entry.get("path", ""))
        player.reference_path = entry.get("path", "")
        player.reference_source = source


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
            logger.warning("[ReferenceManager] Could not select voice for %s: %s", character.name, exc)

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

    def lookup_character(self, query: str = "", name_only: bool = False) -> CharacterLookupResult:
        """List all session characters or search by name or trait.

        Args:
            query: Search query or prompt to match against session characters.
            name_only: If True, matches characters solely by their canonical name.

        Returns:
            CharacterLookupResult containing matched characters and player.
        """
        with self._characters_lock:
            characters = list(self._characters.values())
        player = self.get_player_character()

        if not characters and player is None:
            return CharacterLookupResult(query=query, characters=[], player=None)

        clean_query = str(query or "").strip().lower()
        matches: list[Character] = characters
        player_matches = False
        if clean_query:
            if name_only:
                matches = []
                for character in characters:
                    char_name = character.name.strip().lower()
                    char_alias = character.alias.strip().lower()
                    if not char_name and not char_alias:
                        continue
                    if char_alias and clean_query in (char_alias, char_alias.replace("_", " ")):
                        matches.append(character)
                        continue
                    query_words = clean_query.replace("_", " ")
                    name_words = char_name.replace("_", " ")
                    if len(char_name) == 1:
                        if clean_query == char_name or query_words == name_words:
                            matches.append(character)
                    else:
                        if (
                            re.search(r"\b" + re.escape(char_name) + r"\b", clean_query)
                            or (len(clean_query) >= 2 and re.search(r"\b" + re.escape(clean_query) + r"\b", char_name))
                            or re.search(r"\b" + re.escape(name_words) + r"\b", query_words)
                            or (len(query_words) >= 2 and re.search(r"\b" + re.escape(query_words) + r"\b", name_words))
                        ):
                            matches.append(character)
                if player is not None:
                    player_name = (player.name or "").strip().lower()
                    player_words = player_name.replace("_", " ")
                    query_words = clean_query.replace("_", " ")
                    if len(player_name) == 1:
                        player_matches = (clean_query == player_name or query_words == player_words)
                    elif len(player_name) >= 2:
                        player_matches = bool(
                            re.search(r"\b" + re.escape(player_name) + r"\b", clean_query)
                            or (len(clean_query) >= 2 and re.search(r"\b" + re.escape(clean_query) + r"\b", player_name))
                            or re.search(r"\b" + re.escape(player_words) + r"\b", query_words)
                            or (len(query_words) >= 2 and re.search(r"\b" + re.escape(query_words) + r"\b", player_words))
                        )
            else:
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
                if player is not None:
                    player_searchable = " ".join(
                        [player.name, player.image_description, player.reference or ""]
                    ).lower()
                    if any(term in player_searchable for term in terms) or "player" in clean_query:
                        player_matches = True
        else:
            player_matches = player is not None

        return CharacterLookupResult(
            query=query,
            characters=matches,
            player=player if player_matches else None,
        )

    def get_player_character(self) -> PlayerCharacter | None:
        """Return the canonical persisted identity and visual reference for the player character."""
        with self._player_character_lock:
            return self._player_character.model_copy(deep=True) if self._player_character else None

    def get_player_reference(self) -> str | None:
        """Return the canonical image reference identifier for the player character."""
        with self._player_character_lock:
            return self._player_character.reference if self._player_character else None

    def get_character_references(self) -> list[str]:
        """Return unique image references for active characters and player."""
        references: list[str] = []
        player = self.get_player_character()
        if player is not None:
            if player.reference and str(player.reference).strip():
                references.append(str(player.reference).strip())
            if player.reference_path and str(player.reference_path).strip():
                references.append(str(player.reference_path).strip())

        for character in self.get_present_characters():
            if character.image_reference and str(character.image_reference).strip():
                references.append(str(character.image_reference).strip())
            if character.image_reference_path and str(character.image_reference_path).strip():
                references.append(str(character.image_reference_path).strip())

        seen: set[str] = set()
        deduped: list[str] = []
        for r in references:
            key = r.casefold()
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        return deduped

    def set_player_character(
        self, player: PlayerCharacter | None
    ) -> PlayerCharacter | None:
        """Canonically set or replace the player character record."""
        with self._player_character_lock:
            if player is None:
                self._player_character = None
                self._sync_story_state()
                return None
            try:
                p = PlayerCharacter.model_validate(player).model_copy(deep=True)
            except Exception as exc:
                raise ValueError(f"player must be a PlayerCharacter or valid player data: {exc}") from exc
            self._bind_player_image(p)
            self._player_character = p
            result = p.model_copy(deep=True)
        self._sync_story_state()
        return result

    def update_player_character(
        self,
        name: str = "",
        reference: str | None = None,
        image_description: str = "",
        **kwargs: str | None,
    ) -> PlayerCharacter:
        """Update or establish canonical player character identity and visual reference."""
        if not image_description and "description" in kwargs:
            raw_desc = kwargs.get("description")
            image_description = str(raw_desc).strip() if raw_desc is not None else ""
        if reference is None and "image_reference" in kwargs:
            raw_ref = kwargs.get("image_reference")
            reference = str(raw_ref).strip() if raw_ref is not None else None

        with self._player_character_lock:
            existing = self._player_character
            new_name = str(name).strip() if name else (existing.name if existing else "")
            clean_desc = str(image_description).strip()[:500] if image_description else ""
            description_changed = bool(
                clean_desc and (existing is None or clean_desc != (existing.image_description or "").strip())
            )
            new_desc = clean_desc if clean_desc else (existing.image_description if existing else "")

            explicit_image_ref = bool(reference is not None and str(reference).strip())
            if explicit_image_ref:
                new_ref = str(reference).strip()
                if existing and existing.reference == new_ref and existing.reference_path:
                    ref_path = existing.reference_path
                    ref_source = existing.reference_source
                else:
                    ref_path = None
                    ref_source = None
            elif existing:
                if description_changed:
                    prev_path = existing.reference_path
                    draft = PlayerCharacter(
                        name=new_name,
                        reference=None,
                        image_description=new_desc,
                        reference_path=None,
                        reference_source=None,
                    )
                    entry = self._generate_player_reference(draft, previous_reference_path=prev_path)
                    if entry is not None:
                        new_ref = entry.get("alias", "")
                        ref_path = entry.get("path", "")
                        ref_source = "generated"
                    else:
                        new_ref = existing.reference
                        ref_path = existing.reference_path
                        ref_source = existing.reference_source
                else:
                    new_ref = existing.reference
                    ref_path = existing.reference_path
                    ref_source = existing.reference_source
            else:
                new_ref = None
                ref_path = None
                ref_source = None

            player = PlayerCharacter(
                name=new_name,
                reference=new_ref,
                image_description=new_desc,
                reference_path=ref_path,
                reference_source=ref_source,
            )
            self._bind_player_image(player)
            self._player_character = player
            result = player.model_copy(deep=True)
        self._sync_story_state()
        return result

    def export_player_character(self) -> PlayerCharacter | None:
        """Export canonical player character state for persistence."""
        with self._player_character_lock:
            if not self._player_character:
                return None
            return self._player_character.model_copy(deep=True)

    def import_player_character(
        self, data: PlayerCharacter | None
    ) -> PlayerCharacter | None:
        """Restore or set canonical player character state from config or persistence."""
        if not data:
            return None
        return self.set_player_character(data)

    def create_or_update_character(
        self,
        name: str,
        description: str = "",
        personality: str = "",
        motivation: str = "",
        quirk: str = "",
        voice_tags: Any = None,
        gender: Optional[str] = None,
        image_reference: str = "",
        **kwargs: Any,
    ) -> Character | None:
        """Generate or update an NPC profile with explicit gender.

        Performs an upsert:
        - If the character already exists, updates all specified fields.
        - If the character is to be created per the story module's views, prioritizes traits
          assigned to that character per the serialized story state for consistency across
          initializations, generating any remaining missing traits.
        """
        clean_name = str(name or "").strip()[:80]
        if not clean_name:
            return None

        supported_tags = self._supported_voice_tags()

        # 1. If character already exists, update all fields specified (upsert).
        existing = self._existing_character(clean_name)
        if existing:
            clean_desc = str(description).strip()[:500] if description else ""
            description_changed = bool(clean_desc and clean_desc != (existing.description or ""))
            if description:
                existing.description = clean_desc
            if personality:
                existing.personality = str(personality).strip()[:300]
            if motivation:
                existing.motivation = str(motivation).strip()[:300]
            if quirk:
                existing.quirk = str(quirk).strip()[:300]

            explicit_image_ref = bool(image_reference and str(image_reference).strip())
            if explicit_image_ref:
                existing.image_reference = str(image_reference).strip()
                existing.image_reference_path = None
                existing.image_reference_source = None
            elif description_changed:
                prev_path = existing.image_reference_path
                entry = self._generate_character_reference(existing, previous_reference_path=prev_path)
                if entry is not None:
                    existing.image_reference = entry.get("alias", "")
                    existing.image_reference_path = entry.get("path", "")
                    existing.image_reference_source = "generated"

            clean_gender = ""
            if gender:
                norm_g = normalize_voice_tags(gender)
                if norm_g:
                    clean_gender = norm_g[0]

            if voice_tags is not None and (
                (type(voice_tags) in (list, tuple, set) and len(voice_tags) > 0)
                or (type(voice_tags) is str and voice_tags.strip())
            ):
                clean_tags = normalize_voice_tags(voice_tags, supported_tags)
                tag_gender = _voice_tag_gender(clean_tags)
                if not clean_gender and tag_gender:
                    clean_gender = tag_gender
                elif clean_gender and not tag_gender:
                    clean_tags.insert(0, clean_gender)
                existing.voice_tags = clean_tags
                if clean_gender:
                    existing.gender = clean_gender
            elif clean_gender:
                existing.gender = clean_gender
                updated_tags = [
                    t for t in existing.voice_tags
                    if t not in SUPPORTED_VOICE_TAGS and not t.startswith("gender=")
                ]
                updated_tags.insert(0, clean_gender)
                existing.voice_tags = updated_tags

            self._ensure_character_bindings(existing)
            with self._characters_lock:
                self._characters[existing.alias] = existing
            self._sync_story_state()
            return existing

        # 2. Character does not exist yet in memory.
        # Check if traits were assigned per the serialized story state.
        serialized = self._serialized_character(clean_name)

        if serialized is not None:
            clean_name = serialized.name or clean_name
            alias = serialized.alias or self._alias_for_name(clean_name)

            # Prioritize traits from serialized story state, falling back to caller's specified views
            clean_gender = serialized.gender
            if not clean_gender and gender:
                norm_g = normalize_voice_tags(gender)
                if norm_g:
                    clean_gender = norm_g[0]

            clean_tags = normalize_voice_tags(serialized.voice_tags or voice_tags, supported_tags)
            tag_gender = _voice_tag_gender(clean_tags)
            if not clean_gender and tag_gender:
                clean_gender = tag_gender
            elif clean_gender and not tag_gender:
                clean_tags.insert(0, clean_gender)

            clean_personality = (serialized.personality or personality or "")[:300]
            clean_motivation = (serialized.motivation or motivation or "")[:300]
            clean_quirk = (serialized.quirk or quirk or "")[:300]
            clean_description = (serialized.description or description or "")[:500]

            voice_id = serialized.voice_id
            if image_reference and str(image_reference).strip():
                clean_image_ref = str(image_reference).strip()
                image_ref_path = None
                image_ref_source = None
            else:
                clean_image_ref = (serialized.image_reference or "").strip()
                image_ref_path = serialized.image_reference_path if serialized.image_reference else None
                image_ref_source = serialized.image_reference_source if serialized.image_reference else None
        else:
            alias = self._alias_for_name(clean_name)
            clean_description = str(description or "").strip()[:500]
            clean_personality = str(personality or "").strip()[:300]
            clean_motivation = str(motivation or "").strip()[:300]
            clean_quirk = str(quirk or "").strip()[:300]
            clean_gender = ""
            if gender:
                norm_g = normalize_voice_tags(gender)
                if norm_g:
                    clean_gender = norm_g[0]

            clean_tags = normalize_voice_tags(voice_tags, supported_tags)
            tag_gender = _voice_tag_gender(clean_tags)
            if not clean_gender and tag_gender:
                clean_gender = tag_gender
            elif clean_gender and not tag_gender:
                clean_tags.insert(0, clean_gender)

            voice_id = None
            clean_image_ref = str(image_reference or "").strip()
            image_ref_path = None
            image_ref_source = None

        # 3. If any traits are still missing, generate them via text provider.
        if not clean_personality or not clean_motivation or not clean_tags or not clean_gender:
            elements = self.notepad.get_present_elements() if self.notepad is not None else []
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
                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)
                generated = GeneratedCharacterProfile.model_validate_json(raw_text)
                if not clean_personality and generated.personality:
                    clean_personality = generated.personality.strip()[:300]
                if not clean_motivation and generated.motivation:
                    clean_motivation = generated.motivation.strip()[:300]
                if not clean_gender and generated.gender:
                    gen_g = normalize_voice_tags(generated.gender)
                    if gen_g:
                        clean_gender = gen_g[0]
                if not clean_tags and generated.voice_tags:
                    clean_tags = normalize_voice_tags(generated.voice_tags, supported_tags)
            except Exception as exc:
                logger.warning("[ReferenceManager] Character profile generation failed: %s", exc)

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

        character = Character(
            name=clean_name,
            alias=alias,
            gender=clean_gender,
            description=clean_description,
            personality=clean_personality,
            motivation=clean_motivation,
            quirk=clean_quirk,
            voice_tags=clean_tags,
            voice_id=voice_id,
            image_reference=clean_image_ref or None,
            image_reference_path=image_ref_path,
            image_reference_source=image_ref_source,
        )
        self._ensure_character_bindings(character)
        with self._characters_lock:
            self._characters[character.alias] = character
        self._sync_story_state()

        if image_reference and not character.image_reference_path and character.image_reference != str(image_reference).strip():
            character.image_reference = str(image_reference).strip()
            character.image_reference_path = None
            character.image_reference_source = None
            self._ensure_character_bindings(character)
            with self._characters_lock:
                self._characters[character.alias] = character
            self._sync_story_state()

        return character

    def _supported_voice_tags(self) -> Mapping[str, tuple[str, ...]]:
        if self.speech_provider is None:
            return {"gender": ("female", "male", "nonbinary")}
        try:
            return self.speech_provider.get_supported_voice_tags()
        except Exception as exc:
            logger.warning("[ReferenceManager] Unable to list speech voice tags: %s", exc)
            return {"gender": ("female", "male", "nonbinary")}

    @staticmethod
    def _format_voice_tag_options(tags: Mapping[str, tuple[str, ...]]) -> str:
        options = [f"- {field}: {', '.join(values)}" for field, values in sorted(tags.items()) if values]
        return "\n".join(options) or "- gender: female, male, nonbinary"

    def apply_character_updates(
        self, updates: Sequence[Character] | None
    ) -> list[Character]:
        if not updates:
            return []
        manifested: list[Character] = []
        for raw_update in updates[:2]:
            try:
                char_update = Character.model_validate(raw_update)
            except Exception as exc:
                logger.warning("[ReferenceManager] Invalid character update ignored: %s (%s)", raw_update, exc)
                continue

            character = self.create_or_update_character(
                name=char_update.name,
                description=char_update.description,
                personality=char_update.personality,
                motivation=char_update.motivation,
                quirk=char_update.quirk,
                voice_tags=char_update.voice_tags,
                gender=char_update.gender,
                image_reference=char_update.image_reference or "",
            )
            if character is not None:
                manifested.append(character)
        return manifested

    def clear_scene(self) -> int:
        """Clear active characters and return the number removed."""
        with self._characters_lock:
            count = len(self._characters)
            self._characters.clear()
        self._sync_story_state()
        return count

    def export_characters(self) -> list[Character]:
        with self._characters_lock:
            return [character.model_copy(deep=True) for character in self._characters.values()]

    def import_characters(
        self,
        characters: Sequence[Character] | None,
    ) -> None:
        """Replace character state from strongly typed character models."""
        if not characters:
            with self._characters_lock:
                self._characters.clear()
            self._sync_story_state()
            return

        imported: dict[str, Character] = {}
        for character in characters:
            try:
                character_model = Character.model_validate(character).model_copy(deep=True)
            except Exception as exc:
                logger.warning("[ReferenceManager] Skipping invalid character record: %s (%s)", character, exc)
                continue

            if not character_model.alias:
                character_model.alias = self._alias_for_name(character_model.name)
            self._ensure_character_bindings(character_model)
            imported[character_model.alias] = character_model

        with self._characters_lock:
            self._characters = imported
        self._sync_story_state()

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
    "PlayerCharacter",
    "ReferenceManager",
    "DEFAULT_MAX_ACTIVE_CHARACTERS",
    "MAX_ACTIVE_CHARACTERS",
    "SUPPORTED_VOICE_TAGS",
    "normalize_voice_tags",
]
