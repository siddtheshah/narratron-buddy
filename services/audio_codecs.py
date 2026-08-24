"""Audio decoding utilities for real-time live agent streaming."""

from __future__ import annotations

import logging
from typing import Optional

import av

logger = logging.getLogger(__name__)


class LiveAudioDecoder:
    """Decodes incoming Opus packets into 16 kHz 16-bit mono linear PCM."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._codec = av.CodecContext.create("opus", "r")
        self._codec.sample_rate = sample_rate
        self._codec.layout = "mono" if channels == 1 else "stereo"
        self._codec.open()
        self._resampler = av.AudioResampler(
            format="s16",
            layout="mono" if channels == 1 else "stereo",
            rate=sample_rate,
        )

    def decode(self, opus_bytes: bytes) -> bytes:
        """Decode a raw Opus frame/packet into 16-bit linear PCM bytes."""
        if not opus_bytes:
            return b""
        try:
            packet = av.Packet(opus_bytes)
            frames = self._codec.decode(packet)
            if not frames:
                return b""
            pcm_chunks = []
            for frame in frames:
                resampled = self._resampler.resample(frame)
                for r_frame in resampled:
                    pcm_chunks.append(bytes(r_frame.planes[0]))
            return b"".join(pcm_chunks)
        except Exception as exc:
            logger.warning("[LiveAudioDecoder] Failed to decode audio packet: %s", exc)
            return b""
