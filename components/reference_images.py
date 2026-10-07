"""Validate raster references before accepting or displaying uploaded bytes."""

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError


REFERENCE_FORMATS = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".webp": ("WEBP", "image/webp"),
    ".gif": ("GIF", "image/gif"),
}


def reference_image_type(filename: str, content: bytes) -> str:
    """Require a supported extension and matching, structurally valid image."""
    expected = REFERENCE_FORMATS.get(Path(filename).suffix.lower())
    if expected is None:
        raise ValueError("References must be PNG, JPEG, WebP, or GIF images.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != expected[0]:
                raise ValueError("Reference image content does not match its extension.")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError("Reference must contain a valid raster image.") from error
    return expected[1]
