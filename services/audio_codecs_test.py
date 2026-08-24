import av
import pytest

from services.audio_codecs import LiveAudioDecoder


def _generate_test_opus_packet() -> bytes:
    encoder = av.CodecContext.create("opus", "w")
    encoder.sample_rate = 16000
    encoder.layout = "mono"
    encoder.format = av.AudioFormat("s16")
    encoder.bit_rate = 24000
    encoder.open()

    raw_pcm = b"\x00\x00" * 480  # 480 samples of silence = 960 bytes
    frame = av.AudioFrame(format="s16", layout="mono", samples=480)
    frame.sample_rate = 16000
    frame.planes[0].update(raw_pcm)

    packets = encoder.encode(frame)
    assert packets, "Opus encoder should yield at least one packet"
    return bytes(packets[0])


def test_live_audio_decoder_decodes_opus_packet():
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    opus_packet = _generate_test_opus_packet()
    assert len(opus_packet) < 100  # Highly compressed

    pcm_bytes = decoder.decode(opus_packet)
    assert len(pcm_bytes) > 0
    # Must be 16-bit samples (multiple of 2 bytes)
    assert len(pcm_bytes) % 2 == 0


def test_live_audio_decoder_empty_input():
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    assert decoder.decode(b"") == b""
    assert decoder.decode(None) == b""


def test_live_audio_decoder_handles_corrupt_packet():
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    corrupt_packet = b"\xff\xff\x00\x12\x34\x56\x78\x9a"
    result = decoder.decode(corrupt_packet)
    assert isinstance(result, bytes)
