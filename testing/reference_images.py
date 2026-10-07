"""Valid image fixtures for reference upload tests."""

import io

from PIL import Image


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    return buffer.getvalue()
