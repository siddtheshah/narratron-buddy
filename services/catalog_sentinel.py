"""Cache-key helpers for local content directories and GCS catalog sentinel objects."""

from __future__ import annotations

from pathlib import Path
from google.cloud import storage

CATALOG_SENTINEL_FILENAME = ".catalog_sentinel.json"

CatalogCacheKey = tuple[tuple[str, int, int], ...] | tuple[str, int | None]


def sentinel_blob_name(root_prefix: str) -> str:
    """Return the GCS object path for a collection's sentinel file."""
    stripped = root_prefix.strip("/")
    return f"{stripped}/{CATALOG_SENTINEL_FILENAME}" if stripped else CATALOG_SENTINEL_FILENAME


def local_directory_cache_key(root: Path) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint package paths and file stats without reading asset contents."""
    if not root.exists():
        return ()
    entries: list[tuple[str, int, int]] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        for path in [folder, *folder.rglob("*")]:
            stat = path.stat()
            entries.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(entries))


def gcs_sentinel_cache_key(
    client: storage.Client,
    bucket_name: str,
    root_prefix: str,
) -> tuple[str, int | None]:
    """Check only the single GCS sentinel blob's generation."""
    blob = client.bucket(bucket_name).get_blob(sentinel_blob_name(root_prefix))
    return ("gcs", blob.generation if blob is not None else None)
