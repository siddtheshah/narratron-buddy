"""Service for managing pre-defined avatars stored on disk or mounted storage.

Lists avatar packages through local or mounted storage and downloads cloud assets
directly through the GCS Python client when attaching an avatar to a theater.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import mimetypes
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock
from google.cloud import storage
from google.cloud.storage import transfer_manager
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from absl import flags
from components.canvas.avatar_state import AvatarActionSpec
from components.theater_manager import asset_destination
from components.theater_package import SHADER_EXTENSIONS, is_avatar_asset, safe_asset_path
from services.catalog_sentinel import (
    CatalogCacheKey,
    gcs_sentinel_cache_key,
    local_directory_cache_key,
)

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, games, avatars, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS

# Ensure mime types are registered
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("text/plain", ".glsl")
mimetypes.add_type("text/plain", ".vert")
mimetypes.add_type("text/plain", ".frag")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")

CACHE_CHECK_INTERVAL_SECONDS = 60.0
DEFAULT_AVATARS_BUCKET = "narratron-buddy-app-storage"
DEFAULT_AVATARS_PREFIX = "avatars"
DOWNLOAD_WORKERS = 8

COVER_EXTENSIONS = (".webp", ".png", ".jpg", ".jpeg", ".svg")
_AVATAR_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)

logger = logging.getLogger(__name__)


class AvatarCatalogManifest(BaseModel):
    """Subset of avatar.json used to populate avatar catalog cards."""

    id: str = ""
    title: str = ""
    model: str = ""
    poster: str = ""
    actions: list[AvatarActionSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


def get_avatars_root() -> Path:
    """Return the avatar-data root for the selected runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return Path(__file__).parent.parent / "avatars"
    return Path("/mnt/storage/avatars")


def ensure_avatars_root() -> Path:
    """Return and create the selected avatar-data root."""
    avatars_root = get_avatars_root().resolve()
    avatars_root.mkdir(parents=True, exist_ok=True)
    return avatars_root


class AvatarService:
    """Provides access to pre-defined avatar packages on local or mounted storage."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir: Path | None = Path(base_dir).resolve()
        self._avatars_cache: list[dict[str, JsonValue]] | None = None
        self._cache_key: CatalogCacheKey | None = None
        self._gcs_client: storage.Client | None = None
        self._refresh_lock = Lock()

    @property
    def base_dir(self) -> Path:
        """Return the effective avatars root directory."""
        if self._base_dir is not None:
            return self._base_dir
        return get_avatars_root().resolve()

    @base_dir.setter
    def base_dir(self, value: Path | str | None) -> None:
        self._base_dir = Path(value).resolve() if value is not None else None

    @property
    def local_dir(self) -> Path:
        return self.base_dir

    def invalidate_cache(self) -> None:
        """Clear cached avatar listings."""
        with self._refresh_lock:
            self._avatars_cache = None
            self._cache_key = None

    def _use_gcs(self) -> bool:
        """Return whether this service targets the cloud GCS avatars bucket."""
        return (
            not FLAGS["testing_use_local"].value
            and self.base_dir == get_avatars_root().resolve()
        )

    def _get_gcs_client(self) -> storage.Client:
        """Lazily initialize and reuse a GCS client for sentinel checks."""
        if self._gcs_client is None:
            self._gcs_client = storage.Client()
        return self._gcs_client

    def _get_cache_key(self) -> CatalogCacheKey:
        """Check only the GCS sentinel in cloud mode, or stat local files in local mode."""
        if self._use_gcs():
            return gcs_sentinel_cache_key(
                self._get_gcs_client(),
                os.getenv("GCS_BUCKET", DEFAULT_AVATARS_BUCKET),
                os.getenv("GCS_AVATARS_PREFIX", DEFAULT_AVATARS_PREFIX),
            )
        return local_directory_cache_key(self.base_dir)

    def refresh_if_changed(self, force_refresh: bool = False) -> bool:
        """Atomically replace the snapshot only when its source fingerprint changes."""
        with self._refresh_lock:
            cache_key = self._get_cache_key()
            if not force_refresh and self._avatars_cache is not None and cache_key == self._cache_key:
                return False
            avatars = self._fetch_avatars()
            avatars.sort(key=lambda avatar: str(avatar.get("created_at") or ""), reverse=True)
            self._avatars_cache = avatars
            self._cache_key = cache_key
            return True

    def list_avatars(self, force_refresh: bool = False) -> list[dict[str, JsonValue]]:
        """List all pre-defined avatars sorted by newest first (created_at desc)."""
        if force_refresh or self._avatars_cache is None:
            self.refresh_if_changed(force_refresh=force_refresh)
        return self._avatars_cache or []

    def get_avatar(self, avatar_id: str) -> dict[str, JsonValue] | None:
        """Retrieve metadata for a specific avatar by ID."""
        for avatar in self.list_avatars():
            if avatar.get("id") == avatar_id:
                return avatar
        return None

    @staticmethod
    def _read_metadata(meta_file: Path) -> dict[str, JsonValue]:
        """Parse metadata.json into a JSON dictionary."""
        if not meta_file.is_file():
            return {}
        return TypeAdapter(dict[str, JsonValue]).validate_json(meta_file.read_text(encoding="utf-8"))

    @staticmethod
    def _read_manifest(manifest_file: Path) -> AvatarCatalogManifest | None:
        """Parse avatar.json into an AvatarCatalogManifest."""
        if not manifest_file.is_file():
            return None
        return AvatarCatalogManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))

    @classmethod
    def _package_id_for_folder(cls, folder: Path) -> str:
        """Resolve the canonical avatar package ID for a package directory."""
        meta = cls._read_metadata(folder / "metadata.json")
        raw_id = meta.get("id")
        if raw_id:
            return str(raw_id)
        manifest = cls._read_manifest(folder / "avatar.json")
        if manifest is not None and manifest.id.strip():
            return manifest.id.strip()
        return folder.name.lower().replace(" ", "-")

    def _fetch_avatars(self) -> list[dict[str, JsonValue]]:
        """Read avatar packages from the base directory."""
        target_dir = self.base_dir
        if not target_dir.exists():
            return []

        results: list[dict[str, JsonValue]] = []
        for folder in target_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            meta = self._read_metadata(folder / "metadata.json")
            manifest = self._read_manifest(folder / "avatar.json")
            raw_id = meta.get("id")
            manifest_id = manifest.id.strip() if manifest is not None else ""
            avatar_id = str(raw_id) if raw_id else (manifest_id or folder.name.lower().replace(" ", "-"))
            default_title = (manifest.title.strip() if manifest is not None else "") or folder.name
            if not meta:
                meta = {
                    "id": avatar_id,
                    "title": default_title,
                    "description": f"Pre-defined avatar package for {default_title}.",
                    "created_at": datetime.fromtimestamp(folder.stat().st_ctime, timezone.utc).isoformat(),
                }
            if not meta.get("id"):
                meta["id"] = avatar_id
            if not meta.get("title"):
                meta["title"] = default_title

            manifest_file = "avatar.json" if (folder / "avatar.json").is_file() else ""
            model_name = (manifest.model.strip() if manifest is not None else "") or "model.glb"
            model_path = (folder / model_name).resolve()
            has_model = folder.resolve() in model_path.parents and model_path.is_file()
            action_count = len(manifest.actions) if manifest is not None else 0
            shader_count = sum(
                1
                for p in folder.rglob("*")
                if p.is_file()
                and "__pycache__" not in p.parts
                and p.suffix.lower() in SHADER_EXTENSIONS
                and is_avatar_asset(("avatars", avatar_id, *p.relative_to(folder).parts))
            )

            meta["manifest_file"] = manifest_file
            meta["has_model"] = has_model
            meta["action_count"] = action_count
            meta["shader_count"] = shader_count

            if not meta.get("cover_image"):
                poster_name = manifest.poster.strip() if manifest is not None else ""
                if poster_name:
                    poster_path = (folder / poster_name).resolve()
                    if folder.resolve() in poster_path.parents and poster_path.is_file():
                        meta["cover_image"] = poster_path.relative_to(folder).as_posix()
                if not meta.get("cover_image") and (folder / "preview.webp").is_file():
                    meta["cover_image"] = "preview.webp"
                if not meta.get("cover_image"):
                    for ext in COVER_EXTENSIONS:
                        found = [p for p in folder.rglob(f"*{ext}") if p.is_file() and "__pycache__" not in p.parts]
                        if found:
                            meta["cover_image"] = found[0].relative_to(folder).as_posix()
                            break

            results.append(meta)

        return results

    def _find_avatar_folder(self, avatar_id: str) -> Path | None:
        target_dir = self.base_dir
        if not target_dir.exists():
            return None
        for folder in target_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            cand_id = self._package_id_for_folder(folder)
            if cand_id == avatar_id or folder.name == avatar_id:
                return folder
        return None

    def get_avatar_cover(self, avatar_id: str) -> tuple[bytes, str] | None:
        """Return (image_bytes, content_type) for the avatar's cover image."""
        avatar = self.get_avatar(avatar_id)
        cover_path = str(avatar.get("cover_image") or "") if avatar else ""

        folder = self._find_avatar_folder(avatar_id)
        if folder is None:
            return None

        if cover_path:
            img_file = (folder / cover_path).resolve()
            if folder.resolve() in img_file.parents and img_file.is_file():
                ctype = mimetypes.guess_type(str(img_file))[0] or "image/webp"
                return img_file.read_bytes(), ctype

        for ext in COVER_EXTENSIONS:
            found = [p for p in folder.rglob(f"*{ext}") if p.is_file() and "__pycache__" not in p.parts]
            if found:
                ctype = mimetypes.guess_type(str(found[0]))[0] or "image/webp"
                return found[0].read_bytes(), ctype

        return None

    def load_avatar_assets(self, avatar_id: str) -> dict[str, bytes]:
        """Load and package all attachable avatar files for mounting into a theater.

        Returns a mapping of theater-relative asset paths (such as
        ``avatars/<id>/avatar.json``, ``avatars/<id>/model.glb``,
        ``avatars/<id>/preview.webp``, and ``avatars/<id>/shaders/*``) to file bytes.
        """
        matched_folder = self._find_avatar_folder(avatar_id)
        if matched_folder is None:
            return {}

        package_id = self._package_id_for_folder(matched_folder)
        if not _AVATAR_PACKAGE_RE.fullmatch(package_id):
            raise ValueError(f"Invalid avatar package ID: {package_id!r}")

        if self._use_gcs():
            with TemporaryDirectory(prefix="narratron-avatar-") as temporary:
                local_folder = Path(temporary)
                self._download_avatar_folder(matched_folder.name, local_folder)
                return self._load_avatar_folder(local_folder, package_id)
        return self._load_avatar_folder(matched_folder, package_id)

    @staticmethod
    def _download_avatar_folder(folder_name: str, destination: Path) -> None:
        """Download an avatar package directly from GCS with bounded parallel transfers."""
        client = storage.Client()
        bucket = client.bucket(os.getenv("GCS_BUCKET", DEFAULT_AVATARS_BUCKET))
        root_prefix = os.getenv("GCS_AVATARS_PREFIX", DEFAULT_AVATARS_PREFIX).strip("/")
        prefix = f"{root_prefix}/{folder_name}/" if root_prefix else f"{folder_name}/"
        names = [
            blob.name.removeprefix(prefix)
            for blob in bucket.list_blobs(prefix=prefix)
            if not blob.name.endswith("/")
        ]
        if not names:
            raise FileNotFoundError(f"No avatar files found under gs://{bucket.name}/{prefix}")
        for name in names:
            asset_destination(destination, name)
        transfer_manager.download_many_to_path(
            bucket,
            names,
            destination_directory=str(destination),
            blob_name_prefix=prefix,
            worker_type=transfer_manager.THREAD,
            max_workers=DOWNLOAD_WORKERS,
            raise_exception=True,
        )

    @staticmethod
    def _load_avatar_folder(folder: Path, package_id: str) -> dict[str, bytes]:
        """Extract attachable avatar files from a local or downloaded avatar package."""
        assets: dict[str, bytes] = {}
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(folder).as_posix()
            parts = [p for p in rel.split("/") if p]
            if not parts or any(p.startswith(".") for p in parts) or "__pycache__" in parts:
                continue
            suffix = file_path.suffix.lower()
            if suffix in {".py", ".pyc", ".pyo"}:
                continue

            target_parts = ("avatars", package_id, *parts)
            if is_avatar_asset(target_parts):
                target_rel = "/".join(target_parts)
                safe_asset_path(folder, target_rel)
                assets[target_rel] = file_path.read_bytes()

        return assets
