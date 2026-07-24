import glob
import logging
import os
from pathlib import Path
import shutil
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
        self.shown_images_history: List[str] = []
        
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

    def update_shown_image(self, file_path: str, session_id: Optional[str] = None):
        self.shown_image_path = file_path
        self.shown_image_time = time.time()
        self.shown_image_prompt = extract_image_prompt(file_path)
        if file_path and file_path not in self.shown_images_history:
            self.shown_images_history.append(file_path)

        # Automatically copy shown image into session output directory if applicable
        if file_path and os.path.exists(file_path):
            try:
                src_path = Path(file_path).resolve()
                sessions_dir = Path(__file__).parent.parent / "sessions"
                if session_id:
                    target_sids = [session_id]
                elif sessions_dir.exists():
                    target_sids = [d.name for d in sessions_dir.iterdir() if d.is_dir()]
                else:
                    target_sids = []

                for sid in target_sids:
                    dest_dir = sessions_dir / sid / "output"
                    if dest_dir.exists():
                        dest_file = dest_dir / src_path.name
                        if not dest_file.exists() or dest_file.resolve() != src_path:
                            shutil.copy2(src_path, dest_file)
            except Exception as e:
                logger.error(f"Error copying shown image to session output folder: {e}")

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

        # 1. Decide which image file to select: prioritize explicit show_image call if set & valid
        selected_file = None
        selected_time = 0.0
        prompt_text = ""

        if self.shown_image_path and os.path.exists(self.shown_image_path):
            selected_file = self.shown_image_path
            selected_time = self.shown_image_time
            prompt_text = self.shown_image_prompt
        else:
            files = []
            if os.path.exists(image_folder):
                files.extend(glob.glob(os.path.join(image_folder, "*.png")))
                files.extend(glob.glob(os.path.join(image_folder, "*.jpg")))
                files.extend(glob.glob(os.path.join(image_folder, "*.jpeg")))
                
            if files:
                selected_file = max(files, key=os.path.getmtime)
                selected_time = os.path.getmtime(selected_file)
                prompt_text = extract_image_prompt(selected_file)

        # 2. If no image selected, fallback to session references or global avatar
        if not selected_file:
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
            
        basename = os.path.basename(selected_file)
        
        if self.current_image_basename is not None and self.current_image_basename != basename:
            self.chat_manager.export_and_reset(self.current_image_basename)
            self.doodles_state.clear()
            
        self.current_image_basename = basename
        
        sel_path_obj = Path(selected_file).resolve()
        if "reference_library" in sel_path_obj.parts:
            if session_id and (Path(__file__).parent.parent / "sessions" / session_id / "reference_library" / basename).exists():
                image_url = f"/sessions/{session_id}/references/{basename}"
            else:
                image_url = f"/reference_library/{basename}"
        elif session_id and (Path(__file__).parent.parent / "sessions" / session_id / "output" / basename).exists():
            image_url = f"/sessions/{session_id}/output/{basename}"
        else:
            image_url = f"/images/{basename}"

        res = {
            "latest": image_url,
            "time": selected_time,
            "prompt": prompt_text,
            "music": music_state
        }
        logger.debug(f"[/api/latest] returning latest={res['latest']}, time={res['time']}, playlist={music_state['playlist']}")
        return res

    def export_session_data(self, session_dir: Optional[Path] = None) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Gather current canvas state data and binary images for database export."""
        state_data = {
            "current_image_basename": self.current_image_basename,
            "shown_image_path": self.shown_image_path,
            "shown_image_prompt": self.shown_image_prompt,
            "current_playlist": self.current_playlist,
            "current_playlist_tracks": self.current_playlist_tracks,
            "music_paused": self.music_paused,
            "doodles": list(self.doodles_state),
            "chat_messages": self.chat_manager.get_messages(),
        }

        image_files = []
        if session_dir and session_dir.exists():
            out_dir = session_dir / "output"
            ref_dir = session_dir / "reference_library"
            if out_dir.exists():
                for f in out_dir.rglob("*"):
                    if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                        with open(f, "rb") as fp:
                            image_files.append({
                                "filename": f.name,
                                "category": "output",
                                "data": fp.read()
                            })
            if ref_dir.exists():
                for f in ref_dir.rglob("*"):
                    if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                        with open(f, "rb") as fp:
                            image_files.append({
                                "filename": f.name,
                                "category": "reference",
                                "data": fp.read()
                            })

        for img_path in self.shown_images_history:
            if img_path and os.path.exists(img_path):
                fn = os.path.basename(img_path)
                if not any(img["filename"] == fn for img in image_files):
                    try:
                        with open(img_path, "rb") as fp:
                            image_files.append({
                                "filename": fn,
                                "category": "output",
                                "data": fp.read()
                            })
                    except Exception:
                        pass

        return state_data, image_files

