"""Valid image fixtures for reference upload tests."""

import io

from PIL import Image


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    return buffer.getvalue()


def mp3_bytes() -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64" + (b"\x00" * 32)


def wav_bytes() -> bytes:
    return (
        b"RIFF$\x00\x00\x00WAVEfmt "
        b"\x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00"
        b"data\x00\x00\x00\x00"
    )

