"""Session Manager providing dynamic asset path resolution and tool context scoping."""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from deployer.deployer import LocalDeployer, SessionMetadata

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages active session state, asset resolution, and dynamic route mounting."""

    def __init__(self, deployer: Optional[LocalDeployer] = None):
        self.deployer = deployer or LocalDeployer()

    def get_session_dir(self, narratron_session_id: str) -> Path:
        return self.deployer._get_session_dir(narratron_session_id)

    def get_session_reference_dir(self, narratron_session_id: str) -> Path:
        ref_dir = self.get_session_dir(narratron_session_id) / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        return ref_dir

    def get_session_playlists_dir(self, narratron_session_id: str) -> Path:
        pl_dir = self.get_session_dir(narratron_session_id) / "playlists"
        pl_dir.mkdir(parents=True, exist_ok=True)
        return pl_dir

    def get_session_output_dir(self, narratron_session_id: str) -> Path:
        out_dir = self.get_session_dir(narratron_session_id) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def get_session_references(self, narratron_session_id: str) -> List[Dict[str, str]]:
        """List reference image details with dynamic serve URLs for a session."""
        ref_dir = self.get_session_reference_dir(narratron_session_id)
        references = []
        if ref_dir.exists():
            for file in ref_dir.iterdir():
                if file.is_file() and file.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                    references.append({
                        "name": file.stem,
                        "filename": file.name,
                        "url": f"/sessions/{narratron_session_id}/references/{file.name}",
                        "size_bytes": file.stat().st_size,
                    })
        return references

    def get_session_playlists(self, narratron_session_id: str) -> Dict[str, List[Dict[str, str]]]:
        """List playlists and track details with dynamic serve URLs for a session."""
        playlists_dir = self.get_session_playlists_dir(narratron_session_id)
        result: Dict[str, List[Dict[str, str]]] = {}
        if playlists_dir.exists():
            for pl_folder in playlists_dir.iterdir():
                if pl_folder.is_dir():
                    playlist_name = pl_folder.name
                    tracks = []
                    for track in pl_folder.iterdir():
                        if track.is_file() and track.suffix.lower() in [".mp3", ".wav", ".ogg", ".m4a"]:
                            tracks.append({
                                "filename": track.name,
                                "url": f"/sessions/{narratron_session_id}/playlists/{playlist_name}/{track.name}",
                                "size_bytes": track.stat().st_size,
                            })
                    result[playlist_name] = tracks
        return result
