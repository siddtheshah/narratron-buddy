"""Persisted story-planning, spoken-scene, and dialogue state."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, JsonValue, TypeAdapter

from components.story.input_gathering_cycle import StoryInputGatheringCycle
from components.story.output_display_cycle import OutputCheckpoint, StoryOutput, StoryOutputDisplayCycle

from providers import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
)

logger = logging.getLogger("components.canvas_state")

MAX_PARALLEL_SCENE_SPEECH = 4
MAX_SCENE_NARRATION_CHARS = 2_000


@dataclass
class PlayerCharacterState:
    name: str = ""
    image_description: str = ""
    reference: Optional[str] = None


@dataclass
class CharacterState:
    name: str = ""
    personality: str = ""
    motivation: str = ""
    quirk: str = ""
    image_reference: Optional[str] = None

    def __getitem__(self, key: str) -> str | None:
        if key == "name":
            return self.name
        if key == "personality":
            return self.personality
        if key == "motivation":
            return self.motivation
        if key == "quirk":
            return self.quirk
        if key == "image_reference":
            return self.image_reference
        raise KeyError(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        if key == "name":
            return self.name
        if key == "personality":
            return self.personality
        if key == "motivation":
            return self.motivation
        if key == "quirk":
            return self.quirk
        if key == "image_reference":
            return self.image_reference
        return default





def speaker_key(speaker: str) -> str:
    return re.sub(r"\s+", " ", str(speaker or "Narrator").strip()).casefold()[:80] or "narrator"


def normalize_character_models(state: dict[str, Any]) -> dict[str, Any]:
    """Serialize Pydantic character records at the canvas persistence edge."""
    normalized = dict(state)
    characters = normalized.get("characters")
    if not isinstance(characters, list):
        return normalized
    normalized["characters"] = [
        character.model_dump(exclude_none=True) if isinstance(character, BaseModel) else dict(character)
        for character in characters
        if isinstance(character, (BaseModel, dict))
    ]
    return normalized


class StoryState:
    def __init__(
        self,
        persist: Callable[[], None] | None = None,
        notify_changed: Callable[..., None] | None = None,
        publish_audio_fn: Callable[[dict[str, Any]], None] | None = None,
        *,
        adventure_mode: bool = False,
        cycle_length: float = 15.0,
        output_cycle_duration: float = 15.0,
    ) -> None:
        self.cycle = StoryInputGatheringCycle(cycle_length)
        self.output_cycle = StoryOutputDisplayCycle(self._present_scene, self._output_changed, output_cycle_duration)
        self.adventure_mode: bool = adventure_mode
        self._persist = persist
        self._notify_changed = notify_changed
        self.publish_audio_fn = publish_audio_fn
        self.story_planning_state: dict[str, Any] = {}
        self.scene_dialogue: list[dict[str, Any]] = []
        self.narration = ""
        self.narration_spans: list[dict[str, Any]] = []
        self.character_voice_assignments: dict[str, str] = {}
        self.character_voice_tags: dict[str, list[str]] = {}
        self._text_beautifier: Any = None
        self._speech_provider: SpeechProvider | None = None
        self._scene_speech_enabled = False
        self._accent_augmentation = True
        self._speech_executor: ThreadPoolExecutor | None = None
        self._speech_lock = threading.Lock()
        # Incremented immediately to invalidate in-flight synthesis work.
        # It is never sent to clients until the corresponding scene commits.
        self._active_speech_generation = 0
        # The active generation whose narration/dialogue is in the latest
        # canvas payload. Clients may play audio only for this generation.
        self.committed_scene_speech_generation = 0
        self.last_die_roll: dict[str, Any] | None = None
        self.player_character: Optional[PlayerCharacterState] = None
        self.characters: dict[str, CharacterState] = {}
        self.character_references: list[str] = []

    @property
    def input_cycle(self) -> StoryInputGatheringCycle:
        """Explicit name for the input cycle; ``cycle`` remains a compatibility alias."""
        return self.cycle

    def _output_changed(self) -> None:
        if self._persist:
            self._persist()
        if self._notify_changed:
            self._notify_changed("latest")

    @property
    def text_beautifier(self) -> Any:
        if self._text_beautifier is not None:
            return self._text_beautifier
        try:
            import object_registry
            return getattr(object_registry, "text_beautifier", None)
        except Exception:
            return None
    @text_beautifier.setter
    def text_beautifier(self, value: Any) -> None:
        self._text_beautifier = value
    def load(
        self,
        data: dict[str, object] | None = None,
        *,
        persist: Callable[[], None] | None = None,
        notify_changed: Callable[..., None] | None = None,
        text_beautifier: Any = None,
        character_lookup: Callable[[str], Any] | None = None,
        provider: SpeechProvider | None = None,
        **kwargs: Any,
    ) -> None:
        if persist is not None:
            self._persist = persist
        if notify_changed is not None:
            self._notify_changed = notify_changed
        if text_beautifier is not None:
            self._text_beautifier = text_beautifier
        if provider is not None:
            self.enable_scene_speech(provider)

        if data is None or not isinstance(data, dict):
            return

        planning = data.get("story_planning_state")
        if isinstance(planning, dict):
            self.story_planning_state = normalize_character_models(planning)

        dialogue = data.get("scene_dialogue")
        if isinstance(dialogue, list):
            self.scene_dialogue = [dict(x) for x in dialogue if isinstance(x, dict)]

        if "narration" in data:
            self.narration = str(data.get("narration") or "")

        spans = data.get("narration_spans")
        if isinstance(spans, list):
            self.narration_spans = [dict(x) for x in spans if isinstance(x, dict)]

        assignments = data.get("character_voice_assignments")
        if isinstance(assignments, dict):
            self.character_voice_assignments = {str(k): str(v) for k, v in assignments.items()}

        tags = data.get("character_voice_tags")
        if isinstance(tags, dict):
            self.character_voice_tags = {
                speaker_key(k): [str(t) for t in v] if isinstance(v, (list, tuple)) else [str(v)]
                for k, v in tags.items()
            }

        # Accept the short-lived original field while restoring older local
        # theater state, but persist the explicit name below.
        generation = data.get("committed_scene_speech_generation", data.get("scene_speech_generation"))
        if isinstance(generation, int) and generation >= 0:
            self.committed_scene_speech_generation = generation
            with self._speech_lock:
                self._active_speech_generation = max(self._active_speech_generation, generation)

        if "player_character" in data and isinstance(data["player_character"], dict):
            pc_data = data["player_character"]
            self.player_character = PlayerCharacterState(
                name=str(pc_data.get("name") or ""),
                image_description=str(pc_data.get("image_description") or ""),
                reference=pc_data.get("reference"),
            )
        if "characters" in data and isinstance(data["characters"], dict):
            chars_data = data["characters"]
            self.characters = {
                name: CharacterState(
                    name=str(c.get("name") or name),
                    personality=str(c.get("personality") or ""),
                    motivation=str(c.get("motivation") or ""),
                    quirk=str(c.get("quirk") or ""),
                    image_reference=c.get("image_reference"),
                )
                for name, c in chars_data.items()
                if isinstance(c, dict)
            }
        if "character_references" in data and isinstance(data["character_references"], list):
            self.character_references = [str(r) for r in data["character_references"]]

        output_state = data.get("story_output_state")
        if output_state is not None:
            self.output_cycle.restore(OutputCheckpoint.model_validate(output_state))

    def serialize(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "story_cycle": self.cycle.payload(),
            "story_input_cycle": self.input_cycle.payload(),
            "story_output_cycle": self.output_cycle.payload(),
            "story_output_state": self.output_cycle.snapshot(),
            "story_planning_state": self.story_planning_state,
            "scene_dialogue": self.scene_dialogue,
            "narration": self.narration,
            "narration_spans": self.narration_spans,
            "character_voice_assignments": self.character_voice_assignments,
            "character_voice_tags": self.character_voice_tags,
            "committed_scene_speech_generation": self.committed_scene_speech_generation,
        }
        if self.player_character is not None:
            result["player_character"] = {
                "name": self.player_character.name,
                "image_description": self.player_character.image_description,
                "reference": self.player_character.reference,
            }
        if self.characters:
            result["characters"] = {
                name: {
                    "name": c.name,
                    "personality": c.personality,
                    "motivation": c.motivation,
                    "quirk": c.quirk,
                    "image_reference": c.image_reference,
                }
                for name, c in self.characters.items()
            }
        if self.character_references:
            result["character_references"] = list(self.character_references)
        return result
    def payload(self) -> dict[str, JsonValue]:
        result = self.serialize()
        result.pop("story_output_state")
        return result

    def set_scene(
        self, narration: str, dialogue: list[dict[str, JsonValue]], *, sequence: int | None = None,
    ) -> None:
        """Stage narration and dialogue together, ordered by their originating input."""
        self.output_cycle.submit(narration, dialogue, sequence)

    def _present_scene(self, output: StoryOutput) -> None:
        """Prepare and commit a promoted scene before its checkpoint is published.

        Keeping beautification ahead of the state mutation prevents clients from
        rendering the plain scene and then restarting it when styled spans arrive.
        """
        scene_narration = output.narration.strip()[:MAX_SCENE_NARRATION_CHARS]
        scene_dialogue = [dict(item) for item in output.dialogue][:3]
        narration_spans: list[dict[str, JsonValue]] = []
        beautifier = self.text_beautifier

        logger.info("[StoryState] New scene: %s", scene_narration)
        logger.info("[StoryState] New dialogue: %s", scene_dialogue)

        speech_enabled = bool(self._scene_speech_enabled and self._speech_provider)
        if speech_enabled:
            generation = self.dispatch(scene_dialogue)
        else:
            # A silent scene must still invalidate audio being synthesized for
            # its predecessor and give canvas a newer generation to render.
            generation = self.cancel()

        if beautifier and (scene_narration or scene_dialogue):
            logger.info(
                "[StoryState] Requesting scene text beautification (narration=%d chars, dialogue=%d line(s))",
                len(scene_narration),
                len(scene_dialogue),
            )
            try:
                beautified = TypeAdapter(dict[str, JsonValue]).validate_python(
                    beautifier.beautify_scene(scene_narration, scene_dialogue),
                )
                records = TypeAdapter(list[dict[str, JsonValue]])
                narration_spans = records.validate_python(beautified.get("narration_spans", []))
                scene_dialogue = records.validate_python(beautified.get("dialogue", scene_dialogue))[:3]
                logger.info(
                    "Scene beautification produced %d narration span(s) and %d dialogue line(s)",
                    len(narration_spans),
                    len(scene_dialogue),
                )
            except Exception as exc:
                logger.warning("Scene text beautification failed: %s", exc)

        self.narration = scene_narration
        self.narration_spans = narration_spans
        self.scene_dialogue = scene_dialogue
        self.committed_scene_speech_generation = generation
    def record_die_roll(self, roll: dict[str, Any]) -> None:
        """Record the latest die roll in story state and notify canvas clients."""
        self.last_die_roll = dict(roll) if isinstance(roll, dict) else None
        if self._notify_changed:
            self._notify_changed("latest")
    def get_story_planning_state(self) -> dict[str, Any]:
        """Return a snapshot of full story planning state."""
        return dict(self.story_planning_state) if isinstance(self.story_planning_state, dict) else {}
    def set_story_planning_state(self, state: dict[str, Any]) -> None:
        """Persist full story planning state and notify canvas clients."""
        self.story_planning_state = normalize_character_models(state) if isinstance(state, dict) else {}
        if self._persist:
            self._persist()
        if self._notify_changed:
            self._notify_changed("latest")

    def set_player_character(self, player: Optional[PlayerCharacterState]) -> None:
        self.player_character = player

    def get_player_character(self) -> Optional[PlayerCharacterState]:
        return self.player_character

    def set_characters(self, characters: dict[str, CharacterState]) -> None:
        self.characters = dict(characters)

    def get_characters(self) -> dict[str, CharacterState]:
        return dict(self.characters)

    def get_present_characters(self) -> list[CharacterState]:
        return list(self.characters.values())

    def set_character_references(self, refs: list[str]) -> None:
        self.character_references = list(refs)

    def get_character_references(self) -> list[str]:
        if self.character_references:
            return list(self.character_references)
        refs: list[str] = []
        if self.player_character is not None and self.player_character.reference:
            clean_ref = str(self.player_character.reference).strip()
            if clean_ref and clean_ref not in refs:
                refs.append(clean_ref)
        for char in self.characters.values():
            if char.image_reference:
                clean_ref = str(char.image_reference).strip()
                if clean_ref and clean_ref not in refs:
                    refs.append(clean_ref)
        return refs
    def get_character_voice(self, speaker: str) -> str | None:
        return self.character_voice_assignments.get(speaker_key(speaker))
    def assign_character_voice(self, speaker: str, voice: str) -> None:
        self.character_voice_assignments[speaker_key(speaker)] = str(voice)
        if self._persist:
            self._persist()
    def update_character_voice_tags(self, voice_tags: Mapping[str, Any]) -> None:
        """Store character voice lookup tags internally."""
        if not isinstance(voice_tags, Mapping):
            return
        allowed = {"male": "male", "female": "female", "nonbinary": "nonbinary", "nb": "nonbinary"}
        for speaker, tags in voice_tags.items():
            key = speaker_key(speaker)
            if isinstance(tags, (list, tuple, set)):
                cleaned = [str(t).strip().lower().replace("-", "") for t in tags]
            elif isinstance(tags, str):
                cleaned = [str(tags).strip().lower().replace("-", "")]
            else:
                cleaned = []
            self.character_voice_tags[key] = [
                allowed[c] if c in allowed else c
                for c in cleaned
                if c in allowed or re.fullmatch(r"[a-z_]+=[^=]+", c)
            ]
        if self._persist:
            self._persist()

    def set_character_voice_tags(self, speaker: str, tags: Any) -> None:
        """Set voice lookup tags for a single character internally."""
        self.update_character_voice_tags({speaker: tags})

    def get_character_voice_tags(self, speaker: str) -> list[str]:
        key = speaker_key(speaker)
        if key in self.character_voice_tags and self.character_voice_tags[key]:
            return list(self.character_voice_tags[key])
        character = self._character(speaker)
        tags = (character.get("voice_tags") or character.get("gender", [])) if character else []
        candidates: list[str] = []
        if isinstance(tags, (list, tuple, set)):
            candidates = [str(tag).strip().lower().replace("-", "") for tag in tags]
        elif isinstance(tags, str):
            candidates = [str(tags).strip().lower().replace("-", "")]
        allowed = {"male": "male", "female": "female", "nonbinary": "nonbinary", "nb": "nonbinary"}
        return [
            allowed[c] if c in allowed else c
            for c in candidates
            if c in allowed or re.fullmatch(r"[a-z_]+=[^=]+", c)
        ]
    def get_character_description(self, speaker: str) -> str:
        character = self._character(speaker)
        if character:
            return " ".join(str(character.get(key) or "") for key in ("name", "description", "personality", "motivation", "quirk")).strip()
        return speaker
    def _character(self, speaker: str) -> dict[str, object] | None:
        characters = self.story_planning_state.get("characters", [])
        normalized = speaker.strip().lower()
        if isinstance(characters, list):
            return next((character for character in characters if isinstance(character, dict) and str(character.get("name") or "").strip().lower() == normalized), None)
        return None

    def enable_scene_speech(self, provider: SpeechProvider, *, accent_augmentation: bool = True) -> None:
        if provider is None:
            raise TypeError("enable_scene_speech requires a SpeechProvider")
        self._accent_augmentation = bool(accent_augmentation)
        if self._scene_speech_enabled and self._speech_provider is not None:
            return
        self._speech_provider = provider
        self._scene_speech_enabled = True

    @property
    def speech_provider(self) -> SpeechProvider | None:
        """The provider used for scene voices, when character voicing is enabled."""
        return self._speech_provider

    def cancel(self) -> int:
        """Invalidates any pending or in-flight scene synthesis."""
        with self._speech_lock:
            self._active_speech_generation += 1
            return self._active_speech_generation

    def dispatch(self, dialogue: list[dict[str, str]]) -> int:
        logger.debug("[StoryState] Dispatching scene speech for generation %d", self._active_speech_generation)
        with self._speech_lock:
            self._active_speech_generation += 1
            generation = self._active_speech_generation

        spoken = [
            {**line, "voice": self._voice_for(str(line.get("speaker") or "Narrator"))}
            for line in dialogue
            if line.get("kind") != "thought" and str(line.get("text") or "").strip()
        ]
        if spoken:
            self._get_executor().submit(self._synthesize_scene_speech, spoken, generation)
        return generation
    def _voice_for(self, speaker: str) -> str:
        key = speaker_key(speaker)
        existing = self.character_voice_assignments.get(key)
        if existing:
            return existing

        tags = self.get_character_voice_tags(speaker)
        used = set(self.character_voice_assignments.values())
        if self._speech_provider is not None:
            voice = self._speech_provider.select_voice({
                "voice_tags": tags,
                "description": self.get_character_description(speaker),
            }, exclude=used)
        else:
            voice = "default"

        self.character_voice_assignments[key] = voice
        if self._persist:
            self._persist()
        logger.info("[StoryState] Assigned %s to %s", voice, speaker)
        return voice
    def _synthesize_line(
        self,
        line: dict[str, str],
        generation: int,
    ) -> tuple[dict[str, str], SpeechSynthesisResult | None]:
        with self._speech_lock:
            if generation != self._active_speech_generation:
                return line, None
        speaker = str(line.get("speaker") or "Narrator").strip()[:80] or "Narrator"
        try:
            voice = str(line["voice"])
            if self._speech_provider is None:
                return line, None
            result = self._speech_provider.synthesize(SpeechSynthesisRequest(
                text=str(line["text"]),
                voice=voice,
                voice_tags=tuple(self.get_character_voice_tags(speaker)),
                voice_instruction=str(line.get("voice_instruction") or "").strip() or None,
                accent_augmentation=getattr(self, "_accent_augmentation", True),
            ))
            with self._speech_lock:
                if generation != self._active_speech_generation:
                    logger.debug(
                        "[StoryState] Discarding stale audio (gen %d != current %d)",
                        generation,
                        self._active_speech_generation,
                    )
                    return line, None
            return line, result
        except (SpeechProviderError, OSError, ValueError) as exc:
            logger.warning("[StoryState] Failed to synthesize dialogue for %s: %s", speaker, exc)
            return line, None

    def _publish_line_audio(
        self,
        line: dict[str, str],
        result: SpeechSynthesisResult,
        generation: int,
    ) -> None:
        logger.debug("[StoryState] Publishing line audio for line %s", line)
        with self._speech_lock:
            if generation != self._active_speech_generation:
                logger.debug(
                    "[StoryState] Discarding stale audio (gen %d != current %d)",
                    generation,
                    self._active_speech_generation,
                )
                return
        speaker = str(line.get("speaker") or "Narrator").strip()[:80] or "Narrator"
        voice = str(line["voice"])
        audio_b64 = base64.b64encode(result.audio_bytes).decode("ascii")
        mime = result.mime_type or "audio/mpeg"
        audio_url = f"data:{mime};base64,{audio_b64}"

        if callable(self.publish_audio_fn):
            self.publish_audio_fn({
                "type": "scene_speech_ready",
                "speaker": speaker,
                "voice": voice,
                "audio_url": audio_url,
                "mime_type": result.mime_type,
                "speech_generation": generation,
            })

    def _synthesize_scene_speech(self, dialogue: list[dict[str, str]], generation: int) -> None:
        logger.debug("[StoryState] Dispatching scene speech for generation %d", generation)
        with self._speech_lock:
            if generation != self._active_speech_generation:
                logger.debug(
                    "[StoryState] Aborting stale synthesis (gen %d != current %d)",
                    generation,
                    self._active_speech_generation,
                )
                return

        if not dialogue or self._speech_provider is None:
            return

        if len(dialogue) == 1:
            line, result = self._synthesize_line(dialogue[0], generation)
            with self._speech_lock:
                if generation != self._active_speech_generation:
                    logger.debug(
                        "[StoryState] Aborting stale synthesis (gen %d != current %d)",
                        generation,
                        self._active_speech_generation,
                    )
                    return
            if result is not None:
                self._publish_line_audio(line, result, generation)
            return

        worker_count = min(len(dialogue), MAX_PARALLEL_SCENE_SPEECH)
        gathered: list[tuple[dict[str, str], SpeechSynthesisResult]] = []
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="scene-speech-line") as pool:
            futures = [pool.submit(self._synthesize_line, line, generation) for line in dialogue]
            for future in futures:
                with self._speech_lock:
                    if generation != self._active_speech_generation:
                        logger.debug(
                            "[StoryState] Aborting stale synthesis (gen %d != current %d)",
                            generation,
                            self._active_speech_generation,
                        )
                        return
                try:
                    line, result = future.result()
                except Exception as exc:
                    logger.warning("[StoryState] Line synthesis future failed: %s", exc)
                    continue

                if result is not None:
                    gathered.append((line, result))

        with self._speech_lock:
            if generation != self._active_speech_generation:
                logger.debug(
                    "[StoryState] Aborting stale synthesis (gen %d != current %d)",
                    generation,
                    self._active_speech_generation,
                )
                return

        for line, result in gathered:
            self._publish_line_audio(line, result, generation)
    @property
    def _scene_speech(self) -> Any:
        return self if self._scene_speech_enabled and self._speech_provider else None
    def _get_executor(self) -> ThreadPoolExecutor:
        if self._speech_executor is None:
            self._speech_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scene-speech")
        return self._speech_executor
    @property
    def _executor(self) -> ThreadPoolExecutor:
        return self._get_executor()
    @property
    def _lock(self) -> threading.Lock:
        return self._speech_lock
    @property
    def _generation(self) -> int:
        return self._active_speech_generation
    @_generation.setter
    def _generation(self, value: int) -> None:
        self._active_speech_generation = value
    @property
    def assignments(self) -> dict[str, str]:
        return self.character_voice_assignments
    @assignments.setter
    def assignments(self, value: dict[str, str]) -> None:
        self.character_voice_assignments = value
