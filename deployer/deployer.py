"""Deployer module defining BaseDeployer interface and LocalDeployer implementation."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import shutil
import secrets
from typing import Dict, List, Optional, Tuple
import zipfile
from pydantic import BaseModel, Field
from utils.theaters_paths import ensure_theaters_root
from utils.config_loader import save_theater_config, get_theater_default_config

logger = logging.getLogger(__name__)


class TheaterMetadata(BaseModel):
    """Metadata representing a single canvas theater deployment."""
    theater_id: str
    name: str
    status: str = "created"  # created, deployed, stopped
    join_key: str = Field(default_factory=lambda: f"KEY-{''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mounted_references: List[str] = Field(default_factory=list)
    mounted_playlists: Dict[str, List[str]] = Field(default_factory=dict)
    config: Dict = Field(default_factory=dict)
    canvas_state: Dict = Field(default_factory=dict)
    allowed_orators: List[int] = Field(default_factory=list)
    active_orator_id: Optional[int] = None
    baton_request: Optional[Dict] = None




MAX_ZIP_BYTES = 10 * 1024 * 1024  # 10MB limit


def extract_asset_package(
    zip_bytes: bytes,
    max_bytes: int = MAX_ZIP_BYTES,
) -> Tuple[List[Tuple[str, bytes]], Dict[str, List[Tuple[str, bytes]]], Optional[str]]:
    """Extract reference files, playlists data, and optional style text from a ZIP archive (max 10MB)."""
    reference_files: List[Tuple[str, bytes]] = []
    playlists_data: Dict[str, List[Tuple[str, bytes]]] = {}
    style: Optional[str] = None

    if len(zip_bytes) > max_bytes:
        logger.warning(f"ZIP archive size ({len(zip_bytes)} bytes) exceeds limit ({max_bytes} bytes).")
        raise ValueError(f"ZIP archive exceeds max allowed size of {max_bytes // (1024 * 1024)}MB.")

    total_uncompressed = 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                total_uncompressed += info.file_size
                if total_uncompressed > max_bytes * 2:
                    logger.warning("Uncompressed ZIP contents exceed max allowable size limit.")
                    raise ValueError("ZIP uncompressed size exceeds allowable limit.")

                parts = [
                    p
                    for p in info.filename.replace("\\", "/").split("/")
                    if p and p != "__MACOSX"
                ]
                if not parts or parts[-1].startswith("."):
                    continue

                filename = parts[-1]
                content = zf.read(info.filename)

                # Check path hierarchy
                if "references" in parts or (
                    filename.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".webp", ".gif")
                    )
                    and "playlists" not in parts
                ):
                    reference_files.append((info.filename, content))
                elif "playlists" in parts:
                    idx = parts.index("playlists")
                    if idx + 1 < len(parts) - 1:
                        pl_name = parts[idx + 1]
                    else:
                        pl_name = "default"
                    if filename.lower().endswith(
                        (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
                    ):
                        if pl_name not in playlists_data:
                            playlists_data[pl_name] = []
                        playlists_data[pl_name].append((filename, content))
                elif filename.lower() == "style.txt":
                    try:
                        style = content.decode("utf-8").strip()
                    except Exception:
                        pass
    except ValueError as ve:
        raise ve
    except Exception as e:
        logger.error(f"Error parsing asset ZIP package: {e}")

    return reference_files, playlists_data, style


class BaseDeployer(ABC):
    """Abstract interface for theater deployers (Local, Hosted, Cloud)."""
    def create_theater(
        self,
        name: str,
        theater_id: str,
        reference_files: Optional[List[tuple[str, bytes]]] = None,
        playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None,
        theater_config: Optional[Dict] = None,
        style: Optional[str] = None,
    ) -> TheaterMetadata:
        """Create a new theater instance with mounted reference images and playlists."""
        pass

    @abstractmethod
    def deploy_theater(self, theater_id: str) -> TheaterMetadata:
        """Deploy/activate a created theater instance."""
        pass

    @abstractmethod
    def get_theater(self, theater_id: str) -> Optional[TheaterMetadata]:
        """Retrieve theater metadata by ID."""
        pass

    @abstractmethod
    def list_theaters(self) -> List[TheaterMetadata]:
        """List all active or saved theater instances."""
        pass

    @abstractmethod
    def stop_theater(self, theater_id: str) -> TheaterMetadata:
        """Stop/deactivate a running theater instance."""
        pass

    @abstractmethod
    def destroy_theater(self, theater_id: str) -> bool:
        """Completely remove and clean up a theater instance."""
        pass


class LocalDeployer(BaseDeployer):
    """Deployer implementation that manages canvas theaters locally on the filesystem."""

    def __init__(self, base_theaters_dir: Optional[str] = None):
        if base_theaters_dir:
            self.base_dir = Path(base_theaters_dir).resolve()
        else:
            self.base_dir = ensure_theaters_root()
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalDeployer initialized at base directory: {self.base_dir}")

    def _get_theater_dir(self, theater_id: str) -> Path:
        return self.base_dir / theater_id

    def _get_metadata_path(self, theater_id: str) -> Path:
        return self._get_theater_dir(theater_id) / "theater.json"

    def _save_metadata(self, metadata: TheaterMetadata) -> None:
        theater_dir = self._get_theater_dir(metadata.theater_id)
        theater_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self._get_metadata_path(metadata.theater_id)
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(metadata.model_dump_json(indent=2))

    def create_theater(
        self,
        name: str,
        theater_id: str,
        reference_files: Optional[List[tuple[str, bytes]]] = None,
        playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None,
        theater_config: Optional[Dict] = None,
        style: Optional[str] = None,
    ) -> TheaterMetadata:
        """Create local theater workspace and mount reference assets & playlists."""
        import uuid
        sid = theater_id
        theater_dir = self._get_theater_dir(sid)
        
        if theater_dir.exists():
            raise ValueError(f"Theater with ID '{sid}' already exists.")

        ref_dir = theater_dir / "references"
        playlists_dir = theater_dir / "playlists"
        output_dir = theater_dir / "output"

        ref_dir.mkdir(parents=True, exist_ok=True)
        playlists_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        if style and style.strip():
            (theater_dir / "style.txt").write_text(style.strip(), encoding="utf-8")

        mounted_refs = []
        if reference_files:
            for rel_filename, content in reference_files:
                rel_path = rel_filename.replace("\\", "/")
                parts = [p for p in rel_path.split("/") if p]
                if "references" in parts:
                    idx = parts.index("references")
                    sub_path = Path(*parts[idx + 1:]) if idx + 1 < len(parts) else Path(parts[-1])
                else:
                    sub_path = Path(parts[-1])
                file_path = ref_dir / sub_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(content)
                mounted_refs.append(str(sub_path).replace("\\", "/"))

        # Copy default reference images from top-level reference_library if present
        import shutil
        top_level_ref_dir = (Path(__file__).parent.parent / "reference_library").resolve()
        if top_level_ref_dir.exists():
            for ref_file in top_level_ref_dir.iterdir():
                if ref_file.is_file() and ref_file.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                    dest_file = ref_dir / ref_file.name
                    if not dest_file.exists():
                        shutil.copy2(ref_file, dest_file)
                        if ref_file.name not in mounted_refs:
                            mounted_refs.append(ref_file.name)

        mounted_playlists: Dict[str, List[str]] = {}
        if playlists_data:
            for playlist_name, files in playlists_data.items():
                pl_dir = playlists_dir / playlist_name
                pl_dir.mkdir(parents=True, exist_ok=True)
                mounted_playlists[playlist_name] = []
                for filename, content in files:
                    clean_name = Path(filename).name
                    file_path = pl_dir / clean_name
                    with open(file_path, "wb") as f:
                        f.write(content)
                    mounted_playlists[playlist_name].append(clean_name)

        final_config = get_theater_default_config()
        if theater_config:
            from utils.config_loader import deep_merge
            deep_merge(final_config, theater_config)

        save_theater_config(sid, final_config, base_dir=self.base_dir)

        metadata = TheaterMetadata(
            theater_id=sid,
            name=name,
            status="created",
            mounted_references=mounted_refs,
            mounted_playlists=mounted_playlists,
            config=final_config,
        )

        self._save_metadata(metadata)
        logger.info(f"Created local theater '{sid}' with {len(mounted_refs)} reference files and {len(mounted_playlists)} playlists.")
        return metadata

    def deploy_theater(self, theater_id: str) -> TheaterMetadata:
        """Mark theater status as deployed and ready for canvas connections."""
        metadata = self.get_theater(theater_id)
        if not metadata:
            raise FileNotFoundError(f"Theater '{theater_id}' not found.")

        metadata.status = "deployed"
        self._save_metadata(metadata)
        logger.info(f"Theater '{theater_id}' status updated to deployed.")
        return metadata

    def get_theater(self, theater_id: str) -> Optional[TheaterMetadata]:
        meta_path = self._get_metadata_path(theater_id)
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return TheaterMetadata(**data)

    def list_theaters(self) -> List[TheaterMetadata]:
        theaters = []
        if not self.base_dir.exists():
            return theaters
        for entry in self.base_dir.iterdir():
            if entry.is_dir():
                meta_path = entry / "theater.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            theaters.append(TheaterMetadata(**data))
                    except Exception as e:
                        logger.error(f"Error reading metadata for theater in {entry}: {e}")
        theaters.sort(key=lambda s: s.created_at, reverse=True)
        return theaters

    def stop_theater(self, theater_id: str) -> TheaterMetadata:
        metadata = self.get_theater(theater_id)
        if not metadata:
            raise FileNotFoundError(f"Theater '{theater_id}' not found.")
        metadata.status = "stopped"
        self._save_metadata(metadata)
        logger.info(f"Theater '{theater_id}' status set to stopped.")
        return metadata

    def destroy_theater(self, theater_id: str) -> bool:
        theater_dir = self._get_theater_dir(theater_id)
        if theater_dir.exists():
            shutil.rmtree(theater_dir)
            logger.info(f"Theater '{theater_id}' directory removed.")
            return True
        return False
