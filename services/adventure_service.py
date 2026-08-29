"""Service for managing premade adventures stored on disk or mounted storage.

Operates directly on a directory path (e.g. local directory or mounted cloud storage).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import mimetypes
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import yaml

from absl import flags

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

CACHE_TTL_SECONDS = 60.0


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

    def __init__(self, base_dir: Path | str):
        self._base_dir = Path(base_dir).resolve()

        self._adventures_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: float = 0.0

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

        adventures = self._fetch_adventures()

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
    ) -> Tuple[List[Tuple[str, bytes]], Dict[str, List[Tuple[str, bytes]]], List[Tuple[str, bytes]], Dict[str, Any]]:
        """Load and package all adventure assets for mounting to a new theater.

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