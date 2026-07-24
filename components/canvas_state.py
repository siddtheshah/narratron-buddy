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
    def __init__(self, session_id: str):
        self.session_id = session_id
        chat_output_dir = str(Path(__file__).parent.parent / "sessions" / session_id / "output" / "chats")
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

        self.load_state_from_disk()

    def load_state_from_disk(self):
        """Restore canvas state from local session.json if available."""
        import json
        sess_dir = (Path(__file__).parent.parent / "sessions" / self.session_id).resolve()
        session_file = sess_dir / "session.json"
        legacy_state_file = sess_dir / "session_state.json"
        
        target_file = session_file if session_file.exists() else (legacy_state_file if legacy_state_file.exists() else None)
        
        if target_file:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    c_state = data.get("canvas_state") or data
                    self.current_image_basename = c_state.get("current_image_basename")
                    self.shown_image_path = c_state.get("shown_image_path")
                    self.shown_image_prompt = c_state.get("shown_image_prompt", "")
                    self.shown_images_history = c_state.get("shown_images_history", [])
                    self.current_playlist = c_state.get("current_playlist")
                    self.current_playlist_tracks = c_state.get("current_playlist_tracks", [])
                    self.music_paused = c_state.get("music_paused", False)
                    self.doodles_state = c_state.get("doodles", [])
                    chat_msgs = c_state.get("chat_messages", [])
                    if chat_msgs:
                        self.chat_manager.messages = chat_msgs
                    logger.info(f"Loaded canvas state from {target_file.name} for session '{self.session_id}'.")
            except Exception as e:
                logger.warning(f"Failed to load canvas state for {self.session_id}: {e}")

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

        # Automatically copy shown image into session output directory if outside session output dir
        target_session = session_id or self.session_id
        if target_session and file_path and os.path.exists(file_path):
            sess_out_dir = (Path(__file__).parent.parent / "sessions" / target_session / "output").resolve()
            sess_out_dir.mkdir(parents=True, exist_ok=True)
            file_path_obj = Path(file_path).resolve()
            try:
                # Check if file is already inside sess_out_dir
                file_path_obj.relative_to(sess_out_dir)
            except ValueError:
                # File is outside session output directory: copy to session output dir
                target_path = sess_out_dir / os.path.basename(file_path)
                if not target_path.exists():
                    try:
                        shutil.copy2(file_path, target_path)
                    except Exception as e:
                        logger.warning(f"Failed to copy shown image to session output dir: {e}")

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

    def get_latest_state(self) -> Dict[str, Any]:
        image_folder = str(Path(__file__).parent.parent / "sessions" / self.session_id / "output")

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

        # 2. If no image selected, fallback to session references
        if not selected_file:
            if self.session_id:
                session_ref_dir = (Path(__file__).parent.parent / "sessions" / self.session_id / "references").resolve()
                if session_ref_dir.exists():
                    ref_images = [f for f in session_ref_dir.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]]
                    if ref_images:
                        return {
                            "latest": f"/sessions/{self.session_id}/references/{ref_images[0].name}",
                            "time": 0,
                            "prompt": f"Mounted Reference: {ref_images[0].stem}",
                            "music": music_state
                        }
            return {"latest": None, "time": 0, "music": music_state}
            
        basename = os.path.basename(selected_file)
        
        if self.current_image_basename is not None and self.current_image_basename != basename:
            self.chat_manager.export_and_reset(self.current_image_basename)
            self.doodles_state.clear()
            
        self.current_image_basename = basename
        
        sel_path_obj = Path(selected_file).resolve()
        if "references" in sel_path_obj.parts or "reference_library" in sel_path_obj.parts:
            image_url = f"/sessions/{self.session_id}/references/{basename}"
        else:
            image_url = f"/sessions/{self.session_id}/output/{basename}"

        res = {
            "latest": image_url,
            "time": selected_time,
            "prompt": prompt_text,
            "music": music_state
        }
        logger.debug(f"[/api/latest] returning latest={res['latest']}, time={res['time']}, playlist={music_state['playlist']}")
        return res

    def export_session_data(self, session_dir: Optional[Path] = None) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Ensure current displayed image is saved and gather session metadata, canvas data and files."""
        import json
        if self.shown_image_path and session_dir:
            self.update_shown_image(self.shown_image_path, session_id=session_dir.name)

        canvas_state = {
            "current_image_basename": self.current_image_basename,
            "shown_image_path": self.shown_image_path,
            "shown_image_prompt": self.shown_image_prompt,
            "shown_images_history": self.shown_images_history,
            "current_playlist": self.current_playlist,
            "current_playlist_tracks": self.current_playlist_tracks,
            "music_paused": self.music_paused,
            "doodles": list(self.doodles_state),
            "chat_messages": self.chat_manager.get_messages(),
        }

        metadata = {}
        if session_dir:
            session_dir = Path(session_dir).resolve()
            session_dir.mkdir(parents=True, exist_ok=True)
            meta_file = session_dir / "session.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception:
                    metadata = {}

            metadata["canvas_state"] = canvas_state

            try:
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to update session.json: {e}")

            # Remove legacy session_state.json if it exists
            legacy_file = session_dir / "session_state.json"
            if legacy_file.exists():
                try:
                    legacy_file.unlink()
                except Exception:
                    pass

        state_data = dict(metadata)
        if "canvas_state" not in state_data:
            state_data["canvas_state"] = canvas_state

        image_files = []
        seen_filenames = set()
        if session_dir and session_dir.exists():
            for f in session_dir.rglob("*"):
                if f.is_file() and f.name not in ["session.json", "session_state.json"]:
                    rel_path = str(f.relative_to(session_dir)).replace("\\", "/")
                    if rel_path not in seen_filenames:
                        seen_filenames.add(rel_path)
                        if "references" in f.parts:
                            category = "reference"
                        elif "output" in f.parts:
                            category = "output"
                        else:
                            category = str(f.parent.relative_to(session_dir)).replace("\\", "/")

                        try:
                            with open(f, "rb") as fp:
                                image_files.append({
                                    "filename": f.name,
                                    "category": category,
                                    "data": fp.read()
                                })
                        except Exception:
                            pass

        return state_data, image_files

