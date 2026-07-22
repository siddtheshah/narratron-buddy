import os
import glob
from typing import List, Callable, Optional
from pathlib import Path

class MusicTools:
    def __init__(self, config: dict):
        self.config = config
        root_dir = Path(__file__).parent.parent.resolve()
        relative_playlists_folder = config.get("music", {}).get("playlists_folder", "playlists")
        self.playlists_folder = str((root_dir / relative_playlists_folder).resolve())
        os.makedirs(self.playlists_folder, exist_ok=True)
        self.on_play_playlist: Optional[Callable[[str, List[str]], None]] = None
        self.on_pause_playlist: Optional[Callable[[], None]] = None
        self.on_resume_playlist: Optional[Callable[[], None]] = None

    def list_playlists(self) -> str:
        """List all available music playlists, their descriptions, and the tracks inside them.

        Returns:
            A formatted string of all available playlists, descriptions, and tracks.
        """
        try:
            if not os.path.exists(self.playlists_folder):
                return "No playlists folder found."

            subdirs = [d for d in os.listdir(self.playlists_folder)
                       if os.path.isdir(os.path.join(self.playlists_folder, d))]

            if not subdirs:
                return "No playlists found. Please add playlist subfolders in the playlists directory."

            result = []
            for subdir in subdirs:
                path = os.path.join(self.playlists_folder, subdir)
                # Check for description.txt
                desc_path = os.path.join(path, "description.txt")
                desc = "No description available."
                if os.path.exists(desc_path):
                    with open(desc_path, "r", encoding="utf-8") as f:
                        desc = f.read().strip()

                # Check for mp3 files
                mp3_files = [os.path.basename(f) for f in glob.glob(os.path.join(path, "*.mp3"))]
                if mp3_files:
                    tracks_str = ", ".join(mp3_files)
                    result.append(f"- Playlist: '{subdir}'\n  Description: {desc}\n  Tracks: {tracks_str}")
                else:
                    result.append(f"- Playlist: '{subdir}'\n  Description: {desc}\n  Tracks: (No mp3 tracks found)")

            return "\n\n".join(result)
        except Exception as e:
            return f"Error listing playlists: {e}"

    def play_playlist(self, playlist_name: str) -> str:
        """Choose a playlist to play. This sends a signal to play the music on the canvas.

        Args:
            playlist_name: The name of the playlist to play.

        Returns:
            A status message indicating success or failure.
        """
        try:
            path = os.path.join(self.playlists_folder, playlist_name)
            if not os.path.exists(path) or not os.path.isdir(path):
                return f"Error: Playlist '{playlist_name}' not found."

            # Find mp3 files
            mp3_paths = glob.glob(os.path.join(path, "*.mp3"))
            if not mp3_paths:
                return f"Error: Playlist '{playlist_name}' does not contain any MP3 files."

            # Sort tracks alphabetically to keep order consistent
            mp3_paths.sort()

            # Build relative URLs for the web app, e.g. /playlists/ambient/track1.mp3
            tracks = [f"/playlists/{playlist_name}/{os.path.basename(f)}" for f in mp3_paths]

            if self.on_play_playlist:
                self.on_play_playlist(playlist_name, tracks)

            return f"Successfully started playing playlist '{playlist_name}' containing {len(tracks)} tracks."
        except Exception as e:
            return f"Error playing playlist: {e}"

    def pause_playlist(self) -> str:
        """Pause the current playing music playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.on_pause_playlist:
                self.on_pause_playlist()
            return "Successfully paused the playlist."
        except Exception as e:
            return f"Error pausing playlist: {e}"

    def resume_playlist(self) -> str:
        """Resume the paused music playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.on_resume_playlist:
                self.on_resume_playlist()
            return "Successfully resumed the playlist."
        except Exception as e:
            return f"Error resuming playlist: {e}"
