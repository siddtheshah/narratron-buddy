import glob
import logging
import os
import re
import threading
import time
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from providers import (
    MusicGenerationRequest,
    MusicProviderError,
    get_music_provider,
)
from tools.base_tool import BaseTools, with_cooldown
from components.theater_manager import TheaterManager
from tools.music_catalog import MusicCatalog

logger = logging.getLogger(__name__)

class MusicTools(BaseTools):
    def __init__(
        self,
        config: dict,
        theater_id: str,
        theater_manager: TheaterManager,
        canvas_state_service: Any = None,
    ):
        raw_config = config or {}
        subconfig = raw_config.get("music", raw_config) if "music" in raw_config else raw_config
        super().__init__(
            config=subconfig,
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
            default_cooldown=60.0,
        )
        self.theater_manager = theater_manager
        self.theater = theater_manager.theater(self.active_theater_id)
        
        # User-provided playlists directory
        self.theater_playlists_dir = str(self.theater.playlists_dir())
        os.makedirs(self.theater_playlists_dir, exist_ok=True)

        # Output created music directory under output/music
        self.output_dir = str(self.theater.music_artifacts_dir())
        os.makedirs(self.output_dir, exist_ok=True)

        # Provider & Cooldown configuration
        self.generation_enabled = bool(subconfig.get("generation_enabled", subconfig.get("enabled", True)))
        self.generation_cooldown = float(subconfig.get("generation_cooldown", subconfig.get("cooldown_duration", 90.0)))
        self.switch_cooldown = float(subconfig.get("switch_cooldown", subconfig.get("cooldown_duration", 15.0)))
        self.style_default = str(subconfig.get("style", "")).strip()
        self._cooldown_duration = self.switch_cooldown

        self.music_provider_id = str(subconfig.get("provider") or "lyria").strip()
        provider_options = subconfig.get("provider_options") or {}
        self.music_provider_options = dict(provider_options) if isinstance(provider_options, dict) else {}
        self._music_provider = None
        self.music_catalog = MusicCatalog(
            theater_manager.music_catalog_dir(),
            match_threshold=float(subconfig.get("catalog_match_threshold", 0.86)),
            candidate_count=int(subconfig.get("catalog_candidate_count", 5)),
            reranker_model=str(subconfig.get("catalog_reranker_model", "gemini-2.5-flash-lite")),
        )

        # Callbacks
        self.on_play_music: Optional[Callable[[str, List[str]], None]] = None
        self.on_pause_music: Optional[Callable[[], None]] = None
        self.on_resume_music: Optional[Callable[[], None]] = None
        self.on_music_created: Optional[Callable] = None

        # In-memory mapping of custom handles/aliases to file paths/track URLs
        self.music_aliases: Dict[str, str] = {}
        self.currently_playing_music_id: Optional[str] = None

    @property
    def cooldown_duration(self) -> float:
        return self._cooldown_duration

    @cooldown_duration.setter
    def cooldown_duration(self, val: float) -> None:
        val_float = float(val)
        self._cooldown_duration = val_float
        self.generation_cooldown = val_float
        self.switch_cooldown = val_float

    def check_cooldown(
        self,
        tool_name: str,
        action_desc: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Optional[str]:
        if duration is None:
            if tool_name == "create_music":
                duration = self.generation_cooldown
            elif tool_name == "play_music":
                duration = self.switch_cooldown
        return super().check_cooldown(tool_name, action_desc, duration)

    def record_tool_call(self, tool_name: str, duration: Optional[float] = None) -> None:
        if duration is None:
            if tool_name == "create_music":
                duration = self.generation_cooldown
            elif tool_name == "play_music":
                duration = self.switch_cooldown
        super().record_tool_call(tool_name, duration)

    @property
    def playlists_folder(self) -> str:
        return self.theater_playlists_dir

    @playlists_folder.setter
    def playlists_folder(self, val: str) -> None:
        self.theater_playlists_dir = str(val)

    def _get_music_provider(self):
        """Build the configured music provider once per session."""
        if self._music_provider is None:
            self._music_provider = get_music_provider(self.music_provider_id, self.music_provider_options)
        return self._music_provider

    def _resolve_music_tracks(self, music_id: str) -> Optional[List[str]]:
        """Resolve a music_id (playlist folder name, created track handle, or filename) to track URLs."""
        if not music_id:
            return None

        clean_id = music_id.strip()

        # 1. Check in-memory aliases first
        if clean_id in self.music_aliases:
            return [self.music_aliases[clean_id]]
        clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', clean_id)
        if clean_stem in self.music_aliases:
            return [self.music_aliases[clean_stem]]

        # 2. Check user playlist directory (folder containing MP3 files)
        playlist_path = os.path.join(self.theater_playlists_dir, clean_id)
        if os.path.exists(playlist_path) and os.path.isdir(playlist_path):
            mp3_paths = glob.glob(os.path.join(playlist_path, "*.mp3"))
            if mp3_paths:
                mp3_paths.sort()
                if self.active_theater_id:
                    return [f"/theaters/{self.active_theater_id}/playlists/{clean_id}/{os.path.basename(f)}" for f in mp3_paths]
                return [f"/playlists/{clean_id}/{os.path.basename(f)}" for f in mp3_paths]

        # 3. Check created music output directory output/music
        if os.path.exists(self.output_dir):
            for filename in os.listdir(self.output_dir):
                if filename.lower().endswith((".mp3", ".wav", ".ogg")):
                    stem = Path(filename).stem
                    if clean_id == filename or clean_id == stem or clean_id.lower() == stem.lower() or clean_id.startswith(stem) or stem.startswith(clean_id):
                        if self.active_theater_id:
                            url = f"/theaters/{self.active_theater_id}/output/music/{filename}"
                        else:
                            url = f"/output/music/{filename}"
                        self.music_aliases[clean_id] = url
                        return [url]

        return None

    def join_generation(self, timeout: float = 10.0) -> None:
        """Helper for unit tests or teardown to wait for background music generation thread."""
        thread = getattr(self, "_last_generation_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def _apply_default_style(self, music_prompt: str) -> str:
        """Apply the agent's default style to the music prompt if no style is specified."""
        if self.style_default and not re.search(r"\bstyle\b", music_prompt, flags=re.IGNORECASE):
            return f"{music_prompt}\n\nStyle: {self.style_default}"
        return music_prompt

    def create_music(
        self,
        prompt: str,
        handle: Optional[str] = None,
    ) -> str:
        """Generates a custom background music track based on a prompt and optional handle.

        Args:
            prompt: Text prompt describing the music to generate.
            handle: Optional handle or name alias for the created music track (e.g. 'desert_ambient').

        Returns:
            A status string indicating background generation has started.
        """
        effective_prompt = self._apply_default_style(prompt)
        logger.info(f"[create_music tool] prompt: {effective_prompt}, handle: {handle}")
        if not self.generation_enabled:
            return "Error: Music generation is disabled in theater configuration."

        match = self.music_catalog.find_match(effective_prompt)
        if match:
            alias_key = handle or re.sub(r'[^a-zA-Z0-9_-]', '_', Path(match["filename"]).stem)
            filename = f"{alias_key}_{int(time.time())}.mp3"
            destination = Path(self.output_dir) / filename
            shutil.copy2(match["path"], destination)
            track_url = f"/theaters/{self.active_theater_id}/output/music/{filename}" if self.active_theater_id else f"/output/music/{filename}"
            self.music_aliases[alias_key] = track_url
            self.music_aliases[alias_key.lower()] = track_url
            self.music_aliases[filename] = track_url
            self._play_music_internal(alias_key)
            return f"Reused a matching private catalog track (similarity {match['score']:.2f}) and started playing it as '{alias_key}'."

        cooldown_error = self.check_cooldown("create_music", "generating another music track")
        if cooldown_error:
            return cooldown_error
        self.record_tool_call("create_music")

        def _worker():
            try:
                provider = self._get_music_provider()
                logger.info(f"[create_music tool] Generating music with provider '{self.music_provider_id}' for prompt: '{effective_prompt}'")
                result = provider.generate(MusicGenerationRequest(prompt=effective_prompt))
                audio_bytes = result.audio_bytes

                if audio_bytes:
                    timestamp = int(time.time())
                    if handle:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', handle)
                        filename = f"{clean_name}_{timestamp}.mp3"
                    else:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', effective_prompt[:20]).strip("_") or "track"
                        filename = f"music_{clean_name}_{timestamp}.mp3"

                    filepath = os.path.join(self.output_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(audio_bytes)

                    try:
                        self.music_catalog.add(Path(filepath), effective_prompt, result.provider, result.model)
                    except Exception as catalog_error:
                        # Generation must still complete if the optional cost-saving
                        # index cannot be written.
                        logger.error("[create_music tool] Could not add track to private catalog: %s", catalog_error)

                    if self.active_theater_id:
                        track_url = f"/theaters/{self.active_theater_id}/output/music/{filename}"
                    else:
                        track_url = f"/output/music/{filename}"

                    alias_key = handle or clean_name
                    self.music_aliases[alias_key] = track_url
                    self.music_aliases[alias_key.lower()] = track_url
                    self.music_aliases[filename] = track_url

                    logger.info(f"[create_music tool] Saved music track to {filepath} with handle '{alias_key}'")

                    if self.on_music_created:
                        try:
                            self.on_music_created(filepath)
                        except Exception as cb_err:
                            logger.error(f"[MusicTools] Callback on_music_created error: {cb_err}")

                    self._play_music_internal(alias_key)
                else:
                    logger.error("[create_music tool] Provider returned no binary audio data.")
            except MusicProviderError as e:
                logger.error(f"[create_music tool] Music provider failed: {e}")
            except Exception as e:
                logger.error(f"[create_music tool] Error generating music in background: {e}")

        t = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = t
        t.start()

        handle_msg = f" with handle '{handle}'" if handle else ""
        return f"Music generation started in background{handle_msg} for prompt: '{effective_prompt[:80]}'. It will automatically play when ready."

    def _play_music_internal(self, music_id: str) -> str:
        try:
            tracks = self._resolve_music_tracks(music_id)
            if not tracks:
                return f"Error: Music or playlist '{music_id}' not found."

            if self.canvas_state_service:
                self.canvas_state_service.update_music(music_id, tracks, theater_id=self.active_theater_id)
            if self.on_play_music:
                self.on_play_music(music_id, tracks)

            self.currently_playing_music_id = music_id
            logger.info(f"Playing music '{music_id}' ({len(tracks)} tracks)")
            return f"Successfully started playing music '{music_id}' containing {len(tracks)} tracks."
        except Exception as e:
            logger.error(f"Error playing music: {e}")
            return f"Error playing music: {e}"

    @with_cooldown("playing another music track")
    def play_music(self, music_id: str) -> str:
        """Choose music to play by its playlist name or generated track handle.

        Args:
            music_id: The playlist name or handle of the music track to play.

        Returns:
            A status message indicating success or failure.
        """
        self.record_tool_call("play_music")
        return self._play_music_internal(music_id)

    def pause_music(self) -> str:
        """Pause the current playing music track or playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.canvas_state_service:
                self.canvas_state_service.pause_music(theater_id=self.theater_id)
            if self.on_pause_music:
                self.on_pause_music()
            logger.info("Paused music")
            return "Successfully paused the music."
        except Exception as e:
            logger.error(f"Error pausing music: {e}")
            return f"Error pausing music: {e}"

    def resume_music(self) -> str:
        """Resume the paused music track or playlist on the canvas dashboard.

        Returns:
            A status message indicating success or failure.
        """
        try:
            if self.canvas_state_service:
                self.canvas_state_service.resume_music(theater_id=self.theater_id)
            if self.on_resume_music:
                self.on_resume_music()
            logger.info("Resumed music")
            return "Successfully resumed the music."
        except Exception as e:
            logger.error(f"Error resuming music: {e}")
            return f"Error resuming music: {e}"
