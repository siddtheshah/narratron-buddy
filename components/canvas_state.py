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
from utils.theaters_paths import ensure_theaters_root

logger = logging.getLogger(__name__)

MAX_AGENT_THOUGHT_LENGTH = 360

class CanvasStateManager:
    """Encapsulates state for the web canvas UI including music playback, shown images,
    chat manager history, WebSocket connections, and doodle drawings.
    """
    @property
    def theaters_dir(self) -> Path:
        if getattr(self, "base_theaters_dir", None) is not None:
            return self.base_theaters_dir
        return ensure_theaters_root()

    def __init__(self, theater_id: str, base_theaters_dir: Optional[Path] = None):
        self.theater_id = theater_id
        if base_theaters_dir is not None:
            self.base_theaters_dir = Path(base_theaters_dir).resolve()
        else:
            self.base_theaters_dir = ensure_theaters_root()
        chat_output_dir = str(self.theaters_dir / theater_id / "output" / "chats")
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
        self.shown_image_transition: str = "crossfade"
        self.shown_image_effect: str = "gleam3"
        
        # WebSocket and doodles
        self.active_ws_connections: List[WebSocket] = []
        self.doodles_state: List[Dict[str, Any]] = []
        self.doodles_enabled: bool = True

        # Transient agent tool activity. This is intentionally not persisted: a
        # reconnect should not show an activity indicator left over from a
        # previous server process.
        self.image_generation_active: bool = False
        self.notes_activity_until: float = 0.0
        self.live_connection_active: bool = False
        self.live_connection_until: float = 0.0

        # The agent's latest chat-tool update is kept separately from the
        # public chat transcript. It is intentionally transient so a restarted
        # theater does not present an outdated status as current.
        self.current_agent_thought: str = ""
        self.current_agent_thought_time: float = 0.0

        self.load_state_from_disk()

    def load_state_from_disk(self):
        """Restore canvas state from local theater.json if available."""
        import json
        sess_dir = (self.theaters_dir / self.theater_id).resolve()
        theater_file = sess_dir / "theater.json"
        legacy_state_file = sess_dir / "theater_state.json"
        
        target_file = theater_file if theater_file.exists() else (legacy_state_file if legacy_state_file.exists() else None)
        
        if target_file:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    c_state = data.get("canvas_state") or data
                    self.current_image_basename = c_state.get("current_image_basename")
                    self.shown_image_path = c_state.get("shown_image_path")
                    self.shown_image_prompt = c_state.get("shown_image_prompt", "")
                    self.shown_images_history = c_state.get("shown_images_history", [])
                    self.shown_image_transition = c_state.get("shown_image_transition", "fade")
                    self.shown_image_effect = c_state.get("shown_image_effect", "gleam3")
                    self.current_playlist = c_state.get("current_playlist")
                    self.current_playlist_tracks = c_state.get("current_playlist_tracks", [])
                    self.music_paused = c_state.get("music_paused", False)
                    self.doodles_state = c_state.get("doodles", [])
                    self.doodles_enabled = c_state.get("doodles_enabled", True)
                    chat_msgs = c_state.get("chat_messages", [])
                    if chat_msgs:
                        self.chat_manager.messages = chat_msgs
                    logger.info(f"Loaded canvas state from {target_file.name} for theater '{self.theater_id}'.")
            except Exception as e:
                logger.warning(f"Failed to load canvas state for {self.theater_id}: {e}")

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

    def _get_url_for_path(self, file_path: str) -> str:
        if not file_path:
            return ""
        basename = os.path.basename(file_path)
        sel_path_obj = Path(file_path).resolve()
        if "references" in sel_path_obj.parts or "reference_library" in sel_path_obj.parts:
            return f"/theaters/{self.theater_id}/references/{basename}"
        return f"/theaters/{self.theater_id}/output/{basename}"

    def update_shown_image(
        self,
        file_path: str,
        theater_id: Optional[str] = None,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ):
        transition = transition or "crossfade"
        effect = effect or "gleam3"
        image_changed = file_path != self.shown_image_path
        presentation_changed = (
            image_changed
            or transition != getattr(self, "shown_image_transition", "crossfade")
            or effect != getattr(self, "shown_image_effect", "gleam3")
        )
        if image_changed:
            self.doodles_state.clear()
        if presentation_changed:
            self.shown_image_time = time.time()
        elif not getattr(self, "shown_image_time", None):
            self.shown_image_time = time.time()
        self.shown_image_path = file_path
        self.shown_image_prompt = extract_image_prompt(file_path)
        self.shown_image_transition = transition
        self.shown_image_effect = effect

        if file_path:
            image_url = self._get_url_for_path(file_path)
            history_item = {
                "path": file_path,
                "url": image_url,
                "prompt": self.shown_image_prompt,
                "time": self.shown_image_time,
                "transition": self.shown_image_transition,
                "effect": self.shown_image_effect,
            }
            # Append if history is empty or last item path differs from file_path
            last_path = None
            if self.shown_images_history:
                last_entry = self.shown_images_history[-1]
                last_path = last_entry.get("path") if isinstance(last_entry, dict) else last_entry
            if last_path != file_path:
                self.shown_images_history.append(history_item)
                if len(self.shown_images_history) > 100:
                    self.shown_images_history = self.shown_images_history[-100:]
            elif self.shown_images_history:
                self.shown_images_history[-1] = history_item

        # Automatically copy shown image into theater output directory if outside theater output dir
        target_theater = theater_id or self.theater_id
        if target_theater and file_path and os.path.exists(file_path):
            sess_out_dir = (self.theaters_dir / target_theater / "output").resolve()
            sess_out_dir.mkdir(parents=True, exist_ok=True)
            file_path_obj = Path(file_path).resolve()
            try:
                # Check if file is already inside sess_out_dir
                file_path_obj.relative_to(sess_out_dir)
            except ValueError:
                # File is outside theater output directory: copy to theater output dir
                target_path = sess_out_dir / os.path.basename(file_path)
                if not target_path.exists():
                    try:
                        shutil.copy2(file_path, target_path)
                    except Exception as e:
                        logger.warning(f"Failed to copy shown image to theater output dir: {e}")

    def add_chat_message(self, text: str, author: str = "agent"):
        self.chat_manager.add_message({"author": author, "text": text})

    def set_agent_thought(self, text: str):
        normalized_text = text.strip()
        if len(normalized_text) > MAX_AGENT_THOUGHT_LENGTH:
            normalized_text = normalized_text[: MAX_AGENT_THOUGHT_LENGTH - 1].rstrip() + "…"
        self.current_agent_thought = normalized_text
        self.current_agent_thought_time = time.time() if self.current_agent_thought else 0.0

    def get_agent_thought(self) -> Dict[str, Any]:
        return {
            "text": self.current_agent_thought,
            "time": self.current_agent_thought_time,
        }

    def register_websocket(self, websocket: WebSocket, user: Optional[Dict[str, Any]] = None):
        if websocket not in self.active_ws_connections:
            self.active_ws_connections.append(websocket)
        if not hasattr(self, "active_user_connections"):
            self.active_user_connections = {}
        self.active_user_connections[websocket] = user

    def unregister_websocket(self, websocket: WebSocket):
        if websocket in self.active_ws_connections:
            self.active_ws_connections.remove(websocket)
        if hasattr(self, "active_user_connections") and websocket in self.active_user_connections:
            del self.active_user_connections[websocket]

    def get_active_viewers(self) -> List[Dict[str, Any]]:
        """Return list of distinct authenticated users currently connected as active canvas viewers."""
        if not hasattr(self, "active_user_connections"):
            return []
        users_by_id = {}
        for u in self.active_user_connections.values():
            if u and "id" in u:
                users_by_id[u["id"]] = {"id": u["id"], "username": u.get("username", "")}
        return list(users_by_id.values())


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
        sess_dir = (self.theaters_dir / self.theater_id).resolve()
        sess_dir.mkdir(parents=True, exist_ok=True)
        self.export_theater_data(theater_dir=sess_dir)

    def set_doodles_enabled(self, enabled: bool):
        self.doodles_enabled = bool(enabled)
        sess_dir = (self.theaters_dir / self.theater_id).resolve()
        if sess_dir.exists():
            self.export_theater_data(theater_dir=sess_dir)

    def set_tool_activity(self, tool: str, active: bool = True, recent_seconds: float = 5.0):
        """Update transient canvas indicators for agent tool use."""
        if tool == "image":
            self.image_generation_active = bool(active)
        elif tool == "notes" and active:
            self.notes_activity_until = time.time() + max(0.0, recent_seconds)
        elif tool == "live":
            self.live_connection_active = bool(active)
            if active and recent_seconds > 0:
                self.live_connection_until = time.time() + max(0.0, recent_seconds)
            elif not active:
                self.live_connection_until = 0.0

    def get_tool_activity(self) -> Dict[str, bool]:
        now = time.time()
        return {
            "image_generating": self.image_generation_active,
            "notes_recent": now < self.notes_activity_until,
            "live_ready": self.live_connection_active or (now < self.live_connection_until),
        }

    def get_latest_state(self) -> Dict[str, Any]:
        image_folder = str(self.theaters_dir / self.theater_id / "output")

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

        if self.shown_image_path:
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

        formatted_history = []
        for h in self.shown_images_history:
            if isinstance(h, dict):
                formatted_history.append(h)
            elif isinstance(h, str):
                formatted_history.append({
                    "path": h,
                    "url": self._get_url_for_path(h),
                    "prompt": extract_image_prompt(h),
                    "time": 0.0,
                    "transition": getattr(self, "shown_image_transition", "fade") or "fade",
                    "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3",
                })

        # 2. If no image selected, fallback to theater references
        if not selected_file:
            if self.theater_id:
                theater_ref_dir = (self.theaters_dir / self.theater_id / "references").resolve()
                if theater_ref_dir.exists():
                    ref_images = [f for f in theater_ref_dir.iterdir() if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]]
                    if ref_images:
                        ref_url = f"/theaters/{self.theater_id}/references/{ref_images[0].name}"
                        ref_prompt = f"Mounted Reference: {ref_images[0].stem}"
                        if not formatted_history:
                            formatted_history.append({
                                "path": str(ref_images[0]),
                                "url": ref_url,
                                "prompt": ref_prompt,
                                "time": 0,
                                "transition": getattr(self, "shown_image_transition", "fade") or "fade",
                                "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3",
                            })
                        return {
                            "latest": ref_url,
                            "time": 0,
                            "prompt": ref_prompt,
                            "music": music_state,
                            "doodles_enabled": self.doodles_enabled,
                            "transition": getattr(self, "shown_image_transition", "fade") or "fade",
                            "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3",
                            "history": formatted_history,
                            "tool_activity": self.get_tool_activity(),
                            "agent_thought": self.get_agent_thought(),
                        }
            return {"latest": None, "time": 0, "music": music_state, "doodles_enabled": self.doodles_enabled, "transition": getattr(self, "shown_image_transition", "fade") or "fade", "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3", "history": formatted_history, "tool_activity": self.get_tool_activity(), "agent_thought": self.get_agent_thought()}
            
        basename = os.path.basename(selected_file)
        
        if self.current_image_basename is not None and self.current_image_basename != basename:
            self.chat_manager.export_and_reset(self.current_image_basename)
            self.doodles_state.clear()
            
        self.current_image_basename = basename
        
        sel_path_obj = Path(selected_file).resolve()
        if "references" in sel_path_obj.parts or "reference_library" in sel_path_obj.parts:
            image_url = f"/theaters/{self.theater_id}/references/{basename}"
        else:
            image_url = f"/theaters/{self.theater_id}/output/{basename}"

        # Ensure history is populated if empty
        if not self.shown_images_history:
            item = {
                "path": selected_file,
                "url": image_url,
                "prompt": prompt_text,
                "time": selected_time,
                "transition": getattr(self, "shown_image_transition", "fade") or "fade",
                "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3",
            }
            self.shown_images_history.append(item)
            formatted_history.append(item)

        res = {
            "latest": image_url,
            "time": selected_time,
            "prompt": prompt_text,
            "music": music_state,
            "doodles_enabled": self.doodles_enabled,
            "transition": getattr(self, "shown_image_transition", "fade") or "fade",
            "effect": getattr(self, "shown_image_effect", "gleam3") or "gleam3",
            "history": formatted_history,
            "tool_activity": self.get_tool_activity(),
            "agent_thought": self.get_agent_thought(),
        }
        logger.debug(f"[/api/latest] returning latest={res['latest']}, time={res['time']}, history_len={len(formatted_history)}, playlist={music_state['playlist']}")
        return res

    def export_theater_data(self, theater_dir: Optional[Path] = None) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Ensure current displayed image is saved and gather theater metadata, canvas data and files."""
        import json
        if self.shown_image_path and theater_dir:
            self.update_shown_image(
                self.shown_image_path,
                theater_id=theater_dir.name,
                transition=getattr(self, "shown_image_transition", "fade"),
                effect=getattr(self, "shown_image_effect", "gleam3"),
            )

        canvas_state = {
            "current_image_basename": self.current_image_basename,
            "shown_image_path": self.shown_image_path,
            "shown_image_prompt": self.shown_image_prompt,
            "shown_images_history": self.shown_images_history,
            "shown_image_transition": getattr(self, "shown_image_transition", "fade"),
            "shown_image_effect": getattr(self, "shown_image_effect", "gleam3"),
            "current_playlist": self.current_playlist,
            "current_playlist_tracks": self.current_playlist_tracks,
            "music_paused": self.music_paused,
            "doodles": list(self.doodles_state),
            "doodles_enabled": self.doodles_enabled,
            "chat_messages": self.chat_manager.get_messages(),
        }

        metadata = {}
        if theater_dir:
            theater_dir = Path(theater_dir).resolve()
            theater_dir.mkdir(parents=True, exist_ok=True)
            meta_file = theater_dir / "theater.json"
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
                logger.warning(f"Failed to update theater.json: {e}")

            # Remove legacy theater_state.json if it exists
            legacy_file = theater_dir / "theater_state.json"
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
        if theater_dir and theater_dir.exists():
            for f in theater_dir.rglob("*"):
                if f.is_file() and f.name not in ["theater.json", "theater_state.json"]:
                    rel_path = str(f.relative_to(theater_dir)).replace("\\", "/")
                    if rel_path not in seen_filenames:
                        seen_filenames.add(rel_path)
                        if "references" in f.parts:
                            category = "reference"
                        elif "output" in f.parts:
                            category = "output"
                        else:
                            category = str(f.parent.relative_to(theater_dir)).replace("\\", "/")

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

