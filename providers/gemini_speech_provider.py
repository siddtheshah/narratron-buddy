"""Gemini Flash text-to-speech provider."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Any, Iterable, Mapping

from google import genai
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
)

GEMINI_VOICES = (
    "Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
# Gender presentation follows Google's published voice catalog.
GEMINI_FEMALE_VOICES = (
    "Kore", "Aoede", "Leda", "Zephyr", "Callirrhoe", "Autonoe", "Despina",
    "Erinome", "Laomedeia", "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
)
GEMINI_MALE_VOICES = tuple(voice for voice in GEMINI_VOICES if voice not in GEMINI_FEMALE_VOICES)
# Timbre labels from https://ai.google.dev/gemini-api/docs/speech-generation.
GEMINI_VOICE_QUALITIES = {
    "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm",
    "Fenrir": "Excitable", "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy",
    "Callirrhoe": "Easy-going", "Autonoe": "Bright", "Enceladus": "Breathy",
    "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth", "Despina": "Smooth",
    "Erinome": "Clear", "Algenib": "Gravelly", "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat", "Achernar": "Soft", "Alnilam": "Firm", "Schedar": "Even",
    "Gacrux": "Mature", "Pulcherrima": "Forward", "Achird": "Friendly",
    "Zubenelgenubi": "Casual", "Vindemiatrix": "Gentle", "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable", "Sulafat": "Warm",
}
VOICE_FIELDS = ("language_code", "region_code", "gender", "accent", "pitch", "persona", "contexts")
VoiceProfile = Iterable[str] | str | Mapping[str, JsonValue] | None
VoiceFilters = dict[str, list[str]]
_PROFILE_ADAPTER = TypeAdapter(dict[str, JsonValue])
_STRING_ADAPTER = TypeAdapter(str)
_TAGS_ADAPTER = TypeAdapter(list[str])


def _tag_values(value: JsonValue | Iterable[str]) -> list[str]:
    """Validate the supported scalar/list forms at the input boundary."""
    if value is None:
        return []
    try:
        return [_STRING_ADAPTER.validate_python(value, strict=True)]
    except ValidationError:
        return _TAGS_ADAPTER.validate_python(value)


class CatalogVoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    language_code: str | None = None
    region_code: str | None = None
    gender: str | None = None
    accent: str | None = None
    pitch: str | None = None
    persona: str | None = None
    context: str | None = None
    description: str | None = None
    display_name: str | None = None

    def metadata(self) -> dict[str, str]:
        return {
            "language_code": self.language_code or "",
            "region_code": self.region_code or "",
            "gender": self.gender or "",
            "accent": self.accent or "",
            "pitch": self.pitch or "",
            "persona": self.persona or "",
            "contexts": self.context or "",
        }


class CatalogPage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    voices: list[CatalogVoice] | None = None
    next_page_token: str | None = None


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
        self.voice_language_code = (voice_language_code or "").strip() or None
        self._voice_catalog_cache: dict[tuple[tuple[str, tuple[str, ...]], ...], tuple[CatalogVoice, ...]] = {}
        self._voice_selection_cache: dict[tuple[str, tuple[str, ...]], str] = {}
        self._voice_tag_catalog_cache: Mapping[str, tuple[str, ...]] | None = None

    def get_supported_voice_tags(self) -> Mapping[str, tuple[str, ...]]:
        """Discover supported values from all pages of the voice catalog."""
        if self._voice_tag_catalog_cache is not None:
            return self._voice_tag_catalog_cache
        voices = self._list_extended_voices({})
        catalog: dict[str, set[str]] = {field: set() for field in VOICE_FIELDS}
        for voice in voices:
            for field, value in voice.metadata().items():
                if value:
                    catalog[field].add("nonbinary" if field == "gender" and value.casefold() == "neutral" else value)
        self._voice_tag_catalog_cache = {
            field: tuple(sorted(values)) for field, values in catalog.items() if values
        } or {"gender": ("female", "male", "nonbinary")}
        return self._voice_tag_catalog_cache

    def select_voice(
        self,
        voice_tags: VoiceProfile = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: JsonValue,
    ) -> str:
        profile, tags = self._normalize_profile(voice_tags)
        filters = self._voice_library_filters(profile, tags, self.voice_language_code)
        canonical = repr(tuple(sorted((key, tuple(sorted(value.casefold() for value in values))) for key, values in filters.items())))
        canonical += repr(sorted({tag.strip().casefold() for tag in tags if "=" not in tag and ":" not in tag and tag.strip().casefold().replace("-", "") not in {"male", "female", "nonbinary", "nb", "neutral"}}))
        # Mutable character prose must not change casting. A name distinguishes
        # characters sharing a profile; anonymous profiles use canonical tags.
        identity = str(profile.get("name") or "").strip().casefold() or canonical
        excluded = set(exclude or ())
        cache_key = (identity + canonical, tuple(sorted(excluded)))
        if cache_key in self._voice_selection_cache:
            return self._voice_selection_cache[cache_key]

        voices = self._list_extended_voices(filters)
        available = [voice for voice in voices if voice.id and voice.id not in excluded]
        if not available:
            # Exact filters are ANDed by Gemini. Keep explicit gender/language
            # constraints, then rank the remaining hints. An implicit en-US
            # default must also allow en-GB voices for a British accent.
            required = {key: values for key, values in filters.items() if key in {"gender", "language_code"}}
            language_family: str | None = None
            explicit_filters = self._voice_library_filters(profile, tags, None)
            if self.voice_language_code and "language_code" not in explicit_filters and ("accent" in filters or "region_code" in filters):
                required.pop("language_code", None)
                language_family = self.voice_language_code.partition("-")[0].casefold()
            if required != filters:
                voices = self._list_extended_voices(required)
                available = [
                    voice for voice in voices
                    if voice.id and voice.id not in excluded
                    and (not language_family or not voice.language_code or voice.language_code.partition("-")[0].casefold() == language_family)
                ]

        if available:
            scores = {voice.id: self._match_score(voice, filters, tags) for voice in available}
            best = max(scores.values())
            candidates = [voice.id for voice in available if voice.id and scores[voice.id] == best]
        else:
            genders = filters.get("gender", [])
            pool = GEMINI_FEMALE_VOICES if genders == ["female"] else GEMINI_MALE_VOICES if genders == ["male"] else GEMINI_VOICES
            candidates = [voice for voice in pool if voice not in excluded]
            if not candidates:
                # Reuse a matching voice rather than silently changing gender.
                candidates = list(pool)
            scores = {
                voice: self._match_score(CatalogVoice(id=voice, persona=GEMINI_VOICE_QUALITIES[voice]), filters, tags)
                for voice in candidates
            }
            best = max(scores.values())
            candidates = [voice for voice in candidates if scores[voice] == best]
            logger.debug("Using curated Gemini voices for filters %s", filters)
        choice = self._stable_voice_choice(candidates, identity)
        self._voice_selection_cache[cache_key] = choice
        return choice

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        self._ensure_client()

        # Gemini 3.8's unary Interactions API returns a complete WAV asset.
        # Unlike the older preview, delivery direction is sent as structured
        # speech metadata, keeping it out of the literal transcript.
        voice = request.voice or (self.select_voice(request.voice_tags) if request.voice_tags else "Kore")
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

    def _list_extended_voices(self, filters: VoiceFilters) -> tuple[CatalogVoice, ...]:
        cache_key = tuple(sorted((key, tuple(sorted(value.casefold() for value in values))) for key, values in filters.items()))
        if cache_key in self._voice_catalog_cache:
            return self._voice_catalog_cache[cache_key]
        try:
            client = self._ensure_client()
            voices: list[CatalogVoice] = []
            page_token: str | None = None
            while True:
                options: dict[str, list[str] | int | str] = {"type_": ["prebuilt"], "page_size": 1000, **filters}
                if page_token:
                    options["page_token"] = page_token
                page = CatalogPage.model_validate(client.voices.list(**options))
                voices.extend(page.voices or [])
                page_token = page.next_page_token
                if not page_token:
                    break
            result = tuple(voices)
            self._voice_catalog_cache[cache_key] = result
            return result
        except Exception as exc:
            logger.debug("Gemini Extended Voice Library unavailable; using curated voices: %s", exc)
            return ()

    @staticmethod
    def _normalize_profile(voice_profile: VoiceProfile) -> tuple[dict[str, JsonValue], list[str]]:
        if voice_profile is None:
            return {}, []
        try:
            profile = _PROFILE_ADAPTER.validate_python(voice_profile)
        except ValidationError:
            return {}, _tag_values(voice_profile)
        return profile, _tag_values(profile.get("voice_tags"))

    @staticmethod
    def _voice_library_filters(
        profile: Mapping[str, JsonValue],
        tags: list[str],
        default_language_code: str | None,
    ) -> VoiceFilters:
        filters: VoiceFilters = {}
        for field in VOICE_FIELDS:
            value = profile.get(field)
            if field == "contexts":
                value = value or profile.get("context")
            if value:
                filters[field] = _tag_values(value)
        tagged: dict[str, list[str]] = {}
        for tag in tags:
            key, separator, value = tag.partition("=")
            if not separator:
                key, separator, value = tag.partition(":")
            key = key.strip().casefold()
            key = "contexts" if key == "context" else key
            if separator and key in VOICE_FIELDS and value.strip():
                tagged.setdefault(key, []).append(value.strip())
            elif not separator:
                gender = tag.strip().casefold().replace("-", "")
                if gender in {"male", "female", "nonbinary", "nb", "neutral"}:
                    tagged.setdefault("gender", []).append(gender)
        filters.update(tagged)
        if default_language_code and not filters.get("language_code"):
            filters["language_code"] = [default_language_code]
        aliases = {"nb": "neutral", "nonbinary": "neutral"}
        for field, values in filters.items():
            normalized = [value.strip() for value in values if value.strip()]
            if field == "gender":
                normalized = [aliases.get(value.casefold().replace("-", ""), value.casefold()) for value in normalized]
            filters[field] = sorted(set(normalized))
        return filters

    @staticmethod
    def _match_score(voice: CatalogVoice, filters: VoiceFilters, tags: list[str]) -> int:
        metadata = voice.metadata()
        weights = {"accent": 8, "region_code": 8, "pitch": 4, "persona": 2, "contexts": 1}
        score = sum(weight for field, weight in weights.items() if metadata[field].casefold() in [value.casefold() for value in filters.get(field, [])])
        prose = " ".join((voice.description or "", voice.persona or "", voice.display_name or "")).casefold()
        score += sum(1 for tag in set(tags) if "=" not in tag and ":" not in tag and tag.strip().casefold() in prose)
        return score

    @staticmethod
    def _stable_voice_choice(voices: list[str], identity: str) -> str:
        # Rendezvous hashing does not depend on ordering, duplicates, or pool
        # size. Removing another voice leaves the winning voice unchanged.
        return max(set(voices), key=lambda voice: (hashlib.sha256(f"{identity}\0{voice}".encode("utf-8")).digest(), voice))

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
