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
from utils.session_paths import ensure_sessions_root

logger = logging.getLogger(__name__)


class SessionMetadata(BaseModel):
    """Metadata representing a single canvas session deployment."""
    session_id: str
    name: str
    status: str = "created"  # created, deployed, stopped
    join_key: str = Field(default_factory=lambda: f"KEY-{''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mounted_references: List[str] = Field(default_factory=list)
    mounted_playlists: Dict[str, List[str]] = Field(default_factory=dict)
    config: Dict = Field(default_factory=dict)
    canvas_state: Dict = Field(default_factory=dict)


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
                    reference_files.append((filename, content))
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
    """Abstract interface for session deployers (Local, Hosted, Cloud)."""
    def create_session(
        self,
        name: str,
        reference_files: Optional[List[tuple[str, bytes]]] = None,
        playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None,
        session_config: Optional[Dict] = None,
        style: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> SessionMetadata:
        """Create a new session instance with mounted reference images and playlists."""
        pass

    @abstractmethod
    def deploy_session(self, session_id: str) -> SessionMetadata:
        """Deploy/activate a created session instance."""
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[SessionMetadata]:
        """Retrieve session metadata by ID."""
        pass

    @abstractmethod
    def list_sessions(self) -> List[SessionMetadata]:
        """List all active or saved session instances."""
        pass

    @abstractmethod
    def stop_session(self, session_id: str) -> SessionMetadata:
        """Stop/deactivate a running session instance."""
        pass

    @abstractmethod
    def destroy_session(self, session_id: str) -> bool:
        """Completely remove and clean up a session instance."""
        pass


class LocalDeployer(BaseDeployer):
    """Deployer implementation that manages canvas sessions locally on the filesystem."""

    def __init__(self, base_sessions_dir: Optional[str] = None):
        if base_sessions_dir:
            self.base_dir = Path(base_sessions_dir).resolve()
        else:
            self.base_dir = ensure_sessions_root()
        
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalDeployer initialized at base directory: {self.base_dir}")

    def _get_session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _get_metadata_path(self, session_id: str) -> Path:
        return self._get_session_dir(session_id) / "session.json"

    def _save_metadata(self, metadata: SessionMetadata) -> None:
        session_dir = self._get_session_dir(metadata.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self._get_metadata_path(metadata.session_id)
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(metadata.model_dump_json(indent=2))

    def create_session(
        self,
        name: str,
        reference_files: Optional[List[tuple[str, bytes]]] = None,
        playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None,
        session_config: Optional[Dict] = None,
        style: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> SessionMetadata:
        """Create local session workspace and mount reference assets & playlists."""
        import uuid
        sid = session_id or f"session_{uuid.uuid4().hex[:8]}"
        session_dir = self._get_session_dir(sid)
        
        if session_dir.exists():
            raise ValueError(f"Session with ID '{sid}' already exists.")

        ref_dir = session_dir / "references"
        playlists_dir = session_dir / "playlists"
        output_dir = session_dir / "output"

        ref_dir.mkdir(parents=True, exist_ok=True)
        playlists_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        if style and style.strip():
            (session_dir / "style.txt").write_text(style.strip(), encoding="utf-8")

        mounted_refs = []
        if reference_files:
            for filename, content in reference_files:
                clean_name = Path(filename).name
                file_path = ref_dir / clean_name
                with open(file_path, "wb") as f:
                    f.write(content)
                mounted_refs.append(clean_name)

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

        metadata = SessionMetadata(
            session_id=sid,
            name=name,
            status="created",
            mounted_references=mounted_refs,
            mounted_playlists=mounted_playlists,
            config=session_config or {},
        )

        self._save_metadata(metadata)
        logger.info(f"Created local session '{sid}' with {len(mounted_refs)} reference files and {len(mounted_playlists)} playlists.")
        return metadata

    def deploy_session(self, session_id: str) -> SessionMetadata:
        """Mark session status as deployed and ready for canvas connections."""
        metadata = self.get_session(session_id)
        if not metadata:
            raise FileNotFoundError(f"Session '{session_id}' not found.")

        metadata.status = "deployed"
        self._save_metadata(metadata)
        logger.info(f"Session '{session_id}' status updated to deployed.")
        return metadata

    def get_session(self, session_id: str) -> Optional[SessionMetadata]:
        meta_path = self._get_metadata_path(session_id)
        if not meta_path.exists():
            return None
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return SessionMetadata(**data)

    def list_sessions(self) -> List[SessionMetadata]:
        sessions = []
        if not self.base_dir.exists():
            return sessions
        for entry in self.base_dir.iterdir():
            if entry.is_dir():
                meta_path = entry / "session.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            sessions.append(SessionMetadata(**data))
                    except Exception as e:
                        logger.error(f"Error reading metadata for session in {entry}: {e}")
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def stop_session(self, session_id: str) -> SessionMetadata:
        metadata = self.get_session(session_id)
        if not metadata:
            raise FileNotFoundError(f"Session '{session_id}' not found.")
        metadata.status = "stopped"
        self._save_metadata(metadata)
        logger.info(f"Session '{session_id}' status set to stopped.")
        return metadata

    def destroy_session(self, session_id: str) -> bool:
        session_dir = self._get_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)
            logger.info(f"Session '{session_id}' directory removed.")
            return True
        return False
