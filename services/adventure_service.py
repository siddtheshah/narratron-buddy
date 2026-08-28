"""Service for managing premade adventures stored in Google Cloud Storage."""

from datetime import datetime, timezone
import json
import logging
import mimetypes
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import yaml

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Ensure mime types are registered
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")

DEFAULT_BUCKET_NAME = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "adventures"
CACHE_TTL_SECONDS = 60.0


class AdventureService:
    """Provides access to premade adventure packages from GCS or local disk fallback."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None,
        storage_client: Optional[Any] = None,
        local_fallback_dir: Optional[Path] = None,
    ):
        load_dotenv()
        self.bucket_name = (
            bucket_name
            or os.getenv("GCS_ADVENTURES_BUCKET")
            or DEFAULT_BUCKET_NAME
        )
        self.prefix = (
            prefix
            if prefix is not None
            else os.getenv("GCS_ADVENTURES_PREFIX", DEFAULT_PREFIX)
        ).strip("/")
        self._storage_client = storage_client
        self._bucket = None
        self._local_fallback_dir = local_fallback_dir or Path("adventures")

        self._adventures_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: float = 0.0

    def _get_bucket(self):
        """Lazy-initialize and return GCS bucket object if accessible."""
        if self._bucket is not None:
            return self._bucket

        try:
            if self._storage_client is None:
                from google.cloud import storage
                project = os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")
                self._storage_client = storage.Client(project=project)
            self._bucket = self._storage_client.bucket(self.bucket_name)
            return self._bucket
        except Exception as e:
            logger.warning("Could not initialize GCS storage client for adventures: %s", e)
            return None

    def invalidate_cache(self) -> None:
        """Clear cached adventure listings."""
        self._adventures_cache = None
        self._cache_timestamp = 0.0

    def list_adventures(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """List all premade adventures sorted by newest first (created_at desc)."""
        now = time.time()
        if (
            not force_refresh
            and self._adventures_cache is not None
            and (now - self._cache_timestamp) < CACHE_TTL_SECONDS
        ):
            return self._adventures_cache

        adventures = self._fetch_adventures_from_gcs()
        if not adventures and self._local_fallback_dir.exists():
            adventures = self._fetch_adventures_from_local()

        # Sort newest first by created_at ISO timestamp (defaulting to empty string)
        adventures.sort(
            key=lambda a: a.get("created_at") or "",
            reverse=True,
        )

        self._adventures_cache = adventures
        self._cache_timestamp = now
        return adventures

    def get_adventure(self, adventure_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve metadata for a specific adventure by ID."""
        for adv in self.list_adventures():
            if adv.get("id") == adventure_id:
                return adv
        return None

    def _fetch_adventures_from_gcs(self) -> List[Dict[str, Any]]:
        """Scan GCS bucket prefix for adventure packages and their metadata."""
        bucket = self._get_bucket()
        if not bucket:
            return []

        try:
            prefix = f"{self.prefix}/" if self.prefix else ""
            blobs = list(bucket.list_blobs(prefix=prefix))
            if not blobs:
                return []

            # Group blobs by adventure folder name: adventures/<slug>/...
            adv_blobs: Dict[str, List[Any]] = {}
            for b in blobs:
                rel = b.name[len(prefix):] if b.name.startswith(prefix) else b.name
                parts = [p for p in rel.split("/") if p]
                if not parts:
                    continue
                adv_id = parts[0]
                if adv_id not in adv_blobs:
                    adv_blobs[adv_id] = []
                adv_blobs[adv_id].append((b, "/".join(parts[1:])))

            results = []
            for adv_id, items in adv_blobs.items():
                metadata_blob = next((b for b, path in items if path == "metadata.json"), None)
                yaml_blob = next((b for b, path in items if path in ("theater.yaml", "theater.yml")), None)
                meta: Dict[str, Any] = {}

                if metadata_blob:
                    try:
                        raw = metadata_blob.download_as_bytes()
                        meta = json.loads(raw.decode("utf-8"))
                    except Exception as e:
                        logger.warning("Failed to decode metadata.json for adventure %s: %s", adv_id, e)

                if not meta:
                    meta = {
                        "id": adv_id,
                        "title": adv_id.replace("-", " ").title(),
                        "description": f"Premade adventure package {adv_id}.",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }

                if not meta.get("id"):
                    meta["id"] = adv_id

                # Calculate item counts
                lore_count = sum(1 for _, path in items if "lore/" in path and path.endswith(".txt"))
                track_count = sum(
                    1 for _, path in items
                    if "playlists/" in path and path.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a"))
                )
                ref_count = sum(
                    1 for _, path in items
                    if any(r in path for r in ("references/", "reference_library/"))
                    and path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                )

                meta["lore_count"] = lore_count
                meta["track_count"] = track_count
                meta["reference_count"] = ref_count
                meta["has_custom_config"] = yaml_blob is not None

                # Ensure cover image URL/path exists
                if not meta.get("cover_image"):
                    for _, path in items:
                        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            meta["cover_image"] = path
                            break

                results.append(meta)

            return results
        except Exception as e:
            logger.warning("Error fetching adventures from GCS: %s", e)
            return []

    def _fetch_adventures_from_local(self) -> List[Dict[str, Any]]:
        """Fallback to read adventures from local directory if GCS is unreachable."""
        if not self._local_fallback_dir.exists():
            return []

        results = []
        for folder in self._local_fallback_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            meta_file = folder / "metadata.json"
            meta: Dict[str, Any] = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            adv_id = meta.get("id") or folder.name.lower().replace(" ", "-")
            if not meta:
                meta = {
                    "id": adv_id,
                    "title": folder.name,
                    "description": f"Premade adventure package for {folder.name}.",
                    "created_at": datetime.fromtimestamp(folder.stat().st_ctime, timezone.utc).isoformat(),
                }
            if not meta.get("id"):
                meta["id"] = adv_id

            lore_count = len(list(folder.glob("lore/**/*.txt")))
            track_count = len([
                p for p in folder.glob("playlists/**/*")
                if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac", ".m4a")
            ])
            ref_count = len([
                p for p in list(folder.glob("references/**/*")) + list(folder.glob("reference_library/**/*"))
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")
            ])

            meta["lore_count"] = lore_count
            meta["track_count"] = track_count
            meta["reference_count"] = ref_count
            meta["has_custom_config"] = (folder / "theater.yaml").exists()

            results.append(meta)

        return results

    def get_adventure_cover(self, adventure_id: str) -> Optional[Tuple[bytes, str]]:
        """Return (image_bytes, content_type) for the adventure's cover image."""
        adv = self.get_adventure(adventure_id)
        cover_path = adv.get("cover_image") if adv else None

        # 1. Try GCS
        bucket = self._get_bucket()
        if bucket:
            try:
                prefix = f"{self.prefix}/" if self.prefix else ""
                if cover_path:
                    blob_name = f"{prefix}{adventure_id}/{cover_path}"
                    blob = bucket.blob(blob_name)
                    if blob.exists():
                        content = blob.download_as_bytes()
                        content_type = blob.content_type or mimetypes.guess_type(cover_path)[0] or "image/jpeg"
                        return content, content_type

                # Find any image in folder if specified cover not found
                for blob in bucket.list_blobs(prefix=f"{prefix}{adventure_id}/"):
                    if blob.name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        content = blob.download_as_bytes()
                        content_type = blob.content_type or mimetypes.guess_type(blob.name)[0] or "image/jpeg"
                        return content, content_type
            except Exception as e:
                logger.warning("Error fetching cover image from GCS for %s: %s", adventure_id, e)

        # 2. Try local fallback
        if self._local_fallback_dir.exists():
            for folder in self._local_fallback_dir.iterdir():
                if not folder.is_dir():
                    continue
                cand_id = folder.name.lower().replace(" ", "-")
                meta_file = folder / "metadata.json"
                if meta_file.exists():
                    try:
                        m = json.loads(meta_file.read_text(encoding="utf-8"))
                        if m.get("id"):
                            cand_id = m["id"]
                    except Exception:
                        pass
                if cand_id == adventure_id or folder.name == adventure_id:
                    if cover_path and (folder / cover_path).exists():
                        img_file = folder / cover_path
                        ctype = mimetypes.guess_type(str(img_file))[0] or "image/jpeg"
                        return img_file.read_bytes(), ctype
                    for ext in (".png", ".jpg", ".jpeg", ".webp"):
                        found = list(folder.rglob(f"*{ext}"))
                        if found:
                            ctype = mimetypes.guess_type(str(found[0]))[0] or "image/jpeg"
                            return found[0].read_bytes(), ctype

        return None

    def load_adventure_assets(
        self, adventure_id: str
    ) -> Tuple[List[Tuple[str, bytes]], Dict[str, List[Tuple[str, bytes]]], List[Tuple[str, bytes]], Dict[str, Any]]:
        """Download and package all adventure assets for mounting to a new theater.

        Returns:
            Tuple of:
            - reference_files: List[(rel_path, bytes)]
            - playlists_data: Dict[playlist_name, List[(filename, bytes)]]
            - lore_files: List[(rel_path, bytes)]
            - theater_config: Dict loaded from theater.yaml
        """
        reference_files: List[Tuple[str, bytes]] = []
        playlists_data: Dict[str, List[Tuple[str, bytes]]] = {}
        lore_files: List[Tuple[str, bytes]] = []
        theater_config: Dict[str, Any] = {}

        # 1. Attempt GCS load
        bucket = self._get_bucket()
        if bucket:
            try:
                prefix = f"{self.prefix}/" if self.prefix else ""
                adv_prefix = f"{prefix}{adventure_id}/"
                blobs = list(bucket.list_blobs(prefix=adv_prefix))

                if blobs:
                    for blob in blobs:
                        rel = blob.name[len(adv_prefix):]
                        if not rel or rel.endswith("/"):
                            continue
                        content = blob.download_as_bytes()
                        parts = [p for p in rel.split("/") if p]
                        filename = parts[-1]

                        if rel in ("theater.yaml", "theater.yml"):
                            try:
                                loaded = yaml.safe_load(content.decode("utf-8"))
                                if isinstance(loaded, dict):
                                    theater_config = loaded
                            except Exception as e:
                                logger.warning("Failed to parse theater.yaml for %s: %s", adventure_id, e)
                        elif "references" in parts or "reference_library" in parts:
                            if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                                reference_files.append((rel, content))
                        elif "playlists" in parts:
                            idx = parts.index("playlists")
                            pl_name = parts[idx + 1] if idx + 1 < len(parts) - 1 else "default"
                            if filename.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                                if pl_name not in playlists_data:
                                    playlists_data[pl_name] = []
                                playlists_data[pl_name].append((filename, content))
                        elif "lore" in parts:
                            if filename.lower().endswith(".txt"):
                                lore_files.append((rel, content))

                    return reference_files, playlists_data, lore_files, theater_config
            except Exception as e:
                logger.warning("Error downloading adventure %s from GCS: %s", adventure_id, e)

        # 2. Local fallback
        if self._local_fallback_dir.exists():
            matched_folder = None
            for folder in self._local_fallback_dir.iterdir():
                if not folder.is_dir():
                    continue
                cand_id = folder.name.lower().replace(" ", "-")
                meta_file = folder / "metadata.json"
                if meta_file.exists():
                    try:
                        m = json.loads(meta_file.read_text(encoding="utf-8"))
                        if m.get("id"):
                            cand_id = m["id"]
                    except Exception:
                        pass
                if cand_id == adventure_id or folder.name == adventure_id:
                    matched_folder = folder
                    break

            if matched_folder:
                for file_path in matched_folder.rglob("*"):
                    if not file_path.is_file():
                        continue
                    rel = file_path.relative_to(matched_folder).as_posix()
                    parts = [p for p in rel.split("/") if p]
                    filename = file_path.name
                    content = file_path.read_bytes()

                    if rel in ("theater.yaml", "theater.yml"):
                        try:
                            loaded = yaml.safe_load(content.decode("utf-8"))
                            if isinstance(loaded, dict):
                                theater_config = loaded
                        except Exception:
                            pass
                    elif "references" in parts or "reference_library" in parts:
                        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                            reference_files.append((rel, content))
                    elif "playlists" in parts:
                        idx = parts.index("playlists")
                        pl_name = parts[idx + 1] if idx + 1 < len(parts) - 1 else "default"
                        if filename.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                            if pl_name not in playlists_data:
                                playlists_data[pl_name] = []
                            playlists_data[pl_name].append((filename, content))
                    elif "lore" in parts:
                        if filename.lower().endswith(".txt"):
                            lore_files.append((rel, content))

        return reference_files, playlists_data, lore_files, theater_config


adventure_service = AdventureService()
