"""Deployer module defining BaseDeployer interface and LocalDeployer implementation."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SessionMetadata(BaseModel):
    """Metadata representing a single canvas session deployment."""
    session_id: str
    name: str
    status: str = "created"  # created, deployed, stopped
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mounted_references: List[str] = Field(default_factory=list)
    mounted_playlists: Dict[str, List[str]] = Field(default_factory=dict)
    config: Dict = Field(default_factory=dict)


class BaseDeployer(ABC):
    """Abstract interface for session deployers (Local, Hosted, Cloud)."""

    @abstractmethod
    def create_session(
        self,
        name: str,
        reference_files: Optional[List[tuple[str, bytes]]] = None,
        playlists_data: Optional[Dict[str, List[tuple[str, bytes]]]] = None,
        session_config: Optional[Dict] = None,
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
            self.base_dir = (Path(__file__).parent.parent / "sessions").resolve()
        
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
        session_id: Optional[str] = None,
    ) -> SessionMetadata:
        """Create local session workspace and mount reference assets & playlists."""
        import uuid
        sid = session_id or f"session_{uuid.uuid4().hex[:8]}"
        session_dir = self._get_session_dir(sid)
        
        if session_dir.exists():
            raise ValueError(f"Session with ID '{sid}' already exists.")

        ref_dir = session_dir / "reference_library"
        playlists_dir = session_dir / "playlists"
        output_dir = session_dir / "output"

        ref_dir.mkdir(parents=True, exist_ok=True)
        playlists_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        mounted_refs = []
        if reference_files:
            for filename, content in reference_files:
                clean_name = Path(filename).name
                file_path = ref_dir / clean_name
                with open(file_path, "wb") as f:
                    f.write(content)
                mounted_refs.append(clean_name)

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
