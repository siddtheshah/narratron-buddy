"""Provider-neutral image and music generation integrations."""

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
from providers.registry import (
    get_image_provider,
    get_music_provider,
    list_image_provider_specs,
    list_music_provider_specs,
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
    "get_image_provider",
    "get_music_provider",
    "list_image_provider_specs",
    "list_music_provider_specs",
]

