"""Automatic Seed Speech delivery for planner-authored canvas dialogue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import mimetypes
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Callable

from providers import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
)

logger = logging.getLogger(__name__)


def speaker_key(speaker: str) -> str:
    return re.sub(r"\s+", " ", str(speaker or "Narrator").strip()).casefold()[:80] or "narrator"


class SceneSpeechDispatcher:
    """Serializes TTS work so audio events reach each browser in line order."""

    def __init__(
        self,
        theater_id: str,
        output_dir: Path,
        assignments: dict[str, str],
        persist_assignments: Callable[[], None],
        publish_audio: Callable[[dict[str, Any]], None],
        provider: SpeechProvider,
        character_lookup: Callable[[str], Any] | None = None,
    ) -> None:
        self.theater_id = theater_id
        self.output_dir = output_dir
        self.assignments = assignments
        self._persist_assignments = persist_assignments
        self._publish_audio = publish_audio
        self._provider = provider
        self._character_lookup = character_lookup
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"scene-speech-{theater_id}")
        self._lock = threading.Lock()
        self._generation = 0

    def cancel(self) -> None:
        """Invalidates any pending or in-flight scene synthesis."""
        with self._lock:
            self._generation += 1

    def dispatch(self, dialogue: list[dict[str, str]]) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation

        spoken = [
            {**line, "voice": self._voice_for(str(line.get("speaker") or "Narrator"))}
            for line in dialogue
            if line.get("kind") != "thought" and str(line.get("text") or "").strip()
        ]
        if spoken:
            self._executor.submit(self._synthesize_scene, spoken, generation)

    def _voice_for(self, speaker: str) -> str:
        key = speaker_key(speaker)
        existing = self.assignments.get(key)
        if existing:
            return existing

        tags = self._character_lookup(speaker) if self._character_lookup else speaker
        used = set(self.assignments.values())
        voice = self._provider.select_voice(tags, exclude=used)

        self.assignments[key] = voice
        self._persist_assignments()
        logger.info("[SceneSpeech] Assigned %s to %s in theater %s", voice, speaker, self.theater_id)
        return voice

    def _synthesize_scene(self, dialogue: list[dict[str, str]], generation: int) -> None:
        for line in dialogue:
            with self._lock:
                if generation != self._generation:
                    logger.debug(
                        "[SceneSpeech] Aborting stale synthesis for theater %s (gen %d != current %d)",
                        self.theater_id,
                        generation,
                        self._generation,
                    )
                    return
            speaker = str(line.get("speaker") or "Narrator").strip()[:80] or "Narrator"
            try:
                voice = str(line["voice"])
                result = self._provider.synthesize(SpeechSynthesisRequest(text=str(line["text"]), voice=voice))
                with self._lock:
                    if generation != self._generation:
                        logger.debug(
                            "[SceneSpeech] Discarding stale audio for theater %s (gen %d != current %d)",
                            self.theater_id,
                            generation,
                            self._generation,
                        )
                        return
                extension = mimetypes.guess_extension(result.mime_type) or ".mp3"
                speech_dir = self.output_dir / "speech"
                speech_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}{extension}"
                (speech_dir / filename).write_bytes(result.audio_bytes)
                self._publish_audio({
                    "type": "scene_speech_ready",
                    "speaker": speaker,
                    "voice": voice,
                    "audio_url": f"/theaters/{self.theater_id}/output/speech/{filename}",
                    "mime_type": result.mime_type,
                    "generation": generation,
                })
            except (SpeechProviderError, OSError, ValueError) as exc:
                logger.warning("[SceneSpeech] Failed to synthesize dialogue for %s: %s", speaker, exc)

