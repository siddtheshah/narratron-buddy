"""Application-level lifecycle and operations for theater canvas state."""

from pathlib import Path
from typing import Any, Optional, Protocol

from fastapi import WebSocket

from components.canvas_state import CanvasStateManager


class _TheaterMetadata(Protocol):
    theater_id: str
    status: str


class CanvasStateDeployer(Protocol):
    base_dir: Path

    def list_theaters(self) -> list[_TheaterMetadata]: ...


class CanvasStateService:
    """Own the lifecycle and application-level operations for theater canvas state.

    ``CanvasStateManager`` remains the per-theater state container; this service
    centralizes selecting, creating, and operating on those containers so route
    handlers and agent callbacks do not coordinate a module-level dictionary.
    """

    def __init__(self, deployer: CanvasStateDeployer):
        self.deployer = deployer
        self.states: dict[str, CanvasStateManager] = {}

    def _resolve_theater_id(self, theater_id: Optional[str]) -> str:
        if theater_id:
            return theater_id

        deployed = [theater for theater in self.deployer.list_theaters() if theater.status == "deployed"]
        if deployed:
            return deployed[0].theater_id

        non_default = next((sid for sid in self.states if sid != "default"), None)
        return non_default or "default"

    def get(self, theater_id: Optional[str] = None) -> CanvasStateManager:
        """Return the requested theater state, creating it on first use."""
        resolved_theater_id = self._resolve_theater_id(theater_id)
        if resolved_theater_id not in self.states:
            self.states[resolved_theater_id] = CanvasStateManager(
                theater_id=resolved_theater_id,
                base_theaters_dir=self.deployer.base_dir,
            )
        return self.states[resolved_theater_id]

    def update_playlist(self, playlist_name: str, tracks: list[str], theater_id: Optional[str] = None) -> None:
        self.get(theater_id).update_current_playlist(playlist_name, tracks)

    def pause_playlist(self, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).pause_current_playlist()

    def resume_playlist(self, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).resume_current_playlist()

    def show_image(
        self,
        file_path: str,
        theater_id: Optional[str] = None,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> None:
        state = self.get(theater_id)
        state.update_shown_image(
            file_path,
            theater_id=state.theater_id,
            transition=transition,
            effect=effect,
        )

    def add_chat_message(self, text: str, author: str = "agent", theater_id: Optional[str] = None) -> None:
        self.get(theater_id).add_chat_message(text, author=author)

    def set_agent_thought(self, text: str, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).set_agent_thought(text)

    def set_tool_activity(
        self,
        tool: str,
        active: bool = True,
        theater_id: Optional[str] = None,
        recent_seconds: float = 5.0,
    ) -> None:
        self.get(theater_id).set_tool_activity(tool, active, recent_seconds)

    def latest_state(self, theater_id: Optional[str] = None) -> dict[str, Any]:
        return self.get(theater_id).get_latest_state()

    def chat_messages(self, theater_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.get(theater_id).chat_manager.get_messages()

    # ------------------------------------------------------------------
    # Viewer Collaboration — suggestion management
    # ------------------------------------------------------------------

    def add_suggestion(self, author: str, text: str, theater_id: Optional[str] = None) -> dict:
        return self.get(theater_id).chat_manager.add_suggestion(author, text)

    def withdraw_suggestion(self, author: str, theater_id: Optional[str] = None) -> bool:
        return self.get(theater_id).chat_manager.withdraw_suggestion(author)

    def upvote_suggestion(self, voter: str, target_author: str, theater_id: Optional[str] = None) -> bool:
        return self.get(theater_id).chat_manager.upvote_suggestion(voter, target_author)

    def get_suggestions(self, theater_id: Optional[str] = None) -> list[dict]:
        return self.get(theater_id).chat_manager.get_suggestions()

    def consume_top_suggestion(self, theater_id: Optional[str] = None) -> dict | None:
        return self.get(theater_id).chat_manager.consume_top_suggestion()

    def set_viewer_collab_enabled(self, enabled: bool, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).set_viewer_collab_enabled(enabled)


    async def connect_doodle_websocket(
        self, websocket: WebSocket, theater_id: Optional[str] = None, user: Optional[dict] = None
    ) -> CanvasStateManager:
        """Register a doodle client and synchronize its current state."""
        state = self.get(theater_id)
        state.register_websocket(websocket, user=user)
        await websocket.send_json({"type": "doodles_toggle", "enabled": state.doodles_enabled})
        for action in state.doodles_state:
            await websocket.send_json(action)
        return state

    async def broadcast_baton_update(self, theater_id: str, baton_state: dict[str, Any]) -> None:
        """Broadcast updated baton state to all connected canvas websockets for theater."""
        state = self.get(theater_id)
        payload = {
            "type": "baton_state",
            "baton_state": baton_state,
            "active_viewers": state.get_active_viewers(),
        }
        await state.broadcast_ws_message(payload)


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

    async def toggle_microphone(self, theater_id: Optional[str] = None) -> int:
        """Ask connected canvas clients to toggle their microphone input."""
        target_states = [self.get(theater_id)] if theater_id else list(self.states.values())
        count = 0
        for state in target_states:
            for websocket in list(state.active_ws_connections):
                try:
                    await websocket.send_json({"type": "toggle_mic"})
                    count += 1
                except Exception:
                    pass
        return count
