"""Automatic Seed Speech delivery for planner-authored canvas dialogue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import mimetypes
from pathlib import Path
import re
import time
import uuid
from typing import Any, Callable

from providers import SpeechProviderError, SpeechSynthesisRequest, get_speech_provider

logger = logging.getLogger(__name__)

# Seed Speech v2 FAL presets with English support. Hosts do not configure this
# pool; the first line for a speaker selects and persists one automatically.
SEED_CHARACTER_VOICES = (
    "vivi_mixed_en_zh_ja_es_id", "mindy_en_es_id_pt_zh", "stokie_en", "dacey_en",
    "tim_en", "kian_en_zh", "cedric_en_zh", "sophie_en_zh", "jean_en_zh",
    "magnus_en_zh", "mabel_en_zh", "nadia_en_zh", "opal_en_zh", "pearl_en_zh",
    "quentin_en_zh", "vienna_mixed_en_zh", "alina_mixed_en_zh", "corinne_mixed_en_zh",
    "esther_mixed_en_zh", "freya_mixed_en_zh", "gigi_mixed_en_zh", "holly_mixed_en_zh",
    "lyla_mixed_en_zh", "daisy_mixed_en_zh", "jess_ja_es_id_pt_en_zh",
    "pinky_es_ko_mixed_en_zh", "sandy_es_mixed_en_zh",
)


def speaker_key(speaker: str) -> str:
    return re.sub(r"\s+", " ", str(speaker or "Narrator").strip()).casefold()[:80] or "narrator"


class SceneSpeechDispatcher:
    """Serializes TTS work so audio events reach each browser in line order."""

    def __init__(self, theater_id: str, output_dir: Path, assignments: dict[str, str], persist_assignments: Callable[[], None], publish_audio: Callable[[dict[str, Any]], None]) -> None:
        self.theater_id = theater_id
        self.output_dir = output_dir
        self.assignments = assignments
        self._persist_assignments = persist_assignments
        self._publish_audio = publish_audio
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"scene-speech-{theater_id}")
        self._provider_unavailable = False

    def dispatch(self, dialogue: list[dict[str, str]]) -> None:
        # Select synchronously as part of accepting the scene update.  That
        # preserves the character identity even when FAL credentials are not
        # configured yet or synthesis later fails.
        spoken = [
            {**line, "voice": self._voice_for(str(line.get("speaker") or "Narrator"))}
            for line in dialogue
            if line.get("kind") != "thought" and str(line.get("text") or "").strip()
        ]
        if spoken:
            self._executor.submit(self._synthesize_scene, spoken)

    def _voice_for(self, speaker: str) -> str:
        key = speaker_key(speaker)
        existing = self.assignments.get(key)
        if existing in SEED_CHARACTER_VOICES:
            return existing
        offset = int(hashlib.sha256(f"{self.theater_id}:{key}".encode("utf-8")).hexdigest(), 16) % len(SEED_CHARACTER_VOICES)
        used = set(self.assignments.values())
        voice = next((SEED_CHARACTER_VOICES[(offset + index) % len(SEED_CHARACTER_VOICES)] for index in range(len(SEED_CHARACTER_VOICES)) if SEED_CHARACTER_VOICES[(offset + index) % len(SEED_CHARACTER_VOICES)] not in used), SEED_CHARACTER_VOICES[offset])
        self.assignments[key] = voice
        self._persist_assignments()
        logger.info("[SceneSpeech] Assigned %s to %s in theater %s", voice, speaker, self.theater_id)
        return voice

    def _synthesize_scene(self, dialogue: list[dict[str, str]]) -> None:
        if self._provider_unavailable:
            return
        try:
            provider = get_speech_provider("fal-seed-speech")
        except SpeechProviderError as exc:
            self._provider_unavailable = True
            logger.info("[SceneSpeech] Seed Speech unavailable: %s", exc)
            return
        for line in dialogue:
            speaker = str(line.get("speaker") or "Narrator").strip()[:80] or "Narrator"
            try:
                voice = str(line["voice"])
                result = provider.synthesize(SpeechSynthesisRequest(text=str(line["text"]), voice=voice))
                extension = mimetypes.guess_extension(result.mime_type) or ".mp3"
                speech_dir = self.output_dir / "speech"
                speech_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}{extension}"
                (speech_dir / filename).write_bytes(result.audio_bytes)
                self._publish_audio({"type": "scene_speech_ready", "speaker": speaker, "voice": voice, "audio_url": f"/theaters/{self.theater_id}/output/speech/{filename}", "mime_type": result.mime_type})
            except (SpeechProviderError, OSError, ValueError) as exc:
                logger.warning("[SceneSpeech] Failed to synthesize dialogue for %s: %s", speaker, exc)
