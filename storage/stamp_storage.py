"""Storage repository for user stamps.

Supports local storage for development/testing and Google Cloud Storage (GCS)
for production deployment.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
import re
import secrets
from typing import Optional, Tuple

from absl import flags
from PIL import Image
from google.cloud import storage

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUCKET = "narratron-buddy-app-storage"
MAX_STAMPS_PER_USER: int = 10
MAX_STAMP_BYTES: int = 5 * 1024 * 1024
MAX_STAMP_DIMENSION: int = 256

logger = logging.getLogger(__name__)


def get_stamps_root() -> Path:
    """Return the stamps root directory for the current runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return PROJECT_ROOT / "stamps"
    return Path("/mnt/storage/stamps")


def ensure_stamps_root() -> Path:
    """Return and create the stamps root directory."""
    stamps_root = get_stamps_root().resolve()
    stamps_root.mkdir(parents=True, exist_ok=True)
    return stamps_root


def process_stamp_image(
    image_bytes: bytes,
    max_dimension: int = MAX_STAMP_DIMENSION,
) -> Tuple[bytes, str]:
    """Validate, downscale, and optimize a user stamp image.

    Stamps are small, relatively low-res images constrained to a maximum
    dimension (default 256x256) while preserving aspect ratio.
    """
    if not image_bytes:
        raise ValueError("Image bytes cannot be empty.")
    if len(image_bytes) > MAX_STAMP_BYTES:
        raise ValueError(f"Stamp image size exceeds limit of {MAX_STAMP_BYTES} bytes.")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image_format = image.format
    except Exception as exc:
        raise ValueError("Invalid image file.") from exc

    if image_format not in {"PNG", "JPEG", "WEBP", "GIF"}:
        raise ValueError(
            f"Unsupported image format: {image_format}. Allowed formats: PNG, JPEG, WEBP, GIF."
        )

    width, height = image.size
    if width > max_dimension or height > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    if image_format == "JPEG":
        if image.mode in {"RGBA", "P"}:
            image = image.convert("RGB")
        image.save(output, format="JPEG", quality=85, optimize=True)
        content_type = "image/jpeg"
    elif image_format == "WEBP":
        image.save(output, format="WEBP", quality=85)
        content_type = "image/webp"
    elif image_format == "GIF":
        image.save(output, format="GIF")
        content_type = "image/gif"
    else:
        image.save(output, format="PNG", optimize=True)
        content_type = "image/png"

    return output.getvalue(), content_type


class StampStorage:
    """Storage manager for user stamps across filesystem and GCS."""

    def __init__(
        self,
        base_dir: Optional[Path | str] = None,
        bucket_name: Optional[str] = None,
    ) -> None:
        if base_dir is not None:
            self._base_dir: Path = Path(base_dir).resolve()
        else:
            self._base_dir = ensure_stamps_root()
        self._base_dir.mkdir(parents=True, exist_ok=True)

        self._bucket_name: str = bucket_name or os.getenv("GCS_STAMPS_BUCKET", DEFAULT_BUCKET)
        self._gcs_client: Optional[storage.Client] = None
        self._init_gcs_client()

    def _init_gcs_client(self) -> None:
        """Initialize GCS client if not running in local test mode."""
        if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
            return
        try:
            self._gcs_client = storage.Client()
        except Exception as exc:
            logger.info("Direct GCS client not available for stamps storage: %s", exc)
            self._gcs_client = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save_stamp(
        self,
        user_id: int,
        filename: str,
        image_bytes: bytes,
        content_type: str,
    ) -> str:
        """Save a stamp image locally and to GCS, returning just the stored filename."""
        clean_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", filename).lstrip(".")
        if not clean_name:
            clean_name = "stamp.png"
        unique_prefix = secrets.token_hex(4)
        safe_filename = f"{unique_prefix}_{clean_name}"
        rel_gcs_path = f"stamps/{user_id}/{safe_filename}"

        # Write to filesystem directory
        user_folder = self._base_dir / str(user_id)
        user_folder.mkdir(parents=True, exist_ok=True)
        file_path = user_folder / safe_filename
        file_path.write_bytes(image_bytes)

        # Upload to GCS bucket if client is available
        if self._gcs_client is not None:
            bucket = self._gcs_client.bucket(self._bucket_name)
            blob = bucket.blob(rel_gcs_path)
            blob.upload_from_string(image_bytes, content_type=content_type)

        return safe_filename

    def delete_stamp(self, user_id: int, filename: str) -> bool:
        """Delete a stamp image from local storage and GCS."""
        clean_name = Path(filename).name
        local_file = self._base_dir / str(user_id) / clean_name
        if local_file.is_file():
            local_file.unlink(missing_ok=True)

        if self._gcs_client is not None:
            gcs_path = f"stamps/{user_id}/{clean_name}"
            bucket = self._gcs_client.bucket(self._bucket_name)
            blob = bucket.blob(gcs_path)
            if blob.exists():
                blob.delete()

        return True

    def read_stamp(self, user_id: int, filename: str) -> Optional[bytes]:
        """Read stamp image bytes from local storage or GCS."""
        clean_name = Path(filename).name
        local_file = self._base_dir / str(user_id) / clean_name
        if local_file.is_file():
            return local_file.read_bytes()

        if self._gcs_client is not None:
            gcs_path = f"stamps/{user_id}/{clean_name}"
            bucket = self._gcs_client.bucket(self._bucket_name)
            blob = bucket.blob(gcs_path)
            if blob.exists():
                return blob.download_as_bytes()

        return None
