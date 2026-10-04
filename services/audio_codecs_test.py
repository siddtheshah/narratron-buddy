from unittest.mock import MagicMock

import av

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


def test_live_audio_decoder_decodes_opus_packet() -> None:
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    opus_packet = _generate_test_opus_packet()
    assert len(opus_packet) < 100  # Highly compressed

    pcm_bytes = decoder.decode(opus_packet)
    assert len(pcm_bytes) > 0
    # Must be 16-bit samples (multiple of 2 bytes)
    assert len(pcm_bytes) % 2 == 0


def test_live_audio_decoder_empty_input() -> None:
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    assert decoder.decode(b"") == b""


def test_live_audio_decoder_handles_corrupt_packet() -> None:
    decoder = LiveAudioDecoder(sample_rate=16000, channels=1)
    corrupt_packet = b"\xff\xff\x00\x12\x34\x56\x78\x9a"
    result = decoder.decode(corrupt_packet)
    assert result == b""


def test_live_audio_decoder_excludes_nonzero_plane_padding() -> None:
    decoder = LiveAudioDecoder()
    decoded_frame = decoder._codec.decode(av.Packet(_generate_test_opus_packet()))[0]
    frame = decoder._resampler.resample(decoded_frame)[0]
    valid_pcm = b"\x12\x34" * frame.samples
    padding_bytes = frame.planes[0].buffer_size - len(valid_pcm)
    assert padding_bytes > 0
    frame.planes[0].update(valid_pcm + b"\xff" * padding_bytes)
    decoder._codec = MagicMock()
    decoder._codec.decode.return_value = [frame]
    decoder._resampler = MagicMock()
    decoder._resampler.resample.return_value = [frame]

    assert decoder.decode(b"encoded-packet") == valid_pcm


def test_live_audio_decoder_matches_valid_opus_samples_across_packets() -> None:
    decoder = LiveAudioDecoder()
    reference_codec = av.CodecContext.create("opus", "r")
    reference_codec.sample_rate = 16000
    reference_codec.layout = "mono"
    reference_codec.open()
    reference_resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    packet = _generate_test_opus_packet()
    for _ in range(10):
        expected = b"".join(
            pcm.to_ndarray().tobytes()
            for frame in reference_codec.decode(av.Packet(packet))
            for pcm in reference_resampler.resample(frame)
        )
        assert decoder.decode(packet) == expected
