"""Music playback state for a canvas theater."""

import time
from collections.abc import Callable


class AudioState:
    def __init__(self, notify_changed: Callable[..., None]) -> None:
        self._notify_changed = notify_changed
        self.current_music_id: str | None = None
        self.current_playlist: str | None = None
        self.current_playlist_tracks: list[str] = []
        self.music_paused = False
        self.current_playlist_time = 0.0

    def update_music(self, music_id: str, tracks: list[str]) -> None:
        self.current_music_id = self.current_playlist = music_id
        self.current_playlist_tracks = list(tracks)
        self.music_paused = False
        self.current_playlist_time = time.time()
        self._notify_changed("latest")

    def pause(self) -> None:
        self.music_paused = True
        self.current_playlist_time = time.time()
        self._notify_changed("latest")

    def resume(self) -> None:
        self.music_paused = False
        self.current_playlist_time = time.time()
        self._notify_changed("latest")

    def payload(self) -> dict:
        playlist_id = self.current_music_id or self.current_playlist
        return {"music_id": playlist_id, "playlist": playlist_id,
                "tracks": list(self.current_playlist_tracks), "paused": self.music_paused,
                "time": self.current_playlist_time}

    def load(self, data: dict[str, object]) -> None:
        self.current_music_id = data.get("current_music_id") if isinstance(data.get("current_music_id"), str) else None
        self.current_playlist = data.get("current_playlist") if isinstance(data.get("current_playlist"), str) else None
        tracks = data.get("current_playlist_tracks", [])
        self.current_playlist_tracks = [track for track in tracks if isinstance(track, str)] if isinstance(tracks, list) else []
        self.music_paused = bool(data.get("music_paused", False))

    def serialize(self) -> dict[str, object]:
        return {"current_music_id": self.current_music_id, "current_playlist": self.current_playlist,
                "current_playlist_tracks": self.current_playlist_tracks, "music_paused": self.music_paused}
