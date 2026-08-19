"""Provider-neutral image, music, and text response generation integrations."""

from providers.image_provider import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
    ImageProviderError,
    ImageReference,
)
from providers.openai_image_provider import OpenAIImageProvider
from providers.fal_flux_klein_provider import FalFluxKleinProvider
from providers.hybrid_image_provider import HybridImageProvider, ImageClassifierResponse
from providers.music_provider import (
    MusicAdaptationRequest,
    MusicAudioArtifact,
    MusicAdapter,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
    MusicProviderError,
)
from providers.lyria_music_provider import LyriaMusicProvider
from providers.fal_stable_audio_adapter import FalStableAudioAdapter
from providers.adapted_music_provider import AdaptedMusicProvider
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
    TextResponseResult,
    parse_and_validate_structured_response,
)
from providers.gemini_text_response_provider import GeminiTextResponseProvider
from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
    extract_character_description,
    extract_voice_tags,
)
from providers.gemini_speech_provider import GEMINI_VOICES, GeminiSpeechProvider
from providers.fal_seed_speech_provider import SEED_CHARACTER_VOICES, FalSeedSpeechProvider
from providers.google_chirp_speech_provider import CHIRP_VOICES, GoogleChirpSpeechProvider
from providers.registry import (
    get_image_provider,
    get_music_provider,
    get_music_adapter,
    get_text_response_provider,
    list_image_provider_specs,
    list_music_provider_specs,
    list_music_adapter_specs,
    list_text_response_provider_specs,
    get_speech_provider,
    list_speech_provider_specs,
)

__all__ = [
    "ImageGenerationRequest",
    "ImageGenerationResult",
    "ImageProvider",
    "ImageProviderError",
    "ImageReference",
    "OpenAIImageProvider",
    "FalFluxKleinProvider",
    "HybridImageProvider",
    "ImageClassifierResponse",
    "MusicGenerationRequest",
    "MusicAdaptationRequest",
    "MusicAudioArtifact",
    "MusicGenerationResult",
    "MusicProvider",
    "MusicProviderError",
    "MusicAdapter",
    "LyriaMusicProvider",
    "FalStableAudioAdapter",
    "AdaptedMusicProvider",
    "TextResponseRequest",
    "TextResponseResult",
    "TextResponseProvider",
    "TextResponseProviderError",
    "GeminiTextResponseProvider",
    "parse_and_validate_structured_response",
    "SpeechProvider",
    "SpeechProviderError",
    "SpeechSynthesisRequest",
    "SpeechSynthesisResult",
    "extract_character_description",
    "extract_voice_tags",
    "GeminiSpeechProvider",
    "GEMINI_VOICES",
    "FalSeedSpeechProvider",
    "SEED_CHARACTER_VOICES",
    "GoogleChirpSpeechProvider",
    "CHIRP_VOICES",
    "get_image_provider",
    "get_music_provider",
    "get_text_response_provider",
    "list_image_provider_specs",
    "list_music_provider_specs",
    "get_music_adapter",
    "list_music_adapter_specs",
    "list_text_response_provider_specs",
    "get_speech_provider",
    "list_speech_provider_specs",
]


