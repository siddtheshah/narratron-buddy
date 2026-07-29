import glob
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.base_tool import BaseTools, with_cooldown
from utils.session_paths import ensure_sessions_root

logger = logging.getLogger(__name__)

class MusicTools(BaseTools):
    def __init__(self, config: dict, session_id: str, canvas_state_service: Any = None):
        raw_config = config or {}
        subconfig = raw_config.get("music", raw_config) if "music" in raw_config else raw_config
        super().__init__(
            config=subconfig,
            session_id=session_id,
            canvas_state_service=canvas_state_service,
            default_cooldown=60.0,
        )
        sessions_root = ensure_sessions_root()
        self.session_playlists_dir = str((sessions_root / self.active_session_id / "playlists").resolve())
        os.makedirs(self.session_playlists_dir, exist_ok=True)

        self.on_play_playlist: Optional[Callable[[str, List[str]], None]] = None
        self.on_pause_playlist: Optional[Callable[[], None]] = None
        self.on_resume_playlist: Optional[Callable[[], None]] = None

    @property
    def playlists_folder(self) -> str:
        return self.session_playlists_dir

    @playlists_folder.setter
    def playlists_folder(self, val: str) -> None:
        self.session_playlists_dir = str(val)

    def list_playlists(self) -> str:
        """List all available music playlists, their descriptions, and the tracks inside them.

        Returns:
            A formatted string of all available playlists, descriptions, and tracks.
        """
        try:
            if not os.path.exists(self.session_playlists_dir):
                return "No playlists folder found."

            subdirs = [d for d in os.listdir(self.session_playlists_dir)
                       if os.path.isdir(os.path.join(self.session_playlists_dir, d))]

            if not subdirs:
                return "No playlists found. Please add playlist subfolders in the playlists directory."

            result = []
            for subdir in sorted(subdirs):
                path = os.path.join(self.session_playlists_dir, subdir)
                desc_path = os.path.join(path, "description.txt")
                desc = "No description available."
                if os.path.exists(desc_path):
                    with open(desc_path, "r", encoding="utf-8") as f:
                        desc = f.read().strip()

                mp3_files = [os.path.basename(f) for f in glob.glob(os.path.join(path, "*.mp3"))]
                if mp3_files:
                    tracks_str = ", ".join(mp3_files)
                    result.append(f"- Playlist: '{subdir}'\n  Description: {desc}\n  Tracks: {tracks_str}")
                else:
                    result.append(f"- Playlist: '{subdir}'\n  Description: {desc}\n  Tracks: (No mp3 tracks found)")

            return "\n\n".join(result)
        except Exception as e:
            logger.error(f"Error listing playlists: {e}")
            return f"Error listing playlists: {e}"

    @with_cooldown("playing another playlist")
    def play_playlist(self, playlist_name: str) -> str:
        """Choose a playlist to play. This sends a signal to play the music on the canvas.

        Args:
            playlist_name: The name of the playlist to play.

        Returns:
            A status message indicating success or failure.
        """
        try:
            path = os.path.join(self.session_playlists_dir, playlist_name)
            if not os.path.exists(path) or not os.path.isdir(path):
                return f"Error: Playlist '{playlist_name}' not found."

            mp3_paths = glob.glob(os.path.join(path, "*.mp3"))
            if not mp3_paths:
                return f"Error: Playlist '{playlist_name}' does not contain any MP3 files."

            mp3_paths.sort()
            if self.active_session_id:
                tracks = [f"/sessions/{self.active_session_id}/playlists/{playlist_name}/{os.path.basename(f)}" for f in mp3_paths]
            else:
                tracks = [f"/playlists/{playlist_name}/{os.path.basename(f)}" for f in mp3_paths]

            if self.canvas_state_service:
                self.canvas_state_service.update_playlist(playlist_name, tracks, session_id=self.active_session_id)
            if self.on_play_playlist:
                self.on_play_playlist(playlist_name, tracks)

            logger.info(f"Playing playlist '{playlist_name}' ({len(tracks)} tracks)")
            return f"Successfully started playing playlist '{playlist_name}' containing {len(tracks)} tracks."
        except Exception as e:
            logger.error(f"Error playing playlist: {e}")
            return f"Error playing playlist: {e}"

    def pause_playlist(self) -> str:
        """Pause the current playing music playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.canvas_state_service:
                self.canvas_state_service.pause_playlist(session_id=self.session_id)
            if self.on_pause_playlist:
                self.on_pause_playlist()
            logger.info("Paused playlist")
            return "Successfully paused the playlist."
        except Exception as e:
            logger.error(f"Error pausing playlist: {e}")
            return f"Error pausing playlist: {e}"

    def resume_playlist(self) -> str:
        """Resume the paused music playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.canvas_state_service:
                self.canvas_state_service.resume_playlist(session_id=self.session_id)
            if self.on_resume_playlist:
                self.on_resume_playlist()
            logger.info("Resumed playlist")
            return "Successfully resumed the playlist."
        except Exception as e:
            logger.error(f"Error resuming playlist: {e}")
            return f"Error resuming playlist: {e}"
