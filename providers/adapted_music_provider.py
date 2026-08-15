"""Test Lab-only composition used to generate base/adapted comparison pairs.

This is intentionally not the production music-variant workflow. Production
callers should retain an existing track and pass its bytes to ``MusicAdapter``
directly, avoiding a new base generation before every adaptation.
"""

from __future__ import annotations

import time

from providers.music_provider import (
    MusicAdaptationRequest,
    MusicAudioArtifact,
    MusicAdapter,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
)


class AdaptedMusicProvider(MusicProvider):
    """TEST ONLY: generate a base track, then adapt it for an A/B benchmark.

    The class exists only to make Test Lab comparisons convenient. Do not use it
    for music variants in the application: use a ``MusicAdapter`` with the
    previously generated or stored source track instead.
    """

    id = "test-base-plus-adapter"
    display_name = "TEST ONLY: Generate base + audio adapter"

    def __init__(self, base: MusicProvider, adapter: MusicAdapter) -> None:
        self.base = base
        self.adapter = adapter
        self.model = f"{base.model} -> {adapter.model}"

    def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult:
        self._progress(request, "base_generating", {"provider": self.base.id, "model": self.base.model})
        base_started = time.perf_counter()
        base_result = self.base.generate(request)
        base_latency_ms = round((time.perf_counter() - base_started) * 1000, 1)
        self._progress(request, "base_completed", {"provider": base_result.provider, "latency_ms": base_latency_ms})
        self._progress(request, "adapter_generating", {"provider": self.adapter.id, "model": self.adapter.model})
        adapter_started = time.perf_counter()
        adapted = self.adapter.adapt(
            MusicAdaptationRequest(
                source_audio=base_result.audio_bytes,
                source_mime_type=base_result.mime_type,
                prompt=request.prompt,
                duration_seconds=request.duration_seconds,
            )
        )
        adapter_latency_ms = round((time.perf_counter() - adapter_started) * 1000, 1)
        self._progress(request, "adapter_completed", {"provider": adapted.provider, "latency_ms": adapter_latency_ms})
        return MusicGenerationResult(
            audio_bytes=adapted.audio_bytes,
            mime_type=adapted.mime_type,
            provider=self.id,
            model=self.model,
            request_id=adapted.request_id,
            usage={
                "base": {"provider": base_result.provider, "model": base_result.model, "request_id": base_result.request_id, "usage": dict(base_result.usage)},
                "adapter": {"provider": adapted.provider, "model": adapted.model, "request_id": adapted.request_id, "usage": dict(adapted.usage)},
                "timings": {"base_latency_ms": base_latency_ms, "adapter_latency_ms": adapter_latency_ms},
            },
            artifacts={
                "base": MusicAudioArtifact(
                    audio_bytes=base_result.audio_bytes,
                    mime_type=base_result.mime_type,
                    provider=base_result.provider,
                    model=base_result.model,
                    request_id=base_result.request_id,
                )
            },
        )

    @staticmethod
    def _progress(request: MusicGenerationRequest, stage: str, details: dict[str, object]) -> None:
        if request.on_progress:
            request.on_progress(stage, details)
