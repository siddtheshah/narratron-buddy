"""Gemini Flash text-to-speech provider."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Any, Iterable, Mapping

from google import genai

from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
    extract_character_description,
    extract_voice_tags,
)

GEMINI_VOICES = (
    "Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
GEMINI_FEMALE_VOICES = ("Kore", "Aoede", "Leda")
GEMINI_MALE_VOICES = ("Puck", "Charon", "Fenrir", "Orus", "Zephyr")

logger = logging.getLogger(__name__)


class GeminiSpeechProvider(SpeechProvider):
    id = "gemini-flash-tts"
    display_name = "Gemini 3.8 Flash TTS"

    def __init__(
        self,
        model: str = "gemini-3.8-flash-tts",
        client: Any = None,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
        voice_language_code: str | None = "en-US",
    ) -> None:
        self.model = model
        self.client = client
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.voice_language_code = str(voice_language_code).strip() or None
        self._voice_catalog_cache: dict[tuple[tuple[str, tuple[str, ...]], ...], tuple[str, ...]] = {}
        self._voice_tag_catalog_cache: Mapping[str, tuple[str, ...]] | None = None

    def get_supported_voice_tags(self) -> Mapping[str, tuple[str, ...]]:
        """Discover the current Extended Voice Library filter values.

        Gemini's catalog changes independently of this application.  Asking
        the API here lets character generation offer only values that can be
        used by ``voices.list``.  A small gender-only fallback also keeps
        character generation available without credentials or connectivity.
        """
        if self._voice_tag_catalog_cache is not None:
            return self._voice_tag_catalog_cache

        fallback: Mapping[str, tuple[str, ...]] = {
            "gender": ("female", "male", "nonbinary"),
        }
        try:
            client = self._ensure_client()
            voices_api = getattr(client, "voices", None)
            if voices_api is None:
                self._voice_tag_catalog_cache = fallback
                return fallback
            voices: list[Any] = []
            page_token: str | None = None
            while True:
                kwargs: dict[str, Any] = {"type_": ["prebuilt"], "page_size": 1000}
                if page_token:
                    kwargs["page_token"] = page_token
                response = voices_api.list(**kwargs)
                page = getattr(response, "voices", None)
                if page is None and isinstance(response, Mapping):
                    page = response.get("voices")
                voices.extend(page or [])
                page_token = (
                    response.get("next_page_token")
                    if isinstance(response, Mapping)
                    else getattr(response, "next_page_token", None)
                )
                if not page_token:
                    break
            fields = ("language_code", "region_code", "gender", "accent", "pitch", "persona", "contexts")
            catalog: dict[str, set[str]] = {field: set() for field in fields}
            for voice in voices:
                for field in fields:
                    value = self._value(voice, field)
                    values = value if isinstance(value, (list, tuple, set)) else (value,)
                    catalog[field].update(str(item).strip() for item in values if item)
            result = {
                field: tuple(sorted(values))
                for field, values in catalog.items()
                if values
            }
            # Gemini uses ``neutral`` in its voice API, while character
            # generation uses the application's nonbinary convention.
            if "gender" in result:
                genders = tuple("nonbinary" if value == "neutral" else value for value in result["gender"])
                result["gender"] = tuple(dict.fromkeys(genders))
            self._voice_tag_catalog_cache = result or fallback
            return self._voice_tag_catalog_cache
        except Exception as exc:
            logger.debug("Gemini Extended Voice Library tags unavailable: %s", exc)
            self._voice_tag_catalog_cache = fallback
            return fallback

    def select_voice(
        self,
        voice_tags: Iterable[str] | str | Mapping[str, Any] | None = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> str:
        tags = extract_voice_tags(voice_tags)
        excluded = set(exclude or ())
        catalog_voices = self._list_extended_voices(voice_tags, tags)
        available_catalog_voices = [voice for voice in catalog_voices if voice not in excluded]
        if available_catalog_voices:
            return self._stable_voice_choice(available_catalog_voices, voice_tags)

        if "female" in tags and "male" not in tags and "nonbinary" not in tags:
            pool = GEMINI_FEMALE_VOICES
        elif "male" in tags and "female" not in tags and "nonbinary" not in tags:
            pool = GEMINI_MALE_VOICES
        else:
            pool = GEMINI_VOICES

        available = [v for v in pool if v not in excluded]
        if not available:
            available = [v for v in GEMINI_VOICES if v not in excluded]
        if not available:
            available = list(pool) or list(GEMINI_VOICES)

        return self._stable_voice_choice(available, voice_tags)

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        self._ensure_client()

        # Gemini 3.8's unary Interactions API returns a complete WAV asset.
        # Unlike the older preview, delivery direction is sent as structured
        # speech metadata, keeping it out of the literal transcript.
        voice = request.voice or "Kore"
        generation_config: dict[str, Any] = {"speech_config": [{"voice": voice}]}
        annotations: list[dict[str, str]] = []
        instruction = request.style_instruction()
        if instruction:
            annotations.append({"type": "speech_metadata", "style": instruction})
        input_data = [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": request.text,
                **({"annotations": annotations} if annotations else {}),
            }],
        }]
        response_format: dict[str, Any] = {"type": "audio", "mime_type": "audio/wav"}
        if request.sample_rate_hz:
            response_format["sample_rate"] = request.sample_rate_hz

        interactions = getattr(self.client, "interactions", None)
        if interactions is None:
            raise SpeechProviderError("Installed google-genai client does not support the Gemini Interactions TTS API.")

        response: Any = None
        pcm: bytes | None = None
        last_error: Exception | None = None
        attempts_made = 0
        for attempt in range(self.max_attempts):
            attempts_made = attempt + 1
            try:
                response = interactions.create(
                    model=self.model,
                    input=input_data,
                    response_format=response_format,
                    generation_config=generation_config,
                )
                encoded_audio = self._value(response, "output_audio", "data")
                if not encoded_audio:
                    raise ValueError("Gemini TTS returned no audio data.")
                pcm = base64.b64decode(encoded_audio) if isinstance(encoded_audio, str) else bytes(encoded_audio)
                break
            except Exception as exc:
                last_error = exc
                if not self._is_retryable(exc):
                    break
                if attempt + 1 < self.max_attempts and self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds * (2 ** attempt))
        if pcm is None:
            detail = str(last_error) if last_error else "unknown failure"
            raise SpeechProviderError(
                f"Gemini TTS request failed after {attempts_made} attempt(s): {detail}"
            ) from last_error

        return SpeechSynthesisResult(
            audio_bytes=pcm,
            mime_type="audio/wav",
            provider=self.id,
            model=self.model,
            request_id=(
                self._value(response, "id")
                or self._value(response, "request_id")
                or self._value(response, "response_id")
            ),
            usage=self._usage(response),
        )

    @staticmethod
    def _value(obj: Any, *path: str) -> Any:
        for key in path:
            obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if obj is None:
                return None
        return obj

    def _ensure_client(self) -> Any:
        if self.client is not None:
            return self.client
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise SpeechProviderError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured for Gemini TTS.")
        try:
            # TTS uses the Gemini Developer API's Interactions endpoint.
            # Be explicit so ambient Vertex/Enterprise flags used by the
            # rest of the app cannot redirect this API-key client.
            self.client = genai.Client(api_key=api_key, enterprise=False, vertexai=False)
            return self.client
        except Exception as exc:
            raise SpeechProviderError(f"Failed to initialize Gemini TTS client: {exc}") from exc

    def _list_extended_voices(
        self,
        voice_profile: Iterable[str] | str | Mapping[str, Any] | None,
        tags: list[str],
    ) -> tuple[str, ...]:
        """Return cached Extended Voice Library IDs, falling back silently offline."""
        filters = self._voice_library_filters(voice_profile, tags, self.voice_language_code)
        cache_key = tuple(sorted((key, tuple(value) if isinstance(value, list) else (str(value),)) for key, value in filters.items()))
        if cache_key in self._voice_catalog_cache:
            return self._voice_catalog_cache[cache_key]

        try:
            client = self._ensure_client()
            voices_api = getattr(client, "voices", None)
            if voices_api is None:
                return ()
            response = voices_api.list(**filters)
            voices = getattr(response, "voices", None)
            if voices is None and isinstance(response, Mapping):
                voices = response.get("voices")
            ids = tuple(
                str(self._value(voice, "id")).strip()
                for voice in (voices or [])
                if self._value(voice, "id")
            )
            self._voice_catalog_cache[cache_key] = ids
            return ids
        except Exception as exc:
            logger.debug("Gemini Extended Voice Library unavailable; using curated voices: %s", exc)
            return ()

    @staticmethod
    def _voice_library_filters(
        voice_profile: Iterable[str] | str | Mapping[str, Any] | None,
        tags: list[str],
        default_language_code: str | None,
    ) -> dict[str, Any]:
        """Build documented ListVoices filters from optional character metadata."""
        profile = dict(voice_profile) if isinstance(voice_profile, Mapping) else {}
        raw_tags = profile.get("voice_tags", voice_profile)
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        if isinstance(raw_tags, (list, tuple, set)):
            for raw_tag in raw_tags:
                key, separator, value = str(raw_tag).partition("=")
                if separator and key.strip() in {"language_code", "region_code", "gender", "accent", "pitch", "persona", "contexts"}:
                    profile.setdefault(key.strip(), value.strip())
        filters: dict[str, Any] = {"type_": ["prebuilt"], "page_size": 1000}
        gender = next((tag for tag in tags if tag in {"female", "male"}), None)
        if gender:
            filters["gender"] = [gender]
        elif "nonbinary" in tags:
            filters["gender"] = ["neutral"]
        for field in ("language_code", "region_code", "accent", "pitch", "persona", "contexts"):
            value = profile.get(field) if profile else None
            if field == "language_code" and not value:
                value = default_language_code
            if value:
                if field == "gender" and str(value).lower() == "nonbinary":
                    value = "neutral"
                filters[field] = [str(item) for item in value] if isinstance(value, (list, tuple, set)) else [str(value)]
        # A character's prose description is intentionally not used as a
        # search term: ListVoices search is substring matching, not semantic.
        return filters

    @staticmethod
    def _stable_voice_choice(voices: list[str], voice_profile: Any) -> str:
        identity = extract_character_description(voice_profile) or " ".join(extract_voice_tags(voice_profile))
        index = int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:4], "big") % len(voices)
        return voices[index]

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Retry transient transport/rate-limit/server failures, not bad requests."""
        status = getattr(exc, "code", None)
        if not isinstance(status, int):
            status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status == 429 or status >= 500
        return True

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = (response.get("usage") or response.get("usage_metadata")) if isinstance(response, dict) else (getattr(response, "usage", None) or getattr(response, "usage_metadata", None))
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        return dict(usage) if isinstance(usage, dict) else {}
