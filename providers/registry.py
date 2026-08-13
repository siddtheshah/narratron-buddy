"""Image provider catalog shared by production integration and Test Lab."""

from __future__ import annotations

import os
from typing import Any

from providers.gemini_image_provider import GeminiImageProvider
from providers.fal_flux_klein_provider import FalFluxKleinProvider
from providers.hybrid_image_provider import HybridImageProvider
from providers.image_provider import ImageProvider, ImageProviderError
from providers.openai_image_provider import OpenAIImageProvider
from providers.lyria_music_provider import LyriaMusicProvider
from providers.music_provider import MusicProvider, MusicProviderError
from providers.gemini_text_response_provider import GeminiTextResponseProvider
from providers.text_response_provider import TextResponseProvider, TextResponseProviderError



_IMAGE_SPECS = (
    {
        "id": "openai-gpt-image",
        "name": "GPT Image 1 Mini",
        "model": "gpt-image-1-mini",
        "model_options": ["gpt-image-1-mini", "gpt-image-2"],
        "quality_options": ["low", "medium", "high"],
        "estimated_cost_usd_1mp": 0.015,
        "reference_limit": None,
        "status": "available" if (os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_API_KEY")) else "unconfigured",
        "notes": "Medium 1536×1024 estimate; references use the image-edit endpoint.",
    },
    {
        "id": "gemini",
        "name": "Gemini 3.1 Flash-Lite Image",
        "model": "gemini-3.1-flash-lite-image",
        "estimated_cost_usd_1mp": 0.0336,
        "reference_limit": None,
        "status": "unconfigured",
        "notes": "Gemini Developer API baseline. Cost estimate excludes input tokens.",
    },
    {
        "id": "flux-klein",
        "name": "FLUX.2 Klein 9B (FAL)",
        "model": "fal-ai/flux-2/klein/9b",
        "estimated_cost_usd_1mp": 0.006,
        "reference_limit": 4,
        "status": "unconfigured",
        "notes": "Higher-capability Klein endpoint. $0.006/MP text-to-image; use the FAL edit endpoint for references."
    },
    {
        "id": "hybrid-flux-gemini",
        "name": "FLUX Klein + Gemini router",
        "model": "FLUX.2 Klein 9B / Gemini 3.1 Flash-Lite Image",
        "classifier_model_options": ["gemini-2.5-flash-lite", "gemini-2.5-flash"],
        "estimated_cost_usd_1mp": None,
        "reference_limit": 4,
        "status": "unconfigured",
        "notes": "FLUX is used only for pure environments and simple single-character scenes. Gemini handles reference-guided, multi-subject, and complex scenes.",
    },
    {
        "id": "qwen-image-2",
        "name": "Qwen Image 2.0",
        "model": "qwen-image-2.0",
        "estimated_cost_usd_1mp": None,
        "reference_limit": 3,
        "status": "planned",
        "notes": "Adapter pending DashScope credentials and confirmed regional pricing.",
    },
)


_MUSIC_SPECS = (
    {
        "id": "lyria",
        "name": "Google Lyria 3 Pro Preview",
        "model": "lyria-3-pro-preview",
        "model_options": ["lyria-3-pro-preview", "lyria-3-pro", "lyria-3"],
        "estimated_cost_usd_per_generation": 0.080,
        "estimated_cost_usd_30s": 0.080,
        "status": "unconfigured",
        "notes": "Google DeepMind Lyria 3 Pro Preview. Generates multiple minutes of audio at $0.08 per generation.",
    },
    {
        "id": "seedance",
        "name": "Seedance Music 1.0",
        "model": "seedance-1.0",
        "estimated_cost_usd_30s": 0.050,
        "status": "planned",
        "notes": "High-fidelity dynamic music generation provider.",
    },
)


_TEXT_RESPONSE_SPECS = (
    {
        "id": "gemini-2-5",
        "name": "Gemini 2.5 Flash-Lite Text",
        "model": "gemini-2.5-flash-lite",
        "model_options": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"],
        "status": "unconfigured",
        "notes": "Fast, cost-effective baseline text response provider.",
    },
    {
        "id": "gemini-3",
        "name": "Gemini 3 Flash Text",
        "model": "gemini-3.6-flash",
        "model_options": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.0-flash", "gemini-3.1-flash-lite"],
        "status": "unconfigured",
        "notes": "Next-generation high-capability text generation model for comparison.",
    },
)



def list_image_provider_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _IMAGE_SPECS]
    for spec in specs:
        if spec["id"] == "openai-gpt-image":
            spec["status"] = "available" if (os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_API_KEY")) else "unconfigured"
        if spec["id"] == "gemini":
            spec["status"] = "available" if os.getenv("GEMINI_API_KEY") else "unconfigured"
        if spec["id"] == "flux-klein":
            spec["status"] = "available" if (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")) else "unconfigured"
        if spec["id"] == "hybrid-flux-gemini":
            spec["status"] = "available" if (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")) and os.getenv("GEMINI_API_KEY") else "unconfigured"
    return specs


def get_image_provider(provider_id: str, options: dict[str, Any] | None = None) -> ImageProvider:
    options = options or {}
    if provider_id == "gemini":
        return GeminiImageProvider(model=str(options.get("model") or "gemini-3.1-flash-lite-image"))
    if provider_id == "openai-gpt-image":
        return OpenAIImageProvider(
            model=str(options.get("model") or "gpt-image-1-mini"),
            quality=str(options.get("quality") or "medium"),
        )
    if provider_id == "flux-klein":
        return FalFluxKleinProvider()
    if provider_id == "hybrid-flux-gemini":
        return HybridImageProvider(
            primary=FalFluxKleinProvider(),
            fallback=GeminiImageProvider(),
            classifier_model=str(options.get("classifier_model") or "gemini-2.5-flash-lite"),
        )
    spec = next((item for item in _IMAGE_SPECS if item["id"] == provider_id), None)
    if spec:
        raise ImageProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise ImageProviderError(f"Unknown image provider: {provider_id}")


def list_music_provider_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _MUSIC_SPECS]
    has_gemini = bool(os.getenv("LYRIA_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    for spec in specs:
        if spec["id"] == "lyria":
            spec["status"] = "available" if has_gemini else "unconfigured"
    return specs


def get_music_provider(provider_id: str, options: dict[str, Any] | None = None) -> MusicProvider:
    options = options or {}
    if provider_id == "lyria":
        return LyriaMusicProvider(model=str(options.get("model") or "lyria-3-pro-preview"))
    spec = next((item for item in _MUSIC_SPECS if item["id"] == provider_id), None)
    if spec:
        raise MusicProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise MusicProviderError(f"Unknown music provider: {provider_id}")

def list_text_response_provider_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _TEXT_RESPONSE_SPECS]
    has_vertex = bool(
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_PROJECT_ID")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    for spec in specs:
        if spec["id"] in ("gemini-2-5", "gemini-3"):
            spec["status"] = "available" if has_vertex else "unconfigured"
    return specs



def get_text_response_provider(provider_id: str, options: dict[str, Any] | None = None) -> TextResponseProvider:
    options = options or {}
    if provider_id in ("gemini-2-5", "gemini-2.5", "gemini-text", "Gemini 2.5"):
        provider = GeminiTextResponseProvider(model=str(options.get("model") or "gemini-2.5-flash-lite"))
        provider.id = "gemini-2-5"
        return provider
    if provider_id in ("gemini-3", "gemini-3-flash", "Gemini 3"):
        provider = GeminiTextResponseProvider(model=str(options.get("model") or "gemini-3.6-flash"))
        provider.id = "gemini-3"
        provider.display_name = "Gemini 3 Flash Text"
        return provider

    spec = next((item for item in _TEXT_RESPONSE_SPECS if item["id"] == provider_id), None)
    if spec:
        raise TextResponseProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise TextResponseProviderError(f"Unknown text response provider: {provider_id}")





