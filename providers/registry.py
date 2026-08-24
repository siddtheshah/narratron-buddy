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
from providers.fal_stable_audio_adapter import FalStableAudioAdapter
from providers.adapted_music_provider import AdaptedMusicProvider
from providers.music_provider import MusicAdapter, MusicProvider, MusicProviderError
from providers.gemini_text_response_provider import GeminiTextResponseProvider
from providers.text_response_provider import TextResponseProvider, TextResponseProviderError
from providers.speech_provider import SpeechProvider, SpeechProviderError
from providers.gemini_speech_provider import GeminiSpeechProvider
from providers.fal_seed_speech_provider import FalSeedSpeechProvider
from providers.google_chirp_speech_provider import GoogleChirpSpeechProvider



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
    {
        "id": "test-base-plus-adapter",
        "name": "TEST ONLY: Generate base + audio adapter",
        "model": "Select a base provider and audio adapter",
        "estimated_cost_usd_30s": None,
        "status": "unconfigured",
        "notes": "Test Lab A/B fixture: generates a source track, then adapts it. Production variants should adapt an existing stored track.",
    },
)


_MUSIC_ADAPTER_SPECS = (
    {
        "id": "fal-stable-audio-3-base-a2a",
        "name": "Stable Audio 3 Small Music Base A2A (FAL)",
        "model": "fal-ai/stable-audio-3/small/music/base/audio-to-audio",
        "estimated_cost_usd_per_generation": 0.032,
        "status": "unconfigured",
        "notes": "Base checkpoint; use a lower noise level to preserve the source track. FAL lists $0.032/audio.",
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
        "model": "gemini-3.7-flash",
        "model_options": ["gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.0-flash", "gemini-3.1-flash-lite"],
        "status": "unconfigured",
        "notes": "Next-generation high-capability text generation model for comparison.",
    },
)

_SPEECH_SPECS = (
    {
        "id": "gemini-flash-tts",
        "name": "Gemini 3.1 Flash TTS",
        "model": "gemini-3.1-flash-tts-preview",
        "model_options": ["gemini-3.1-flash-tts-preview"],
        "voice": "Kore",
        "estimated_cost_usd_1k_chars": 0.15,
        "status": "unconfigured",
        "notes": "Gemini preview TTS. Returns 24 kHz PCM, normalized to WAV for browser playback.",
    },
    {
        "id": "fal-seed-speech",
        "name": "ByteDance Seed Speech v2 (FAL)",
        "model": "fal-ai/bytedance/seed-speech/tts/v2",
        "voice": "stokie_en",
        "estimated_cost_usd_1k_chars": 0.03,
        "status": "unconfigured",
        "notes": "ByteDance's TTS model on FAL. Seedance is a video model family; Seed Speech is the corresponding TTS endpoint.",
    },
    {
        "id": "google-chirp-3-hd",
        "name": "Google Cloud Chirp 3: HD",
        "model": "en-US-Chirp3-HD-Charon",
        "voice": "en-US-Chirp3-HD-Charon",
        "estimated_cost_usd_1k_chars": 0.03,
        "status": "unconfigured",
        "notes": "Google Cloud Text-to-Speech Chirp 3: HD. Uses Application Default Credentials and the Cloud Text-to-Speech API.",
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
    has_base_provider = any(spec["id"] != "test-base-plus-adapter" and spec["status"] == "available" for spec in specs)
    for spec in specs:
        if spec["id"] == "test-base-plus-adapter":
            spec["status"] = "available" if has_base_provider and bool(os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")) else "unconfigured"
    return specs


def list_music_adapter_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _MUSIC_ADAPTER_SPECS]
    for spec in specs:
        if spec["id"] == "fal-stable-audio-3-base-a2a":
            spec["status"] = "available" if (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")) else "unconfigured"
    return specs


def get_music_adapter(adapter_id: str, options: dict[str, Any] | None = None) -> MusicAdapter:
    options = options or {}
    if adapter_id == "fal-stable-audio-3-base-a2a":
        return FalStableAudioAdapter(
            init_noise_level=float(options.get("init_noise_level", 0.35)),
            num_inference_steps=int(options.get("num_inference_steps", 50)),
            guidance_scale=float(options.get("guidance_scale", 7.0)),
            output_format=str(options.get("output_format", "mp3")),
            bitrate=str(options.get("bitrate", "192k")),
        )
    spec = next((item for item in _MUSIC_ADAPTER_SPECS if item["id"] == adapter_id), None)
    if spec:
        raise MusicProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise MusicProviderError(f"Unknown music adapter: {adapter_id}")


def get_music_provider(provider_id: str, options: dict[str, Any] | None = None) -> MusicProvider:
    options = options or {}
    if provider_id == "lyria":
        return LyriaMusicProvider(model=str(options.get("model") or "lyria-3-pro-preview"))
    if provider_id == "test-base-plus-adapter":
        base_provider_id = str(options.get("base_provider") or "lyria")
        adapter_id = str(options.get("adapter") or "fal-stable-audio-3-base-a2a")
        if base_provider_id == provider_id:
            raise MusicProviderError("The Test Lab base-plus-adapter fixture cannot use itself as its base provider.")
        return AdaptedMusicProvider(
            base=get_music_provider(base_provider_id, dict(options.get("base_options") or {})),
            adapter=get_music_adapter(adapter_id, dict(options.get("adapter_options") or {})),
        )
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


def list_speech_provider_specs() -> list[dict[str, Any]]:
    specs = [dict(spec) for spec in _SPEECH_SPECS]
    for spec in specs:
        if spec["id"] == "gemini-flash-tts":
            spec["status"] = "available" if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) else "unconfigured"
        if spec["id"] == "fal-seed-speech":
            spec["status"] = "available" if (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")) else "unconfigured"
        if spec["id"] == "google-chirp-3-hd":
            spec["status"] = "available" if (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")) else "unconfigured"
    return specs


def get_speech_provider(provider_id: str, options: dict[str, Any] | None = None) -> SpeechProvider:
    options = options or {}
    if provider_id == "gemini-flash-tts":
        return GeminiSpeechProvider(model=str(options.get("model") or "gemini-3.1-flash-tts-preview"))
    if provider_id == "fal-seed-speech":
        return FalSeedSpeechProvider(
            output_format=str(options.get("output_format") or "mp3"),
            sample_rate_hz=int(options.get("sample_rate_hz") or 24_000),
        )
    if provider_id == "google-chirp-3-hd":
        return GoogleChirpSpeechProvider(model=str(options.get("model") or "en-US-Chirp3-HD-Charon"))
    spec = next((item for item in _SPEECH_SPECS if item["id"] == provider_id), None)
    if spec:
        raise SpeechProviderError(f"{spec['name']} is listed for comparison but its adapter is not configured yet.")
    raise SpeechProviderError(f"Unknown speech provider: {provider_id}")





