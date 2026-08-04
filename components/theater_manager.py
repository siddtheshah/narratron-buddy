"""Filesystem-backed theater lifecycle and asset management."""

from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import secrets
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import zipfile

from pydantic import BaseModel, Field

from absl import flags

logger = logging.getLogger(__name__)

MAX_ZIP_BYTES = 10 * 1024 * 1024


flags.DEFINE_boolean(
    "use_cloud_theater_storage",
    False,
    "Store theater files under /tmp/theaters instead of the workspace theaters directory.",
)

FLAGS = flags.FLAGS


def get_theaters_root() -> Path:
    """Return the theater-data root for the selected runtime environment."""
    if FLAGS["use_cloud_theater_storage"].value:
        return Path("/mnt/storage/theaters")
    return Path(__file__).parent.parent / "theaters"


def ensure_theaters_root() -> Path:
    """Return and create the selected theater-data root."""
    theaters_root = get_theaters_root().resolve()
    theaters_root.mkdir(parents=True, exist_ok=True)
    return theaters_root


class TheaterMetadata(BaseModel):
    """Persisted metadata for one filesystem-backed theater."""

    theater_id: str
    name: str
    status: str = "created"
    join_key: str = Field(default_factory=lambda: f"KEY-{''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mounted_references: List[str] = Field(default_factory=list)
    mounted_playlists: Dict[str, List[str]] = Field(default_factory=dict)
    config: Dict = Field(default_factory=dict)
    canvas_state: Dict = Field(default_factory=dict)
    allowed_orators: List[int] = Field(default_factory=list)
    active_orator_id: Optional[int] = None
    baton_request: Optional[Dict] = None


@dataclass(frozen=True)
class Theater:
    """A theater-bound filesystem and lifecycle interface."""

    manager: "TheaterManager"
    theater_id: str

    def directory(self) -> Path:
        return self.manager._get_theater_dir(self.theater_id)

    def references_dir(self) -> Path:
        return self.manager._get_theater_reference_dir(self.theater_id)

    def playlists_dir(self) -> Path:
        return self.manager._get_theater_playlists_dir(self.theater_id)

    def output_dir(self) -> Path:
        return self.manager._get_theater_output_dir(self.theater_id)

    def artifacts_dir(self) -> Path:
        return self.manager._get_theater_artifacts_dir(self.theater_id)

    def image_artifacts_dir(self) -> Path:
        return self.manager._get_theater_image_artifacts_dir(self.theater_id)

    def notes_artifacts_dir(self) -> Path:
        return self.manager._get_theater_notes_artifacts_dir(self.theater_id)

    @property
    def metadata(self) -> Optional[TheaterMetadata]:
        return self.manager.get_theater(self.theater_id)

    def deploy(self) -> TheaterMetadata:
        return self.manager.deploy_theater(self.theater_id)

    def stop(self) -> TheaterMetadata:
        return self.manager.stop_theater(self.theater_id)

    def destroy(self) -> bool:
        return self.manager.destroy_theater(self.theater_id)

    def references(self) -> List[Dict[str, str]]:
        return self.manager.get_theater_references(self.theater_id)

    def playlists(self) -> Dict[str, List[Dict[str, str]]]:
        return self.manager.get_theater_playlists(self.theater_id)


def extract_asset_package(
    zip_bytes: bytes, max_bytes: int = MAX_ZIP_BYTES,
) -> Tuple[List[Tuple[str, bytes]], Dict[str, List[Tuple[str, bytes]]], Optional[str]]:
    """Read supported assets and an optional ``theater.yaml`` from a ZIP archive."""
    if len(zip_bytes) > max_bytes:
        raise ValueError(f"ZIP archive exceeds max allowed size of {max_bytes // (1024 * 1024)}MB.")

    reference_files: List[Tuple[str, bytes]] = []
    playlists_data: Dict[str, List[Tuple[str, bytes]]] = {}
    theater_config_yaml: Optional[str] = None
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > max_bytes * 2:
                    raise ValueError("ZIP uncompressed size exceeds allowable limit.")
                parts = [part for part in info.filename.replace("\\", "/").split("/") if part and part != "__MACOSX"]
                if not parts or parts[-1].startswith("."):
                    continue
                filename, content = parts[-1], archive.read(info.filename)
                if "references" in parts or (filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) and "playlists" not in parts):
                    reference_files.append((info.filename, content))
                elif "playlists" in parts and filename.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                    index = parts.index("playlists")
                    playlist = parts[index + 1] if index + 1 < len(parts) - 1 else "default"
                    playlists_data.setdefault(playlist, []).append((filename, content))
                elif filename.lower() == "theater.yaml":
                    try:
                        theater_config_yaml = content.decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError("theater.yaml must be UTF-8 encoded.")
    except ValueError:
        raise
    except Exception as error:
        logger.error("Error parsing asset ZIP package: %s", error)
    return reference_files, playlists_data, theater_config_yaml


class TheaterManager:
    """Own theater workspace creation, lifecycle metadata, and asset lookup."""

    def __init__(self, base_theaters_dir: Optional[str | Path] = None):
        self.base_dir = Path(base_theaters_dir).resolve() if base_theaters_dir else ensure_theaters_root()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_theater_dir(self, theater_id: str) -> Path:
        return self.base_dir / theater_id

    def theater(self, theater_id: str) -> Theater:
        """Return a theater-bound interface without creating its workspace."""
        return Theater(manager=self, theater_id=theater_id)

    def _get_theater_reference_dir(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "references"

    def _get_theater_playlists_dir(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "playlists"

    def _get_theater_output_dir(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "output"

    def _get_theater_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_output_dir(theater_id) / "artifacts"

    def _get_theater_image_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_artifacts_dir(theater_id) / "images"

    def _get_theater_notes_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_artifacts_dir(theater_id) / "notes"

    def _metadata_path(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "theater.json"

    def _save_metadata(self, metadata: TheaterMetadata) -> None:
        theater_dir = self._get_theater_dir(metadata.theater_id)
        theater_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path(metadata.theater_id).write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

    def create_theater(self, name: str, theater_id: str, reference_files: Optional[List[tuple[str, bytes]]] = None, playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None, theater_config: Optional[Dict] = None) -> TheaterMetadata:
        # Import lazily so config loading can reuse the theater-root helper.
        from utils.config_loader import deep_merge, get_theater_default_config, save_theater_config

        theater_dir = self._get_theater_dir(theater_id)
        if theater_dir.exists():
            raise ValueError(f"Theater with ID '{theater_id}' already exists.")
        reference_dir = self._get_theater_reference_dir(theater_id)
        playlists_dir = self._get_theater_playlists_dir(theater_id)
        reference_dir.mkdir(parents=True)
        playlists_dir.mkdir()
        self._get_theater_output_dir(theater_id).mkdir()
        mounted_references = []
        for relative_filename, content in reference_files or []:
            parts = [part for part in relative_filename.replace("\\", "/").split("/") if part]
            relative_path = Path(*parts[parts.index("references") + 1:]) if "references" in parts else Path(parts[-1])
            target = reference_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            mounted_references.append(relative_path.as_posix())
        default_references = Path(__file__).parent.parent / "reference_library"
        if default_references.exists():
            for reference in default_references.iterdir():
                if reference.is_file() and reference.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    target = reference_dir / reference.name
                    if not target.exists():
                        shutil.copy2(reference, target)
                        mounted_references.append(reference.name)

        mounted_playlists: Dict[str, List[str]] = {}
        for playlist_name, files in (playlists_data or {}).items():
            playlist_dir = playlists_dir / playlist_name
            playlist_dir.mkdir(parents=True, exist_ok=True)
            mounted_playlists[playlist_name] = []
            for filename, content in files:
                clean_name = Path(filename).name
                (playlist_dir / clean_name).write_bytes(content)
                mounted_playlists[playlist_name].append(clean_name)

        config = get_theater_default_config()
        if theater_config:
            deep_merge(config, theater_config)
        save_theater_config(theater_id, config, base_dir=self.base_dir)
        metadata = TheaterMetadata(theater_id=theater_id, name=name, mounted_references=mounted_references, mounted_playlists=mounted_playlists, config=config)
        self._save_metadata(metadata)
        return metadata

    def get_theater(self, theater_id: str) -> Optional[TheaterMetadata]:
        metadata_path = self._metadata_path(theater_id)
        if not metadata_path.exists():
            return None
        return TheaterMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))

    def list_theaters(self) -> List[TheaterMetadata]:
        theaters = []
        for entry in self.base_dir.iterdir() if self.base_dir.exists() else []:
            if entry.is_dir() and (entry / "theater.json").exists():
                try:
                    theaters.append(TheaterMetadata.model_validate_json((entry / "theater.json").read_text(encoding="utf-8")))
                except (OSError, ValueError) as error:
                    logger.warning("Skipping invalid theater metadata in %s: %s", entry, error)
        return sorted(theaters, key=lambda theater: theater.created_at, reverse=True)

    def deploy_theater(self, theater_id: str) -> TheaterMetadata:
        return self._set_status(theater_id, "deployed")

    def stop_theater(self, theater_id: str) -> TheaterMetadata:
        return self._set_status(theater_id, "stopped")

    def _set_status(self, theater_id: str, status: str) -> TheaterMetadata:
        metadata = self.get_theater(theater_id)
        if metadata is None:
            raise FileNotFoundError(f"Theater '{theater_id}' not found.")
        metadata.status = status
        self._save_metadata(metadata)
        return metadata

    def destroy_theater(self, theater_id: str) -> bool:
        theater_dir = self._get_theater_dir(theater_id)
        if not theater_dir.exists():
            return False
        shutil.rmtree(theater_dir)
        return True

    def get_theater_references(self, theater_id: str) -> List[Dict[str, str]]:
        reference_dir = self._get_theater_reference_dir(theater_id)
        if not reference_dir.exists():
            return []
        return [{"name": file.stem, "filename": file.name, "url": f"/theaters/{theater_id}/references/{file.name}", "size_bytes": file.stat().st_size} for file in reference_dir.iterdir() if file.is_file() and file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}]

    def get_theater_playlists(self, theater_id: str) -> Dict[str, List[Dict[str, str]]]:
        playlists_dir = self._get_theater_playlists_dir(theater_id)
        if not playlists_dir.exists():
            return {}
        return {directory.name: [{"filename": track.name, "url": f"/theaters/{theater_id}/playlists/{directory.name}/{track.name}", "size_bytes": track.stat().st_size} for track in directory.iterdir() if track.is_file() and track.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"}] for directory in playlists_dir.iterdir() if directory.is_dir()}
