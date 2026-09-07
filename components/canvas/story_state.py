"""Persisted story-planning, spoken-scene, and dialogue state."""

from __future__ import annotations

import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
from typing import Any

from providers import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
)

logger = logging.getLogger("components.canvas_state")

MAX_PARALLEL_SCENE_SPEECH = 4


def speaker_key(speaker: str) -> str:
    return re.sub(r"\s+", " ", str(speaker or "Narrator").strip()).casefold()[:80] or "narrator"


class StoryState:
    def __init__(
        self,
        persist: Callable[[], None] | None = None,
        notify_changed: Callable[..., None] | None = None,
        publish_audio_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._persist = persist
        self._notify_changed = notify_changed
        self.publish_audio_fn = publish_audio_fn
        self.named_elements: list[dict[str, str]] = []
        self.story_planning_state: dict[str, str] = {}
        self.scene_dialogue: list[dict[str, Any]] = []
        self.narration = ""
        self.narration_spans: list[dict[str, Any]] = []
        self.character_voice_assignments: dict[str, str] = {}
        self._character_lookup: Callable[[str], Any] | None = None
        self._text_beautifier: Any = None
        self._speech_provider: SpeechProvider | None = None
        self._scene_speech_enabled = False
        self._speech_executor: ThreadPoolExecutor | None = None
        self._speech_lock = threading.Lock()
        self._speech_generation = 0
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
    def sticky_notes(self) -> list[dict[str, str]]:
        notes = self.story_planning_state.get("sticky_notes", self.named_elements)
        return [note for note in notes if isinstance(note, dict)] if isinstance(notes, list) else []
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
        if character_lookup is not None:
            self._character_lookup = character_lookup
        if provider is not None:
            self.enable_scene_speech(provider)

        if data is None or not isinstance(data, dict):
            return

        named = data.get("named_elements")
        if isinstance(named, list):
            self.named_elements = [dict(x) for x in named if isinstance(x, dict)]

        planning = data.get("story_planning_state")
        if isinstance(planning, dict):
            self.story_planning_state = dict(planning)

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
    def serialize(self) -> dict[str, object]:
        return {
            "named_elements": self.named_elements,
            "story_planning_state": self.story_planning_state,
            "scene_dialogue": self.scene_dialogue,
            "narration": self.narration,
            "narration_spans": self.narration_spans,
            "character_voice_assignments": self.character_voice_assignments,
        }
    payload = serialize
    def set_scene(self, narration: str, dialogue: list[dict[str, Any]]) -> None:
        """Commit a fully beautified scene and notify canvas clients once.

        Keeping beautification ahead of the state mutation prevents clients from
        rendering the plain scene and then restarting it when styled spans arrive.
        """
        scene_narration = " ".join(str(narration or "").strip().split()[:45])[:500]
        scene_dialogue = [dict(item) for item in (dialogue or []) if isinstance(item, dict)][:3]
        narration_spans: list[dict[str, Any]] = []
        beautifier = self.text_beautifier

        logger.info("[StoryState] New scene: %s", scene_narration)
        logger.info("[StoryState] New dialogue: %s", scene_dialogue)

        if beautifier and (scene_narration or scene_dialogue):
            logger.info(
                "[StoryState] Requesting scene text beautification (narration=%d chars, dialogue=%d line(s))",
                len(scene_narration),
                len(scene_dialogue),
            )
            try:
                beautified = beautifier.beautify_scene(scene_narration, scene_dialogue)
                if not isinstance(beautified, dict):
                    raise TypeError("scene beautifier returned a non-dictionary result")
                narration_spans = [
                    dict(span)
                    for span in beautified.get("narration_spans", [])
                    if isinstance(span, dict)
                ]
                beautified_dialogue = beautified.get("dialogue", scene_dialogue)
                if not isinstance(beautified_dialogue, list):
                    raise TypeError("scene beautifier returned non-list dialogue")
                scene_dialogue = [
                    dict(item) for item in beautified_dialogue if isinstance(item, dict)
                ][:3]
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
        if self._persist:
            self._persist()
        if self._notify_changed:
            self._notify_changed("latest")
        if self._scene_speech_enabled and self._speech_provider:
            self.dispatch(self.scene_dialogue)
    def get_sticky_notes(self) -> list[dict[str, str]]:
        """Return active sticky notes."""
        return self.sticky_notes()
    def set_sticky_notes(self, notes: list[dict[str, str]]) -> None:
        """Persist sticky notes to story state and notify canvas clients."""
        self.named_elements = [dict(n) for n in (notes or []) if isinstance(n, dict)]
        if not isinstance(self.story_planning_state, dict):
            self.story_planning_state = {}
        self.story_planning_state["sticky_notes"] = list(self.named_elements)
        if self._persist:
            self._persist()
        if self._notify_changed:
            self._notify_changed("latest")
    def get_story_planning_state(self) -> dict[str, Any]:
        """Return a snapshot of full story planning state."""
        return dict(self.story_planning_state) if isinstance(self.story_planning_state, dict) else {}
    def set_story_planning_state(self, state: dict[str, Any]) -> None:
        """Persist full story planning state, synchronize sticky notes, and notify canvas clients."""
        self.story_planning_state = dict(state) if isinstance(state, dict) else {}
        if "sticky_notes" in self.story_planning_state and isinstance(self.story_planning_state["sticky_notes"], list):
            self.named_elements = [dict(n) for n in self.story_planning_state["sticky_notes"] if isinstance(n, dict)]
        if self._persist:
            self._persist()
        if self._notify_changed:
            self._notify_changed("latest")
    def get_character_voice(self, speaker: str) -> str | None:
        return self.character_voice_assignments.get(speaker_key(speaker))
    def assign_character_voice(self, speaker: str, voice: str) -> None:
        self.character_voice_assignments[speaker_key(speaker)] = str(voice)
        if self._persist:
            self._persist()
    def get_character_voice_tags(self, speaker: str) -> list[str]:
        character = self._character(speaker)
        tags = character.get("voice_tags", []) if character else []
        if isinstance(tags, (list, tuple, set)):
            return [str(tag).strip().lower() for tag in tags if str(tag).strip().lower() in {"male", "female"}]
        return [tags.strip().lower()] if isinstance(tags, str) and tags.strip().lower() in {"male", "female"} else []
    def get_character_description(self, speaker: str) -> str:
        character = self._character(speaker)
        if character:
            return " ".join(str(character.get(key) or "") for key in ("name", "description", "personality", "motivation", "quirk")).strip()
        normalized = speaker.strip().lower()
        for note in self.sticky_notes():
            if str(note.get("topic") or note.get("name") or "").strip().lower() == normalized:
                return f"{note.get('topic') or note.get('name', '')} {note.get('info') or note.get('content') or note.get('description') or ''}".strip()
        return speaker
    def _character(self, speaker: str) -> dict[str, object] | None:
        characters = self.story_planning_state.get("characters", [])
        normalized = speaker.strip().lower()
        if isinstance(characters, list):
            return next((character for character in characters if isinstance(character, dict) and str(character.get("name") or "").strip().lower() == normalized), None)
        return None
    def enable_scene_speech(self, provider: SpeechProvider | None = None) -> None:
        if self._scene_speech_enabled and self._speech_provider is not None:
            return
        if provider is None:
            from providers import SpeechProviderError, get_speech_provider
            try:
                provider = get_speech_provider("fal-seed-speech")
            except SpeechProviderError:
                from providers.fal_seed_speech_provider import FalSeedSpeechProvider
                provider = FalSeedSpeechProvider()
        self._speech_provider = provider
        self._scene_speech_enabled = True
    def cancel(self) -> None:
        """Invalidates any pending or in-flight scene synthesis."""
        with self._speech_lock:
            self._speech_generation += 1
    def dispatch(self, dialogue: list[dict[str, str]]) -> None:
        with self._speech_lock:
            self._speech_generation += 1
            generation = self._speech_generation

        spoken = [
            {**line, "voice": self._voice_for(str(line.get("speaker") or "Narrator"))}
            for line in dialogue
            if line.get("kind") != "thought" and str(line.get("text") or "").strip()
        ]
        if spoken:
            self._get_executor().submit(self._synthesize_scene, spoken, generation)
    def _voice_for(self, speaker: str) -> str:
        key = speaker_key(speaker)
        existing = self.character_voice_assignments.get(key)
        if existing:
            return existing

        tags = self._character_lookup(speaker) if self._character_lookup else self.get_character_voice_tags(speaker)
        used = set(self.character_voice_assignments.values())
        if self._speech_provider is not None:
            voice = self._speech_provider.select_voice(tags, exclude=used)
        else:
            voice = "default"

        self.character_voice_assignments[key] = voice
        if self._persist:
            self._persist()
        logger.info("[SceneSpeech] Assigned %s to %s", voice, speaker)
        return voice
    def _synthesize_line(
        self,
        line: dict[str, str],
        generation: int,
    ) -> tuple[dict[str, str], SpeechSynthesisResult | None]:
        with self._speech_lock:
            if generation != self._speech_generation:
                return line, None
        speaker = str(line.get("speaker") or "Narrator").strip()[:80] or "Narrator"
        try:
            voice = str(line["voice"])
            if self._speech_provider is None:
                return line, None
            result = self._speech_provider.synthesize(SpeechSynthesisRequest(text=str(line["text"]), voice=voice))
            with self._speech_lock:
                if generation != self._speech_generation:
                    logger.debug(
                        "[SceneSpeech] Discarding stale audio (gen %d != current %d)",
                        generation,
                        self._speech_generation,
                    )
                    return line, None
            return line, result
        except (SpeechProviderError, OSError, ValueError) as exc:
            logger.warning("[SceneSpeech] Failed to synthesize dialogue for %s: %s", speaker, exc)
            return line, None

    def _publish_line_audio(
        self,
        line: dict[str, str],
        result: SpeechSynthesisResult,
        generation: int,
    ) -> None:
        with self._speech_lock:
            if generation != self._speech_generation:
                logger.debug(
                    "[SceneSpeech] Discarding stale audio (gen %d != current %d)",
                    generation,
                    self._speech_generation,
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
                "generation": generation,
            })

    def _synthesize_scene(self, dialogue: list[dict[str, str]], generation: int) -> None:
        with self._speech_lock:
            if generation != self._speech_generation:
                logger.debug(
                    "[SceneSpeech] Aborting stale synthesis (gen %d != current %d)",
                    generation,
                    self._speech_generation,
                )
                return

        if not dialogue or self._speech_provider is None:
            return

        if len(dialogue) == 1:
            line, result = self._synthesize_line(dialogue[0], generation)
            if result is not None:
                self._publish_line_audio(line, result, generation)
            return

        worker_count = min(len(dialogue), MAX_PARALLEL_SCENE_SPEECH)
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="scene-speech-line") as pool:
            futures = [pool.submit(self._synthesize_line, line, generation) for line in dialogue]
            for future in futures:
                with self._speech_lock:
                    if generation != self._speech_generation:
                        logger.debug(
                            "[SceneSpeech] Aborting stale synthesis (gen %d != current %d)",
                            generation,
                            self._speech_generation,
                        )
                        return
                try:
                    line, result = future.result()
                except Exception as exc:
                    logger.warning("[SceneSpeech] Line synthesis future failed: %s", exc)
                    continue

                if result is not None:
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
        return self._speech_generation
    @_generation.setter
    def _generation(self, value: int) -> None:
        self._speech_generation = value
    @property
    def assignments(self) -> dict[str, str]:
        return self.character_voice_assignments
    @assignments.setter
    def assignments(self, value: dict[str, str]) -> None:
        self.character_voice_assignments = value
