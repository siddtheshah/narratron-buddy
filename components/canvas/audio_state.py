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
        self.pinned = False
        self.current_playlist_time = 0.0
        self.music_history: list[dict[str, object]] = []
        self.orator_cursor: int | None = None
        self._history_index: int | None = None

    @property
    def music_orator_cursor(self) -> int | None:
        return self.orator_cursor

    @music_orator_cursor.setter
    def music_orator_cursor(self, value: int | None) -> None:
        self.orator_cursor = value

    def update_music(self, music_id: str, tracks: list[str]) -> None:
        if self.pinned:
            return
        self.current_music_id = self.current_playlist = music_id
        self.current_playlist_tracks = list(tracks)
        self.music_paused = False
        self.current_playlist_time = time.time()
        entry: dict[str, object] = {"music_id": music_id, "tracks": list(tracks)}
        if not self.music_history or self.music_history[-1].get("music_id") != music_id:
            self.music_history.append(entry)
            self.music_history = self.music_history[-50:]
        self.orator_cursor = len(self.music_history) - 1 if self.music_history else None
        self._history_index = self.orator_cursor
        self._notify_changed("latest")

    def _apply_history_item(self, target_idx: int) -> bool:
        if not self.music_history or target_idx < 0 or target_idx >= len(self.music_history):
            return False
        entry = self.music_history[target_idx]
        music_id = entry.get("music_id")
        tracks_raw = entry.get("tracks")
        if type(music_id) is not str:
            return False
        tracks = [track for track in tracks_raw if type(track) is str] if type(tracks_raw) is list else []
        self.orator_cursor = target_idx
        self._history_index = target_idx
        self.current_music_id = self.current_playlist = music_id
        self.current_playlist_tracks = list(tracks)
        self.music_paused = False
        self.current_playlist_time = time.time()
        self._notify_changed("latest")
        return True

    def previous_music(self) -> bool:
        """Revert playback to the previous music entry in history."""
        curr_idx = len(self.music_history) - 1 if self.orator_cursor is None else self.orator_cursor
        if curr_idx <= 0 or not self.music_history:
            return False
        return self._apply_history_item(curr_idx - 1)

    def pause(self) -> None:
        if self.pinned:
            return
        self.music_paused = True
        self.current_playlist_time = time.time()
        self._notify_changed("latest")

    def resume(self) -> None:
        if self.pinned:
            return
        self.music_paused = False
        self.current_playlist_time = time.time()
        self._notify_changed("latest")

    def payload(self) -> dict[str, object]:
        playlist_id = self.current_music_id or self.current_playlist
        cursor_index = (
            len(self.music_history) - 1
            if self.orator_cursor is None
            else self.orator_cursor
        )
        if self.music_history:
            cursor_index = max(0, min(cursor_index, len(self.music_history) - 1))
        else:
            cursor_index = 0
        return {"music_id": playlist_id, "playlist": playlist_id,
                "tracks": list(self.current_playlist_tracks), "paused": self.music_paused,
                "time": self.current_playlist_time, "orator_cursor": cursor_index}

    def set_pinned(self, pinned: bool) -> None:
        self.pinned = pinned
        self._notify_changed("latest")

    def load(self, data: dict[str, object]) -> None:
        self.pinned = bool(data.get("music_pinned", False))
        self.current_music_id = data.get("current_music_id") if type(data.get("current_music_id")) is str else None
        self.current_playlist = data.get("current_playlist") if type(data.get("current_playlist")) is str else None
        tracks = data.get("current_playlist_tracks", [])
        self.current_playlist_tracks = [track for track in tracks if type(track) is str] if type(tracks) is list else []
        self.music_paused = bool(data.get("music_paused", False))
        saved_time = data.get("current_playlist_time")
        if type(saved_time) in (int, float) and float(saved_time) > 0:
            self.current_playlist_time = float(saved_time)
        elif self.current_music_id or self.current_playlist:
            # Older saved theater files did not include a timestamp.  Give
            # restored playback a fresh event time so a newly connected canvas
            # does not discard it as stale.
            self.current_playlist_time = time.time()
        else:
            self.current_playlist_time = 0.0

        history_raw = data.get("music_history")
        if type(history_raw) is list:
            parsed_history: list[dict[str, object]] = []
            for item in history_raw:
                if type(item) is dict and type(item.get("music_id")) is str:
                    t_list = item.get("tracks")
                    safe_tracks = [t for t in t_list if type(t) is str] if type(t_list) is list else []
                    parsed_history.append({"music_id": str(item["music_id"]), "tracks": safe_tracks})
            self.music_history = parsed_history
        elif self.current_music_id:
            self.music_history = [{"music_id": self.current_music_id, "tracks": list(self.current_playlist_tracks)}]
        else:
            self.music_history = []

        cursor_val = data.get("music_orator_cursor")
        if cursor_val is None and "orator_cursor" in data and "shown_images_history" not in data:
            cursor_val = data.get("orator_cursor")
        if not self.music_history:
            self.orator_cursor = None
            self._history_index = None
        elif cursor_val is not None:
            self.orator_cursor = max(0, min(int(cursor_val), len(self.music_history) - 1))
            self._history_index = self.orator_cursor
        else:
            self.orator_cursor = len(self.music_history) - 1
            self._history_index = self.orator_cursor

    def serialize(self) -> dict[str, object]:
        return {"current_music_id": self.current_music_id, "current_playlist": self.current_playlist,
                "current_playlist_tracks": self.current_playlist_tracks, "music_paused": self.music_paused,
                "current_playlist_time": self.current_playlist_time, "music_pinned": self.pinned,
                "music_history": list(self.music_history), "music_orator_cursor": self.orator_cursor,
                "orator_cursor": self.orator_cursor}
