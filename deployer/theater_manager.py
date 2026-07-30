"""theater Manager providing dynamic asset path resolution and tool context scoping."""

import logging
from pathlib import Path
from typing import Dict, List, Optional
from deployer.deployer import LocalDeployer, TheaterMetadata

logger = logging.getLogger(__name__)


class TheaterManager:
    """Manages active theater state, asset resolution, and dynamic route mounting."""

    def __init__(self, deployer: Optional[LocalDeployer] = None):
        self.deployer = deployer or LocalDeployer()

    def get_theater_dir(self, theater_id: str) -> Path:
        return self.deployer._get_theater_dir(theater_id)

    def get_theater_reference_dir(self, theater_id: str) -> Path:
        ref_dir = self.get_theater_dir(theater_id) / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        return ref_dir

    def get_theater_playlists_dir(self, theater_id: str) -> Path:
        pl_dir = self.get_theater_dir(theater_id) / "playlists"
        pl_dir.mkdir(parents=True, exist_ok=True)
        return pl_dir

    def get_theater_output_dir(self, theater_id: str) -> Path:
        out_dir = self.get_theater_dir(theater_id) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def get_theater_references(self, theater_id: str) -> List[Dict[str, str]]:
        """List reference image details with dynamic serve URLs for a theater."""
        ref_dir = self.get_theater_reference_dir(theater_id)
        references = []
        if ref_dir.exists():
            for file in ref_dir.iterdir():
                if file.is_file() and file.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                    references.append({
                        "name": file.stem,
                        "filename": file.name,
                        "url": f"/theaters/{theater_id}/references/{file.name}",
                        "size_bytes": file.stat().st_size,
                    })
        return references

    def get_theater_playlists(self, theater_id: str) -> Dict[str, List[Dict[str, str]]]:
        """List playlists and track details with dynamic serve URLs for a theater."""
        playlists_dir = self.get_theater_playlists_dir(theater_id)
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
                                "url": f"/theaters/{theater_id}/playlists/{playlist_name}/{track.name}",
                                "size_bytes": track.stat().st_size,
                            })
                    result[playlist_name] = tracks
        return result
