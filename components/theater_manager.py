"""Filesystem-backed theater lifecycle and asset management."""

from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
import zipfile

from pydantic import BaseModel, Field

from absl import flags
from components.reference_images import reference_image_type

logger = logging.getLogger(__name__)

MAX_ZIP_BYTES = 10 * 1024 * 1024
LORE_TEXT_EXTENSION = ".txt"
MAX_LORE_DOCUMENT_BYTES = 256 * 1024
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
}

FLAGS = flags.FLAGS
if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local ephemeral storage for testing.",
    )


def validate_asset_path(filename: str) -> list[str]:
    """Validate an upload path before stripping package or asset prefixes."""
    normalized = filename.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not filename
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(filename).drive
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            any(character in '<>:"|?*' or ord(character) < 32 for character in part)
            or part.endswith((".", " "))
            or part.split(".")[0].upper() in WINDOWS_RESERVED_NAMES
            for part in parts
        )
    ):
        raise ValueError("Asset paths must be relative paths without traversal or invalid components.")
    return parts


def asset_destination(root: Path, relative_path: str) -> Path:
    """Resolve an asset destination and reject escapes through existing links."""
    parts = validate_asset_path(relative_path)
    destination = (root / Path(*parts)).resolve()
    if root.resolve() not in destination.parents:
        raise ValueError("Asset path cannot escape its destination directory.")
    return destination


def get_ephemeral_root() -> Path:
    """Return the ephemeral theater workspace root for the active runtime."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return Path(__file__).parent.parent / "ephemeral"
    return Path("/tmp/ephemeral")


def ensure_ephemeral_root() -> Path:
    """Return and create the ephemeral theater workspace root."""
    root = get_ephemeral_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class TheaterMetadata(BaseModel):
    """Persisted metadata for one filesystem-backed theater."""

    theater_id: str
    name: str
    status: str = "created"
    join_key: str = Field(default_factory=lambda: f"KEY-{''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_connected_at: Optional[str] = None
    last_disconnected_at: Optional[str] = None
    last_auto_begin_at: Optional[str] = None
    mounted_references: List[str] = Field(default_factory=list)
    mounted_playlists: Dict[str, List[str]] = Field(default_factory=dict)
    config: Dict = Field(default_factory=dict)
    canvas_state: Dict = Field(default_factory=dict)
    contributors: List[int] = Field(default_factory=list)
    active_orator_id: Optional[int] = None
    baton_request: Optional[Dict] = None


@dataclass(frozen=True)
class Theater:
    """A theater-bound filesystem and lifecycle interface."""

    manager: "TheaterManager"
    theater_id: str = ""

    def directory(self) -> Path:
        return self.manager._get_theater_dir(self.theater_id)

    def references_dir(self) -> Path:
        return self.manager._get_theater_reference_dir(self.theater_id)

    def stamps_dir(self) -> Path:
        return self.directory() / "stamps"

    def stamps(self) -> list[dict[str, str]]:
        """List theater stamps with identities distinct from user stamp IDs."""
        root = self.stamps_dir().resolve()
        if not root.is_dir():
            return []
        stamps: list[dict[str, str]] = []
        for image in sorted(root.rglob("*")):
            if not image.is_file() or root not in image.resolve().parents:
                continue
            if image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                continue
            filename = image.relative_to(root).as_posix()
            stamps.append({
                "id": f"theater:{self.theater_id}:{filename}",
                "name": image.stem,
                "url": f"/theaters/{quote(self.theater_id, safe='')}/stamps/{quote(filename, safe='/')}",
            })
        return stamps

    def playlists_dir(self) -> Path:
        return self.manager._get_theater_playlists_dir(self.theater_id)

    def lore_dir(self) -> Path:
        """Directory containing text-only story reference documents."""
        return self.manager._get_theater_lore_dir(self.theater_id)

    def output_dir(self) -> Path:
        return self.manager._get_theater_output_dir(self.theater_id)

    def characters_dir(self) -> Path:
        return self.manager._get_theater_characters_dir(self.theater_id)

    def canvas_captures_dir(self) -> Path:
        """Directory of observed canvas snapshots; usable as references, never displayable."""
        return self.output_dir() / "canvas_captures"

    def artifacts_dir(self) -> Path:
        return self.manager._get_theater_artifacts_dir(self.theater_id)

    def image_artifacts_dir(self) -> Path:
        return self.manager._get_theater_image_artifacts_dir(self.theater_id)

    def music_artifacts_dir(self) -> Path:
        return self.manager._get_theater_music_artifacts_dir(self.theater_id)

    def chats_dir(self) -> Path:
        """Directory containing exported chat session logs."""
        return self.manager._get_theater_chats_dir(self.theater_id)

    def config(self) -> Dict[str, Any]:
        """Retrieve and merge configuration for this theater."""
        return self.manager.get_theater_config(self.theater_id)

    def get_config(self) -> Dict[str, Any]:
        """Alias for config()."""
        return self.config()

    def get_url_for_path(self, file_path: str) -> str:
        return self.manager.get_url_for_path(self.theater_id, file_path)


    @property
    def metadata(self) -> Optional[TheaterMetadata]:
        return self.manager.get_theater(self.theater_id)

    def deploy(self) -> TheaterMetadata:
        return self.manager.deploy_theater(self.theater_id)

    def stop(self) -> TheaterMetadata:
        return self.manager.stop_theater(self.theater_id)

    def destroy(self) -> bool:
        return self.manager.destroy_theater(self.theater_id)

    def references(self) -> list[dict[str, str | int]]:
        return self.manager.get_theater_references(self.theater_id)

    def playlists(self) -> dict[str, list[dict[str, str | int]]]:
        return self.manager.get_theater_playlists(self.theater_id)

    def lore_documents(self) -> List[str]:
        return self.manager.get_lore_documents(self.theater_id)

    def read_lore_document(self, document: str) -> str:
        return self.manager.read_lore_document(self.theater_id, document)

    def read_output_file_lines(self, file_path: str) -> List[str]:
        return self.manager.read_output_file_lines(self.theater_id, file_path)

    def append_output_file(self, file_path: str, content: str | bytes) -> None:
        self.manager.append_output_file(self.theater_id, file_path, content)

    def planning_schema_path(self) -> Optional[Path]:
        for name in ("planning.yaml", "planning.yml"):
            p = self.directory() / name
            if p.is_file():
                return p
        return None

    def read_planning_schema(self) -> Optional[Dict[str, Any]]:
        path = self.planning_schema_path()
        if path:
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data if isinstance(data, dict) else None
            except Exception as e:
                logger.warning("Failed to load planning schema from %s: %s", path, e)
        return None


def extract_asset_package(
    zip_bytes: bytes, max_bytes: int = MAX_ZIP_BYTES,
) -> Tuple[List[Tuple[str, bytes]], Dict[str, List[Tuple[str, bytes]]], List[Tuple[str, bytes]], Optional[str]]:
    """Read supported assets, text-only lore, and an optional ``theater.yaml`` from a ZIP archive."""
    if len(zip_bytes) > max_bytes:
        raise ValueError(f"ZIP archive exceeds max allowed size of {max_bytes // (1024 * 1024)}MB.")

    reference_files: List[Tuple[str, bytes]] = []
    playlists_data: Dict[str, List[Tuple[str, bytes]]] = {}
    lore_files: List[Tuple[str, bytes]] = []
    theater_config_yaml: Optional[str] = None
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
            for info in archive.infolist():
                # Validate directory entries too, before any path classification.
                parts = validate_asset_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > max_bytes * 2:
                    raise ValueError("ZIP uncompressed size exceeds allowable limit.")
                parts = [part for part in parts if part != "__MACOSX"]
                if not parts or parts[-1].startswith("."):
                    continue
                filename, content = parts[-1], archive.read(info.filename)
                if filename.lower() in ("planning.yaml", "planning.yml"):
                    reference_files.append(("planning.yaml", content))
                elif "references" in parts or (filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) and "playlists" not in parts):
                    reference_files.append((info.filename, content))
                elif "playlists" in parts and filename.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                    index = parts.index("playlists")
                    playlist = parts[index + 1] if index + 1 < len(parts) - 1 else "default"
                    playlists_data.setdefault(playlist, []).append((filename, content))
                elif "lore" in parts and filename.lower().endswith(LORE_TEXT_EXTENSION):
                    if len(content) > MAX_LORE_DOCUMENT_BYTES:
                        raise ValueError(
                            f"Lore document '{filename}' exceeds the {MAX_LORE_DOCUMENT_BYTES // 1024}KB limit."
                        )
                    try:
                        content.decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError(f"Lore document '{filename}' must be UTF-8 encoded text.")
                    lore_files.append((info.filename, content))
                elif filename.lower() == "theater.yaml":
                    try:
                        theater_config_yaml = content.decode("utf-8")
                    except UnicodeDecodeError:
                        raise ValueError("theater.yaml must be UTF-8 encoded.")
    except ValueError:
        raise
    except Exception as error:
        logger.error("Error parsing asset ZIP package: %s", error)
    return reference_files, playlists_data, lore_files, theater_config_yaml


class TheaterManager:
    """Own theater workspace creation, lifecycle metadata, and asset lookup."""

    def __init__(self, base_theaters_dir: Optional[str | Path] = None):
        self.base_dir = Path(base_theaters_dir).resolve() if base_theaters_dir else ensure_ephemeral_root()
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

    def _get_theater_lore_dir(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "lore"

    def _get_theater_output_dir(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "output"

    def _get_theater_characters_dir(self, theater_id: str) -> Path:
        return self._get_theater_output_dir(theater_id) / "characters"

    def _get_theater_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_output_dir(theater_id) / "artifacts"

    def _get_theater_image_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_artifacts_dir(theater_id) / "images"

    def _get_theater_music_artifacts_dir(self, theater_id: str) -> Path:
        return self._get_theater_output_dir(theater_id) / "music"

    def _get_theater_chats_dir(self, theater_id: str) -> Path:
        return self._get_theater_output_dir(theater_id) / "chats"

    def get_theater_config(self, theater_id: str) -> Dict[str, Any]:
        """Retrieve and merge configuration for a specific theater."""
        from utils.config_loader import get_theater_config

        return get_theater_config(theater_id, theater_manager=self)

    def get_url_for_path(self, theater_id: str, file_path: str) -> str:
        if not file_path:
            return ""
        path_obj = Path(file_path)
        if not path_obj.is_absolute() and path_obj.parts and path_obj.parts[0] == "references":
            sel_path_obj = (self._get_theater_dir(theater_id) / path_obj).resolve()
        elif not path_obj.is_absolute():
            sel_path_obj = (self._get_theater_output_dir(theater_id) / path_obj).resolve()
        else:
            sel_path_obj = path_obj.resolve()

        if "references" in sel_path_obj.parts or "reference_library" in sel_path_obj.parts:
            if "references" in sel_path_obj.parts:
                try:
                    relative_reference = sel_path_obj.relative_to(self._get_theater_reference_dir(theater_id).resolve()).as_posix()
                except ValueError:
                    reference_index = len(sel_path_obj.parts) - 1 - sel_path_obj.parts[::-1].index("references")
                    relative_reference = Path(*sel_path_obj.parts[reference_index + 1:]).as_posix()
                return f"/theaters/{theater_id}/references/{quote(relative_reference, safe='/')}"
            return f"/theaters/{theater_id}/references/{quote(sel_path_obj.name)}"

        output_dir = self._get_theater_output_dir(theater_id).resolve()
        try:
            relative_path = sel_path_obj.relative_to(output_dir).as_posix()
        except ValueError:
            parts = sel_path_obj.parts
            if "output" in parts:
                output_idx = len(parts) - 1 - parts[::-1].index("output")
                relative_path = Path(*parts[output_idx + 1:]).as_posix()
            else:
                relative_path = sel_path_obj.name
        return f"/theaters/{theater_id}/output/{relative_path}"


    def _metadata_path(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "theater.json"

    def _save_metadata(self, metadata: TheaterMetadata) -> None:
        theater_dir = self._get_theater_dir(metadata.theater_id)
        theater_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path(metadata.theater_id).write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

    def update_theater_name(self, theater_id: str, new_name: str) -> Optional[TheaterMetadata]:
        metadata = self.get_theater(theater_id)
        if not metadata:
            return None
        metadata.name = new_name
        self._save_metadata(metadata)
        return metadata

    def record_theater_connected(
        self, theater_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Persist a connection timestamp and return prior connection lifecycle times."""
        metadata = self.get_theater(theater_id)
        if metadata is None:
            return None, None
        previous = (metadata.last_connected_at, metadata.last_disconnected_at)
        metadata.last_connected_at = datetime.now(timezone.utc).isoformat()
        self._save_metadata(metadata)
        return previous

    def record_theater_disconnected(self, theater_id: str) -> None:
        """Persist the time at which the last active theater connection ended."""
        metadata = self.get_theater(theater_id)
        if metadata is None:
            return
        metadata.last_disconnected_at = datetime.now(timezone.utc).isoformat()
        self._save_metadata(metadata)

    def record_auto_begin(self, theater_id: str) -> Optional[str]:
        """Persist and return the time at which Adventure Mode auto-began."""
        metadata = self.get_theater(theater_id)
        if metadata is None:
            return None
        metadata.last_auto_begin_at = datetime.now(timezone.utc).isoformat()
        self._save_metadata(metadata)
        return metadata.last_auto_begin_at

    def create_theater(self, name: str, theater_id: str, reference_files: Optional[List[tuple[str, bytes]]] = None, playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None, lore_files: Optional[List[tuple[str, bytes]]] = None, theater_config: Optional[Dict] = None, metadata_json: Optional[Any] = None) -> TheaterMetadata:
        # Import lazily so config loading can reuse the theater-root helper.
        from utils.config_loader import get_theater_default_config, save_theater_config

        # Validate the whole batch before creating a workspace or writing assets.
        theater_parts = validate_asset_path(theater_id)
        if len(theater_parts) != 1:
            raise ValueError("Theater ID must be a single path component.")
        theater_dir = asset_destination(self.base_dir, theater_id)
        if theater_dir.exists():
            raise ValueError(f"Theater with ID '{theater_id}' already exists.")
        reference_dir = theater_dir / "references"
        playlists_dir = theater_dir / "playlists"
        lore_dir = theater_dir / "lore"
        for filename, content in reference_files or []:
            parts = validate_asset_path(filename)
            if "stamps" in parts or parts[-1].lower() in {"metadata.json", "planning.yaml", "planning.yml"}:
                if "stamps" in parts:
                    asset_destination(theater_dir / "stamps", "/".join(parts[parts.index("stamps") + 1:]))
                    if Path(parts[-1]).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                        raise ValueError("Unsupported stamp image format")
                continue
            relative_name = "/".join(parts[parts.index("references") + 1:]) if "references" in parts else parts[-1]
            asset_destination(reference_dir, relative_name)
            reference_image_type(filename, content)
        for playlist_name, files in (playlists_data or {}).items():
            if len(validate_asset_path(playlist_name)) != 1:
                raise ValueError("Playlist names must be a single path component.")
            playlist_dir = asset_destination(playlists_dir, playlist_name)
            for filename, _ in files:
                parts = validate_asset_path(filename)
                asset_destination(playlist_dir, parts[-1])
        for filename, content in lore_files or []:
            parts = validate_asset_path(filename)
            relative_name = "/".join(parts[parts.index("lore") + 1:]) if "lore" in parts else parts[-1]
            asset_destination(lore_dir, relative_name)
            if not parts[-1].lower().endswith(LORE_TEXT_EXTENSION):
                raise ValueError("Lore documents must be .txt files.")
            if len(content) > MAX_LORE_DOCUMENT_BYTES:
                raise ValueError(f"Lore documents must be at most {MAX_LORE_DOCUMENT_BYTES // 1024}KB.")
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("Lore documents must be UTF-8 encoded text.") from error
        reference_dir.mkdir(parents=True)
        playlists_dir.mkdir()
        lore_dir.mkdir()
        (theater_dir / "stamps").mkdir()
        self._get_theater_output_dir(theater_id).mkdir()

        if metadata_json is not None:
            meta_file = asset_destination(theater_dir, "metadata.json")
            if isinstance(metadata_json, dict):
                meta_file.write_text(json.dumps(metadata_json, indent=2), encoding="utf-8")
            elif isinstance(metadata_json, str):
                meta_file.write_text(metadata_json, encoding="utf-8")
            elif isinstance(metadata_json, (bytes, bytearray)):
                meta_file.write_bytes(metadata_json)

        mounted_references = []
        for relative_filename, content in reference_files or []:
            parts = validate_asset_path(relative_filename)
            if "stamps" in parts:
                stamp_path = asset_destination(theater_dir / "stamps", "/".join(parts[parts.index("stamps") + 1:]))
                stamp_path.parent.mkdir(parents=True, exist_ok=True)
                stamp_path.write_bytes(content)
                continue
            if relative_filename == "metadata.json" or (parts and parts[-1].lower() == "metadata.json"):
                asset_destination(theater_dir, "metadata.json").write_bytes(content)
                continue
            if relative_filename in ("planning.yaml", "planning.yml") or (parts and parts[-1].lower() in ("planning.yaml", "planning.yml")):
                asset_destination(theater_dir, "planning.yaml").write_bytes(content)
                continue
            relative_path = Path(*parts[parts.index("references") + 1:]) if "references" in parts else Path(parts[-1])
            target = asset_destination(reference_dir, relative_path.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            mounted_references.append(relative_path.as_posix())
        default_references = Path(__file__).parent.parent / "reference_library"
        if default_references.exists():
            for reference in default_references.iterdir():
                if reference.is_file() and reference.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    target = asset_destination(reference_dir, reference.name)
                    if not target.exists():
                        shutil.copy2(reference, target)
                        mounted_references.append(reference.name)

        mounted_playlists: Dict[str, List[str]] = {}
        for playlist_name, files in (playlists_data or {}).items():
            playlist_dir = asset_destination(playlists_dir, playlist_name)
            playlist_dir.mkdir(parents=True, exist_ok=True)
            mounted_playlists[playlist_name] = []
            for filename, content in files:
                clean_name = validate_asset_path(filename)[-1]
                asset_destination(playlist_dir, clean_name).write_bytes(content)
                mounted_playlists[playlist_name].append(clean_name)

        for relative_filename, content in lore_files or []:
            parts = validate_asset_path(relative_filename)
            relative_path = Path(*parts[parts.index("lore") + 1:]) if "lore" in parts else Path(parts[-1])
            if not relative_path.parts:
                continue
            target = asset_destination(lore_dir, relative_path.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        if theater_config is not None:
            config = deepcopy(theater_config)
        else:
            config = get_theater_default_config()
        save_theater_config(theater_id, config, theater_manager=self)
        metadata = TheaterMetadata(theater_id=theater_id, name=name, mounted_references=mounted_references, mounted_playlists=mounted_playlists, config=config)
        self._save_metadata(metadata)
        return metadata

    def get_theater(self, theater_id: str) -> Optional[TheaterMetadata]:
        metadata_path = self._metadata_path(theater_id)
        if not metadata_path.exists():
            return None
        metadata_data = json.loads(metadata_path.read_text(encoding="utf-8"))

        # Early canvas persistence wrote a canvas-only theater.json for some
        # restored deployments.  Normalize those legacy records before
        # validating so an agent restart cannot be blocked by missing identity
        # fields.  Use the directory ID as the safe fallback display name when
        # the original name was not persisted.
        if not metadata_data.get("theater_id"):
            metadata_data["theater_id"] = theater_id
        if not metadata_data.get("name"):
            metadata_data["name"] = theater_id

        metadata = TheaterMetadata.model_validate(metadata_data)
        self._save_metadata(metadata)
        return metadata

    def get_lore_documents(self, theater_id: str) -> List[str]:
        """List text documents available to the story planner for a theater."""
        lore_dir = self._get_theater_lore_dir(theater_id)
        if not lore_dir.is_dir():
            return []
        return sorted(
            path.relative_to(lore_dir).as_posix()
            for path in lore_dir.rglob(f"*{LORE_TEXT_EXTENSION}")
            if path.is_file()
        )

    def read_lore_document(self, theater_id: str, document: str) -> str:
        """Read one UTF-8 ``.txt`` lore document without leaving its theater workspace."""
        lore_dir = self._get_theater_lore_dir(theater_id).resolve()
        requested = Path(str(document or "").replace("\\", "/"))
        if not document or requested.is_absolute() or requested.suffix.lower() != LORE_TEXT_EXTENSION:
            raise ValueError("Lore documents must be relative .txt paths.")
        target = (lore_dir / requested).resolve()
        if lore_dir not in target.parents or not target.is_file():
            raise ValueError("Lore document was not found.")
        if target.stat().st_size > MAX_LORE_DOCUMENT_BYTES:
            raise ValueError(f"Lore documents must be at most {MAX_LORE_DOCUMENT_BYTES // 1024}KB.")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Lore documents must be UTF-8 encoded text.") from error

    def read_output_file_lines(self, theater_id: str, file_path: str) -> List[str]:
        """Read lines from a file in the theater output directory."""
        output_dir = self._get_theater_output_dir(theater_id).resolve()
        requested = Path(str(file_path or "").replace("\\", "/"))
        if not file_path or requested.is_absolute():
            raise ValueError("Output file path must be a relative path.")
        target = (output_dir / requested).resolve()
        if output_dir not in target.parents:
            raise ValueError("Output file path cannot escape the output directory.")
        if not target.is_file():
            return []
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except OSError as error:
            logger.warning("Failed to read output file %s: %s", target, error)
            return []

    def append_output_file(self, theater_id: str, file_path: str, content: str | bytes) -> None:
        """Append text or bytes to a file in the theater output directory."""
        output_dir = self._get_theater_output_dir(theater_id).resolve()
        requested = Path(str(file_path or "").replace("\\", "/"))
        if not file_path or requested.is_absolute():
            raise ValueError("Output file path must be a relative path.")
        target = (output_dir / requested).resolve()
        if output_dir not in target.parents:
            raise ValueError("Output file path cannot escape the output directory.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (bytes, bytearray)):
            with open(target, "ab") as f:
                f.write(content)
        else:
            with open(target, "a", encoding="utf-8") as f:
                f.write(str(content))

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

    def get_theater_references(self, theater_id: str) -> list[dict[str, str | int]]:
        reference_dir = self._get_theater_reference_dir(theater_id)
        if not reference_dir.exists():
            return []
        references: list[dict[str, str | int]] = []
        for file in sorted(reference_dir.rglob("*")):
            if not file.is_file() or reference_dir.resolve() not in file.resolve().parents or file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                continue
            filename = file.relative_to(reference_dir).as_posix()
            references.append({"name": file.stem, "filename": filename, "url": f"/theaters/{theater_id}/references/{quote(filename, safe='/')}", "size_bytes": file.stat().st_size})
        return references

    def get_theater_playlists(self, theater_id: str) -> dict[str, list[dict[str, str | int]]]:
        playlists_dir = self._get_theater_playlists_dir(theater_id)
        if not playlists_dir.exists():
            return {}
        playlists: dict[str, list[dict[str, str | int]]] = {}
        for directory in sorted(playlists_dir.iterdir()):
            if not directory.is_dir() or playlists_dir.resolve() not in directory.resolve().parents:
                continue
            tracks: list[dict[str, str | int]] = []
            for track in sorted(directory.rglob("*")):
                if not track.is_file() or directory.resolve() not in track.resolve().parents or track.suffix.lower() not in {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}:
                    continue
                filename = track.relative_to(directory).as_posix()
                tracks.append({"filename": filename, "url": f"/theaters/{theater_id}/playlists/{quote(directory.name)}/{quote(filename, safe='/')}", "size_bytes": track.stat().st_size})
            playlists[directory.name] = tracks
        return playlists
