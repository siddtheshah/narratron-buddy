"""Google Cloud Chirp 3: HD text-to-speech provider."""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
    extract_voice_tags,
)

CHIRP_NARRATOR_VOICE = "en-US-Chirp3-HD-Laomedeia"

CHIRP_VOICES = (
    "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Puck", "en-US-Chirp3-HD-Fenrir",
    "en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Kore", "en-US-Chirp3-HD-Leda",
    "en-US-Chirp3-HD-Laomedeia",
)
CHIRP_FEMALE_VOICES = (
    "en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Kore", "en-US-Chirp3-HD-Leda",
    "en-US-Chirp3-HD-Laomedeia",
)
CHIRP_MALE_VOICES = (
    "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Puck", "en-US-Chirp3-HD-Fenrir",
)


class SynthesisInputProtocol(Protocol):
    pass


class VoiceSelectionParamsProtocol(Protocol):
    pass


class AudioConfigProtocol(Protocol):
    pass


class AudioEncodingProtocol(Protocol):
    MP3: int


class ChirpSynthesisResponse(Protocol):
    audio_content: bytes
    request_id: str | None


class ChirpClient(Protocol):
    def synthesize_speech(
        self,
        *,
        input: SynthesisInputProtocol,
        voice: VoiceSelectionParamsProtocol,
        audio_config: AudioConfigProtocol,
    ) -> ChirpSynthesisResponse: ...


class TextToSpeechModule(Protocol):
    AudioEncoding: AudioEncodingProtocol
    def SynthesisInput(self, *, text: str) -> SynthesisInputProtocol: ...
    def VoiceSelectionParams(self, *, name: str, language_code: str) -> VoiceSelectionParamsProtocol: ...
    def AudioConfig(self, *, audio_encoding: int, speaking_rate: float) -> AudioConfigProtocol: ...
    def TextToSpeechClient(self) -> ChirpClient: ...


class VoiceProfileModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    speaker: str = ""
    voice: str = ""
    preferred_voice: str = ""
    persona: str = ""
    description: str = ""
    gender: str = ""
    voice_tags: list[str] = []


_PROFILE_MAP_ADAPTER = TypeAdapter(dict[str, JsonValue])
_STRING_ADAPTER = TypeAdapter(str)
_LIST_STR_ADAPTER = TypeAdapter(list[str])


def _inspect_profile(voice_tags: Iterable[str] | str | Mapping[str, JsonValue] | None) -> tuple[VoiceProfileModel, list[str]]:
    if voice_tags is None:
        return VoiceProfileModel(), []
    try:
        mapping = _PROFILE_MAP_ADAPTER.validate_python(voice_tags)
        return VoiceProfileModel.model_validate(mapping), [str(tag) for tag in mapping.get("voice_tags", []) if tag]
    except ValidationError:
        pass
    try:
        text = _STRING_ADAPTER.validate_python(voice_tags)
        return VoiceProfileModel(voice_tags=[text]), [text]
    except ValidationError:
        pass
    try:
        tags_list = _LIST_STR_ADAPTER.validate_python(voice_tags)
        return VoiceProfileModel(voice_tags=tags_list), tags_list
    except ValidationError:
        pass
    return VoiceProfileModel(), []


class GoogleChirpSpeechProvider(SpeechProvider):
    id = "google-chirp-3-hd"
    display_name = "Google Cloud Chirp 3: HD"

    def __init__(
        self,
        model: str = CHIRP_NARRATOR_VOICE,
        client: ChirpClient | None = None,
        texttospeech_module: TextToSpeechModule | None = None,
    ) -> None:
        self.model = model
        self.client = client
        self.texttospeech = texttospeech_module

    def _ensure_client(self) -> None:
        if self.texttospeech is None:
            try:
                from google.cloud import texttospeech as texttospeech_module
                self.texttospeech = texttospeech_module
            except ImportError as exc:
                raise SpeechProviderError("google-cloud-texttospeech is required for Google Cloud Chirp TTS.") from exc
        if self.client is None:
            try:
                self.client = self.texttospeech.TextToSpeechClient()
            except Exception as exc:
                raise SpeechProviderError(f"Failed to initialize Google Cloud Text-to-Speech: {exc}") from exc

    def select_voice(
        self,
        voice_tags: Iterable[str] | str | Mapping[str, JsonValue] | None = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: JsonValue,
    ) -> str:
        profile, tags = _inspect_profile(voice_tags)
        all_tags = [t.lower() for t in tags]
        all_identifiers = [
            profile.name.strip().lower(),
            profile.speaker.strip().lower(),
            profile.voice.strip().lower(),
            profile.preferred_voice.strip().lower(),
            profile.persona.strip().lower(),
        ]
        is_narrator = (
            "narrator" in all_identifiers
            or any("narrator" in t for t in all_tags)
            or any("laomedeia" in ident for ident in all_identifiers if ident)
            or any("laomedeia" in t for t in all_tags)
        )
        if is_narrator:
            return CHIRP_NARRATOR_VOICE

        extracted_tags = extract_voice_tags(tags or profile.voice_tags or [profile.gender])
        excluded = set(exclude or ())

        if "female" in extracted_tags and "male" not in extracted_tags and "nonbinary" not in extracted_tags:
            pool = CHIRP_FEMALE_VOICES
        elif "male" in extracted_tags and "female" not in extracted_tags and "nonbinary" not in extracted_tags:
            pool = CHIRP_MALE_VOICES
        else:
            pool = CHIRP_VOICES

        available = [v for v in pool if v not in excluded]
        if not available:
            available = [v for v in CHIRP_VOICES if v not in excluded]
        if not available:
            available = list(pool) or list(CHIRP_VOICES)

        return available[0]

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        self._ensure_client()
        voice_name = request.voice or self.model
        language_code = "-".join(voice_name.split("-")[:2]) if voice_name.count("-") >= 1 else "en-US"
        try:
            response = self.client.synthesize_speech(
                input=self.texttospeech.SynthesisInput(text=request.text),
                voice=self.texttospeech.VoiceSelectionParams(name=voice_name, language_code=language_code),
                audio_config=self.texttospeech.AudioConfig(
                    audio_encoding=self.texttospeech.AudioEncoding.MP3,
                    speaking_rate=request.speed or 1.0,
                ),
            )
        except Exception as exc:
            raise SpeechProviderError(f"Google Cloud Chirp TTS request failed: {exc}") from exc
        audio_content = response.audio_content
        if not audio_content:
            raise SpeechProviderError("Google Cloud Chirp TTS returned no audio data.")
        return SpeechSynthesisResult(
            audio_bytes=bytes(audio_content),
            mime_type="audio/mpeg",
            provider=self.id,
            model=voice_name,
            request_id=response.request_id,
        )
