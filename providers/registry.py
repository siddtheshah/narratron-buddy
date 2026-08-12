"""Image provider catalog shared by production integration and Test Lab."""

from __future__ import annotations

import os
from typing import Any

from providers.gemini_image_provider import GeminiImageProvider
from providers.fal_flux_klein_provider import FalFluxKleinProvider
from providers.hybrid_image_provider import HybridImageProvider
from providers.image_provider import ImageProvider, ImageProviderError
from providers.openai_image_provider import OpenAIImageProvider


_SPECS = (
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
        "notes": "Gemini routes typography, creature interactions, and context-sensitive word meanings to Gemini; ordinary scenes use Klein.",
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


def list_image_provider_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _SPECS]
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
    spec = next((item for item in _SPECS if item["id"] == provider_id), None)
    if spec:
        raise ImageProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise ImageProviderError(f"Unknown image provider: {provider_id}")
