import glob
import logging
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

from components.chat_manager import ChatManager
from utils.image_utils import extract_image_prompt

logger = logging.getLogger(__name__)

class CanvasStateManager:
    """Encapsulates state for the web canvas UI including music playback, shown images,
    chat manager history, WebSocket connections, and doodle drawings.
    """
    def __init__(self, chat_output_dir: str = "output/chats"):
        self.chat_manager = ChatManager(output_dir=chat_output_dir)
        self.current_image_basename: Optional[str] = None
        
        # Shared state for music
        self.current_playlist: Optional[str] = None
        self.current_playlist_tracks: List[str] = []
        self.music_paused: bool = False
        self.current_playlist_time: float = 0.0
        
        # Shared state for shown image
        self.shown_image_path: Optional[str] = None
        self.shown_image_time: float = 0.0
        self.shown_image_prompt: str = ""
        
        # WebSocket and doodles
        self.active_ws_connections: List[WebSocket] = []
        self.doodles_state: List[Dict[str, Any]] = []

    def update_current_playlist(self, playlist_name: str, tracks: List[str]):
        self.current_playlist = playlist_name
        self.current_playlist_tracks = tracks
        self.music_paused = False
        self.current_playlist_time = time.time()

    def pause_current_playlist(self):
        self.music_paused = True
        self.current_playlist_time = time.time()

    def resume_current_playlist(self):
        self.music_paused = False
        self.current_playlist_time = time.time()

    def update_shown_image(self, file_path: str):
        self.shown_image_path = file_path
        self.shown_image_time = time.time()
        self.shown_image_prompt = extract_image_prompt(file_path)

    def add_chat_message(self, text: str, author: str = "agent"):
        self.chat_manager.add_message({"author": author, "text": text})

    def register_websocket(self, websocket: WebSocket):
        if websocket not in self.active_ws_connections:
            self.active_ws_connections.append(websocket)

    def unregister_websocket(self, websocket: WebSocket):
        if websocket in self.active_ws_connections:
            self.active_ws_connections.remove(websocket)

    async def broadcast_ws_message(self, message: Dict[str, Any], sender: Optional[WebSocket] = None):
        for connection in list(self.active_ws_connections):
            if connection != sender:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

    def add_doodle(self, doodle: Dict[str, Any]):
        if doodle.get("type") == "clear":
            self.doodles_state.clear()
        else:
            self.doodles_state.append(doodle)

    def get_latest_state(self, image_folder: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        music_state = {
            "playlist": self.current_playlist,
            "tracks": self.current_playlist_tracks,
            "paused": self.music_paused,
            "time": self.current_playlist_time
        }

        files = []
        if os.path.exists(image_folder):
            files.extend(glob.glob(os.path.join(image_folder, "*.png")))
            files.extend(glob.glob(os.path.join(image_folder, "*.jpg")))
            files.extend(glob.glob(os.path.join(image_folder, "*.jpeg")))
            
        if not files:
            from pathlib import Path
            if session_id:
                session_ref_dir = (Path(__file__).parent.parent / "sessions" / session_id / "reference_library").resolve()
                if session_ref_dir.exists():
                    ref_images = [f for f in session_ref_dir.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]]
                    if ref_images:
                        return {
                            "latest": f"/sessions/{session_id}/references/{ref_images[0].name}",
                            "time": 0,
                            "prompt": f"Mounted Reference: {ref_images[0].stem}",
                            "music": music_state
                        }

            global_avatar = (Path(__file__).parent.parent / "reference_library" / "narratron_avatar.jpg").resolve()
            if global_avatar.exists():
                return {
                    "latest": "/reference_library/narratron_avatar.jpg",
                    "time": 0,
                    "prompt": "Narratron Buddy Initialized",
                    "music": music_state
                }
            return {"latest": None, "time": 0, "music": music_state}
            
        # Get the newest file by modification time
        latest_file = max(files, key=os.path.getmtime)
        latest_file_time = os.path.getmtime(latest_file)
        
        # Decide which image to show
        if self.shown_image_path and os.path.exists(self.shown_image_path) and self.shown_image_time >= latest_file_time:
            selected_file = self.shown_image_path
            selected_time = self.shown_image_time
            prompt_text = self.shown_image_prompt
        else:
            selected_file = latest_file
            selected_time = latest_file_time
            prompt_text = extract_image_prompt(selected_file)
            
        basename = os.path.basename(selected_file)
        
        if self.current_image_basename is not None and self.current_image_basename != basename:
            self.chat_manager.export_and_reset(self.current_image_basename)
            self.doodles_state.clear()
            
        self.current_image_basename = basename
        
        image_url = f"/sessions/{session_id}/output/{basename}" if session_id else f"/images/{basename}"

        res = {
            "latest": image_url,
            "time": selected_time,
            "prompt": prompt_text,
            "music": music_state
        }
        logger.debug(f"[/api/latest] returning latest={res['latest']}, time={res['time']}, playlist={music_state['playlist']}")
        return res
