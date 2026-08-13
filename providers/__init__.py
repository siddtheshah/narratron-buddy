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
from providers.hybrid_image_provider import HybridImageProvider
from providers.music_provider import (
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
    MusicProviderError,
)
from providers.lyria_music_provider import LyriaMusicProvider
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
    TextResponseResult,
)
from providers.gemini_text_response_provider import GeminiTextResponseProvider
from providers.registry import (
    get_image_provider,
    get_music_provider,
    get_text_response_provider,
    list_image_provider_specs,
    list_music_provider_specs,
    list_text_response_provider_specs,
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
    "MusicGenerationRequest",
    "MusicGenerationResult",
    "MusicProvider",
    "MusicProviderError",
    "LyriaMusicProvider",
    "TextResponseRequest",
    "TextResponseResult",
    "TextResponseProvider",
    "TextResponseProviderError",
    "GeminiTextResponseProvider",
    "get_image_provider",
    "get_music_provider",
    "get_text_response_provider",
    "list_image_provider_specs",
    "list_music_provider_specs",
    "list_text_response_provider_specs",
]


