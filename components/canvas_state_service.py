"""Application-level lifecycle and operations for session canvas state."""

from pathlib import Path
from typing import Any, Optional, Protocol

from fastapi import WebSocket

from components.canvas_state import CanvasStateManager


class _SessionMetadata(Protocol):
    session_id: str
    status: str


class CanvasStateDeployer(Protocol):
    base_dir: Path

    def list_sessions(self) -> list[_SessionMetadata]: ...


class CanvasStateService:
    """Own the lifecycle and application-level operations for session canvas state.

    ``CanvasStateManager`` remains the per-session state container; this service
    centralizes selecting, creating, and operating on those containers so route
    handlers and agent callbacks do not coordinate a module-level dictionary.
    """

    def __init__(self, deployer: CanvasStateDeployer):
        self.deployer = deployer
        self.states: dict[str, CanvasStateManager] = {}

    def _resolve_session_id(self, session_id: Optional[str]) -> str:
        if session_id:
            return session_id

        deployed = [session for session in self.deployer.list_sessions() if session.status == "deployed"]
        if deployed:
            return deployed[0].session_id

        non_default = next((sid for sid in self.states if sid != "default"), None)
        return non_default or "default"

    def get(self, session_id: Optional[str] = None) -> CanvasStateManager:
        """Return the requested session state, creating it on first use."""
        resolved_session_id = self._resolve_session_id(session_id)
        if resolved_session_id not in self.states:
            self.states[resolved_session_id] = CanvasStateManager(
                session_id=resolved_session_id,
                base_sessions_dir=self.deployer.base_dir,
            )
        return self.states[resolved_session_id]

    def update_playlist(self, playlist_name: str, tracks: list[str], session_id: Optional[str] = None) -> None:
        self.get(session_id).update_current_playlist(playlist_name, tracks)

    def pause_playlist(self, session_id: Optional[str] = None) -> None:
        self.get(session_id).pause_current_playlist()

    def resume_playlist(self, session_id: Optional[str] = None) -> None:
        self.get(session_id).resume_current_playlist()

    def show_image(
        self,
        file_path: str,
        session_id: Optional[str] = None,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> None:
        state = self.get(session_id)
        state.update_shown_image(
            file_path,
            session_id=state.session_id,
            transition=transition,
            effect=effect,
        )

    def add_chat_message(self, text: str, author: str = "agent", session_id: Optional[str] = None) -> None:
        self.get(session_id).add_chat_message(text, author=author)

    def set_tool_activity(
        self,
        tool: str,
        active: bool = True,
        session_id: Optional[str] = None,
        recent_seconds: float = 5.0,
    ) -> None:
        self.get(session_id).set_tool_activity(tool, active, recent_seconds)

    def latest_state(self, session_id: Optional[str] = None) -> dict[str, Any]:
        return self.get(session_id).get_latest_state()

    def chat_messages(self, session_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.get(session_id).chat_manager.get_messages()

    async def connect_doodle_websocket(self, websocket: WebSocket, session_id: Optional[str] = None) -> CanvasStateManager:
        """Register a doodle client and synchronize its current state."""
        state = self.get(session_id)
        state.register_websocket(websocket)
        await websocket.send_json({"type": "doodles_toggle", "enabled": state.doodles_enabled})
        for action in state.doodles_state:
            await websocket.send_json(action)
        return state

    async def apply_doodle_message(
        self,
        state: CanvasStateManager,
        data: dict[str, Any],
        sender: WebSocket,
    ) -> None:
        """Persist and broadcast one doodle protocol message."""
        if data.get("type") == "toggle_doodles":
            state.set_doodles_enabled(bool(data.get("enabled", True)))
            await state.broadcast_ws_message(
                {"type": "doodles_toggle", "enabled": state.doodles_enabled},
                sender=None,
            )
            return

        state.add_doodle(data)
        await state.broadcast_ws_message(data, sender=sender)

    async def toggle_microphone(self, session_id: Optional[str] = None) -> int:
        """Ask connected canvas clients to toggle their microphone input."""
        target_states = [self.get(session_id)] if session_id else list(self.states.values())
        count = 0
        for state in target_states:
            for websocket in list(state.active_ws_connections):
                try:
                    await websocket.send_json({"type": "toggle_mic"})
                    count += 1
                except Exception:
                    pass
        return count
