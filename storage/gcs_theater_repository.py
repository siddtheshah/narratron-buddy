"""Theater persistence through GCS object APIs."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
from pathlib import Path
from typing import Callable

from google.cloud import storage

from components.theater_package import safe_asset_path
from storage.theater_repository import TheaterRepository

logger = logging.getLogger(__name__)


class GCSTheaterRepository(TheaterRepository):
    """Save changed objects and copy catalog assets within an injected bucket."""

    def __init__(
        self, base_dir: Path | str, bucket: storage.Bucket,
        storage_changed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(base_dir, storage_changed)
        self._bucket = bucket

    def _export_theater(self, theater_id: str, source_dir: Path) -> bool:
        return self._save(theater_id, source_dir)

    def export_catalog_theater(
        self, theater_id: str, source_dir: Path, catalog_assets: dict[str, storage.Blob],
    ) -> bool:
        return self._save(theater_id, source_dir, catalog_assets=catalog_assets)

    def _save(
        self, theater_id: str, source_dir: Path, *, catalog_assets: dict[str, storage.Blob] | None = None,
    ) -> bool:
        """Save changed files through GCS, without reading saved asset bodies.

        Content matching handles normalized package paths and user overrides.
        Generation preconditions prevent a concurrent catalog update from
        silently changing the saved bytes. Publish theater.json after all assets
        succeed. Catalog copies are only used for newly allocated theater IDs.
        """
        target = self.theater_path(theater_id)
        prefix = f"theaters/{theater_id}/"
        saved_blobs: list[storage.Blob] = []
        existing: dict[str, storage.Blob] = {}
        bucket = self._bucket

        def save_file(path: Path) -> str:
            relative = path.relative_to(source_dir).as_posix()
            destination = prefix + relative
            checksum = base64.b64encode(hashlib.md5(path.read_bytes()).digest()).decode("ascii")
            previous = existing.get(relative)
            if previous is not None and previous.md5_hash == checksum:
                return "unchanged"
            source = catalog_assets.get(checksum) if catalog_assets is not None else None
            if source is not None:
                copied = source.bucket.copy_blob(
                    source, bucket, destination,
                    source_generation=source.generation,
                    if_source_generation_match=source.generation,
                    if_generation_match=0,
                )
                saved_blobs.append(copied)
                return "copied"
            else:
                uploaded = bucket.blob(destination)
                uploaded.upload_from_filename(
                    str(path), if_generation_match=previous.generation if previous is not None else 0,
                )
                saved_blobs.append(uploaded)
                return "uploaded"

        try:
            if catalog_assets is None:
                existing = {blob.name.removeprefix(prefix): blob for blob in bucket.list_blobs(prefix=prefix)
                            if not blob.name.endswith("/")}
            files = [path for path in source_dir.rglob("*")
                     if path.is_file() and path.relative_to(source_dir).as_posix()
                     not in {"editor.json", "editor.tmp", "theater.json"}]
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(save_file, files))
            for relative, blob in existing.items():
                try:
                    source_file = safe_asset_path(source_dir, relative)
                except ValueError:
                    continue
                if not source_file.is_file():
                    blob.delete(if_generation_match=blob.generation)
            results.append(save_file(source_dir / "theater.json"))
            logger.info(
                "Saved theater %s directly to GCS: %d server-side copies, %d uploads, %d unchanged files",
                theater_id, results.count("copied"), results.count("uploaded"), results.count("unchanged"),
            )
            return True
        except Exception:
            logger.exception("Failed to save theater %s to %s", theater_id, target)
            for blob in saved_blobs if catalog_assets is not None else []:
                try:
                    blob.delete(if_generation_match=blob.generation)
                except Exception:
                    logger.exception("Failed to clean up partial catalog save: %s", blob.name)
            return False
        finally:
            self._notify_storage_changed(theater_id)

