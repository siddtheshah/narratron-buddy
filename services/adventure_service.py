"""Service for managing premade adventures stored on disk or mounted storage.

Lists packages through local or mounted storage and downloads cloud assets
directly through the GCS Python client when preparing a deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import mimetypes
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
import yaml
from google.cloud import storage
from google.cloud.storage import transfer_manager
from pydantic import JsonValue, TypeAdapter

from absl import flags
from game.game_source import GAME_FILES
from components.theater_manager import asset_destination

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS

# Ensure mime types are registered
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")

CACHE_CHECK_INTERVAL_SECONDS = 60.0
DEFAULT_ADVENTURES_BUCKET = "narratron-buddy-app-storage"
DEFAULT_ADVENTURES_PREFIX = "adventures"
DOWNLOAD_WORKERS = 8

AdventureAssets = tuple[list[tuple[str, bytes]], dict[str, list[tuple[str, bytes]]],
                        list[tuple[str, bytes]], dict[str, JsonValue]]


def get_adventures_root() -> Path:
    """Return the adventure-data root for the selected runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return Path(__file__).parent.parent / "adventures"
    return Path("/mnt/storage/adventures")


def ensure_adventures_root() -> Path:
    """Return and create the selected adventure-data root."""
    adventures_root = get_adventures_root().resolve()
    adventures_root.mkdir(parents=True, exist_ok=True)
    return adventures_root


logger = logging.getLogger(__name__)


class AdventureService:
    """Provides access to premade adventure packages on local or mounted storage."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir = Path(base_dir).resolve()

        self._adventures_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_key: Optional[tuple[tuple[str, int, int], ...]] = None
        self._refresh_lock = Lock()

    @property
    def base_dir(self) -> Path:
        """Return the effective adventures root directory."""
        if self._base_dir is not None:
            return self._base_dir
        return get_adventures_root().resolve()

    @base_dir.setter
    def base_dir(self, value: Optional[Path | str]) -> None:
        self._base_dir = Path(value).resolve() if value is not None else None

    # Maintain property aliases for backwards compatibility
    @property
    def local_dir(self) -> Path:
        return self.base_dir

    def invalidate_cache(self) -> None:
        """Clear cached adventure listings."""
        with self._refresh_lock:
            self._adventures_cache = None
            self._cache_key = None

    def _get_cache_key(self) -> tuple[tuple[str, int, int], ...]:
        """Fingerprint package paths and file stats without reading asset contents.

        This scan runs at startup or in the background, never on a warm listing.
        Directory entries also detect added/removed empty adventure packages.
        """
        root = self.base_dir
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

    def refresh_if_changed(self, force_refresh: bool = False) -> bool:
        """Atomically replace the snapshot only when its source fingerprint changes.

        Failures leave the previous snapshot and key intact for the next retry.
        A key taken before loading ensures edits during a scan are checked again.
        """
        with self._refresh_lock:
            cache_key = self._get_cache_key()
            if not force_refresh and self._adventures_cache is not None and cache_key == self._cache_key:
                return False
            adventures = self._fetch_adventures()
            adventures.sort(key=lambda adventure: adventure.get("created_at") or "", reverse=True)
            self._adventures_cache = adventures
            self._cache_key = cache_key
            return True

    def list_adventures(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """List all premade adventures sorted by newest first (created_at desc)."""
        if force_refresh or self._adventures_cache is None:
            self.refresh_if_changed(force_refresh=force_refresh)
        return self._adventures_cache

    def get_adventure(self, adventure_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve metadata for a specific adventure by ID."""
        for adv in self.list_adventures():
            if adv.get("id") == adventure_id:
                return adv
        return None

    def _fetch_adventures(self) -> List[Dict[str, Any]]:
        """Read adventure packages from the base directory."""
        target_dir = self.base_dir
        if not target_dir.exists():
            return []

        results = []
        for folder in target_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            meta_file = folder / "metadata.json"
            meta: Dict[str, Any] = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning("Failed to decode metadata.json for adventure %s: %s", folder.name, e)

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

            lore_count = sum(1 for path in (folder / "lore").rglob("*") if path.is_file() and path.suffix.lower() in {".txt", ".md"})
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
            meta["has_custom_config"] = (folder / "theater.yaml").exists() or (folder / "theater.yml").exists()

            # Ensure cover image URL/path exists
            if not meta.get("cover_image"):
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    found = list(folder.rglob(f"*{ext}"))
                    if found:
                        meta["cover_image"] = found[0].relative_to(folder).as_posix()
                        break

            results.append(meta)

        return results

    def get_adventure_cover(self, adventure_id: str) -> Optional[Tuple[bytes, str]]:
        """Return (image_bytes, content_type) for the adventure's cover image."""
        adv = self.get_adventure(adventure_id)
        cover_path = adv.get("cover_image") if adv else None

        target_dir = self.base_dir
        if not target_dir.exists():
            return None

        for folder in target_dir.iterdir():
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
    ) -> AdventureAssets:
        """Load and package all adventure assets for mounting to a new theater.

        Returns:
            Tuple of:
            - reference_files: List[(rel_path, bytes)], including character portraits and stamps
            - playlists_data: Dict[playlist_name, List[(filename, bytes)]]
            - lore_files: List[(rel_path, bytes)]
            - theater_config: Dict loaded from theater.yaml
        """
        reference_files: list[tuple[str, bytes]] = []
        playlists_data: dict[str, list[tuple[str, bytes]]] = {}
        lore_files: list[tuple[str, bytes]] = []
        theater_config: dict[str, JsonValue] = {}

        target_dir = self.base_dir
        if not target_dir.exists():
            return reference_files, playlists_data, lore_files, theater_config

        matched_folder = None
        for folder in target_dir.iterdir():
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

        if matched_folder is None:
            return reference_files, playlists_data, lore_files, theater_config

        use_gcs = (not FLAGS["testing_use_local"].value
                   and self.base_dir == get_adventures_root().resolve())
        if use_gcs:
            with TemporaryDirectory(prefix="narratron-adventure-") as temporary:
                local_folder = Path(temporary)
                self._download_adventure_folder(matched_folder.name, local_folder)
                return self._load_adventure_folder(local_folder, adventure_id)
        return self._load_adventure_folder(matched_folder, adventure_id)

    @staticmethod
    def _download_adventure_folder(folder_name: str, destination: Path) -> None:
        """Download a package directly from GCS with bounded parallel transfers."""
        client = storage.Client()
        bucket = client.bucket(os.getenv("GCS_BUCKET", DEFAULT_ADVENTURES_BUCKET))
        root_prefix = os.getenv("GCS_ADVENTURES_PREFIX", DEFAULT_ADVENTURES_PREFIX).strip("/")
        prefix = f"{root_prefix}/{folder_name}/" if root_prefix else f"{folder_name}/"
        names = [blob.name.removeprefix(prefix) for blob in bucket.list_blobs(prefix=prefix)
                 if not blob.name.endswith("/")]
        if not names:
            raise FileNotFoundError(f"No adventure files found under gs://{bucket.name}/{prefix}")
        # Reject unsafe object names before starting any of the downloads.
        for name in names:
            asset_destination(destination, name)
        transfer_manager.download_many_to_path(
            bucket, names, destination_directory=str(destination), blob_name_prefix=prefix,
            worker_type=transfer_manager.THREAD, max_workers=DOWNLOAD_WORKERS,
            raise_exception=True,
        )

    @staticmethod
    def _load_adventure_folder(folder: Path, adventure_id: str) -> AdventureAssets:
        """Apply the same package parsing to local and downloaded adventures."""
        reference_files: list[tuple[str, bytes]] = []
        playlists_data: dict[str, list[tuple[str, bytes]]] = {}
        lore_files: list[tuple[str, bytes]] = []
        theater_config: dict[str, JsonValue] = {}
        for file_path in folder.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(folder).as_posix()
            parts = [p for p in rel.split("/") if p]
            filename = file_path.name
            content = file_path.read_bytes()

            if rel in ("theater.yaml", "theater.yml"):
                try:
                    loaded = yaml.safe_load(content.decode("utf-8"))
                    theater_config = TypeAdapter(dict[str, JsonValue]).validate_python(loaded)
                except Exception as e:
                    logger.warning("Failed to parse theater.yaml for %s: %s", adventure_id, e)
            elif rel in GAME_FILES or (parts[0] == "game" and filename.lower().endswith(".svg")):
                reference_files.append((rel, content))
            elif rel in ("planning.yaml", "planning.yml") or filename.lower() in ("planning.yaml", "planning.yml"):
                reference_files.append(("planning.yaml", content))
            elif "references" in parts or "reference_library" in parts or "stamps" in parts or parts[0] == "characters":
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
                if filename.lower().endswith((".txt", ".md")):
                    lore_files.append((rel, content))

        return reference_files, playlists_data, lore_files, theater_config
