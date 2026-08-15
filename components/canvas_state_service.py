"""Application-level lifecycle and operations for theater canvas state."""

from typing import Any, Optional

from fastapi import WebSocket

from components.canvas_state import CanvasStateManager
from components.theater_manager import TheaterManager


class CanvasStateService:
    """Own the lifecycle and application-level operations for theater canvas state.

    ``CanvasStateManager`` remains the per-theater state container; this service
    centralizes selecting, creating, and operating on those containers so route
    handlers and agent callbacks do not coordinate a module-level dictionary.
    """

    def __init__(self, theater_manager: TheaterManager):
        self.theater_manager = theater_manager
        self.states: dict[str, CanvasStateManager] = {}

    def _resolve_theater_id(self, theater_id: Optional[str]) -> str:
        if theater_id:
            return theater_id

        deployed = [theater for theater in self.theater_manager.list_theaters() if theater.status == "deployed"]
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
                theater_manager=self.theater_manager,
            )
        return self.states[resolved_theater_id]

    def update_music(self, music_id: str, tracks: list[str], theater_id: Optional[str] = None) -> None:
        self.get(theater_id).update_current_music(music_id, tracks)

    def pause_music(self, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).pause_current_music()

    def resume_music(self, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).resume_current_music()

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

    def show_triframe(self, frame_paths: list[str], theater_id: Optional[str] = None) -> None:
        state = self.get(theater_id)
        state.show_triframe(frame_paths, theater_id=state.theater_id)

    def show_triframe_if_current(
        self,
        frame_paths: list[str],
        expected_image_revision: int,
        theater_id: Optional[str] = None,
    ) -> bool:
        """Show an animation only if no newer image has replaced its source state."""
        state = self.get(theater_id)
        if state.image_revision != expected_image_revision:
            return False
        state.show_triframe(frame_paths, theater_id=state.theater_id)
        return True

    def add_chat_message(self, text: str, author: str = "agent", theater_id: Optional[str] = None, profile_username: Optional[str] = None, profile_color: Optional[str] = None) -> None:
        self.get(theater_id).add_chat_message(text, author=author, profile_username=profile_username, profile_color=profile_color)

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

    def add_suggestion(self, author: str, text: str, theater_id: Optional[str] = None, profile_username: Optional[str] = None, profile_color: Optional[str] = None) -> dict:
        state = self.get(theater_id)
        suggestion = state.chat_manager.add_suggestion(author, text, profile_username=profile_username, profile_color=profile_color)
        state._notify_state_changed("chat", "suggestions")
        return suggestion

    def withdraw_suggestion(self, author: str, theater_id: Optional[str] = None) -> bool:
        state = self.get(theater_id)
        changed = state.chat_manager.withdraw_suggestion(author)
        if changed:
            state._notify_state_changed("chat", "suggestions")
        return changed

    def upvote_suggestion(self, voter: str, target_author: str, theater_id: Optional[str] = None) -> bool:
        state = self.get(theater_id)
        changed = state.chat_manager.upvote_suggestion(voter, target_author)
        if changed:
            state._notify_state_changed("suggestions")
        return changed

    def get_suggestions(self, theater_id: Optional[str] = None) -> list[dict]:
        return self.get(theater_id).chat_manager.get_suggestions()

    def consume_top_suggestion(self, theater_id: Optional[str] = None) -> dict | None:
        return self.get(theater_id).consume_top_suggestion()

    def set_viewer_collab_enabled(self, enabled: bool, theater_id: Optional[str] = None) -> None:
        self.get(theater_id).set_viewer_collab_enabled(enabled)

    def get_named_elements(self, theater_id: Optional[str] = None) -> list[dict[str, str]]:
        return self.get(theater_id).get_named_elements()

    def set_named_elements(self, elements: list[dict[str, str]], theater_id: Optional[str] = None) -> None:
        self.get(theater_id).set_named_elements(elements)

    async def connect_doodle_websocket(
        self, websocket: WebSocket, theater_id: Optional[str] = None, user: Optional[dict] = None
    ) -> CanvasStateManager:
        """Register a doodle client and synchronize its current state."""
        state = self.get(theater_id)
        state.register_websocket(websocket, user=user)
        await websocket.send_json({"type": "doodles_toggle", "enabled": state.doodles_enabled})
        # One compact snapshot avoids a WebSocket frame and repeated style
        # fields per historical segment. Persisted data stays in its original,
        # backwards-compatible segment format.
        await websocket.send_json({"type": "doodle_snapshot", "batches": state.get_doodle_snapshot_batches()})
        return state

    async def connect_state_websocket(
        self, websocket: WebSocket, theater_id: Optional[str] = None
    ) -> CanvasStateManager:
        """Register a notification-only canvas-state WebSocket client."""
        state = self.get(theater_id)
        state.register_state_websocket(websocket)
        await websocket.send_json({"type": "state_ready", "revision": state.state_revision})
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
        message_id = data.get("client_message_id")
        message_id = message_id if isinstance(message_id, str) and len(message_id) <= 128 else None

        async def acknowledge() -> None:
            if not message_id:
                return
            state._processed_doodle_message_ids.add(message_id)
            # Keep bounded transient de-duplication state for long sessions.
            if len(state._processed_doodle_message_ids) > 2_000:
                state._processed_doodle_message_ids.clear()
                state._processed_doodle_message_ids.add(message_id)
            await sender.send_json({"type": "doodle_ack", "client_message_id": message_id})

        if message_id and message_id in state._processed_doodle_message_ids:
            await sender.send_json({"type": "doodle_ack", "client_message_id": message_id})
            return

        if data.get("type") == "toggle_doodles":
            state.set_doodles_enabled(bool(data.get("enabled", True)))
            await state.broadcast_ws_message(
                {"type": "doodles_toggle", "enabled": state.doodles_enabled},
                sender=None,
            )
            await acknowledge()
            return

        if data.get("type") == "draw_batch":
            color = data.get("color")
            size = data.get("size", 3)
            points = data.get("points")
            if not isinstance(points, list) or len(points) < 4 or len(points) % 2 or len(points) > 400:
                return
            try:
                points = [float(point) for point in points]
                size = float(size)
            except (TypeError, ValueError):
                return
            if not all(0 <= point <= 1 for point in points) or not 1 <= size <= 100:
                return

            actions = [
                {
                    "type": "draw", "x0": points[index], "y0": points[index + 1],
                    "x1": points[index + 2], "y1": points[index + 3],
                    "color": color, "size": size,
                }
                for index in range(0, len(points) - 2, 2)
            ]
            state.add_doodles(actions)
            await state.broadcast_ws_message(
                {"type": "draw_batch", "color": color, "size": size, "points": points},
                sender=sender,
            )
            await acknowledge()
            return

        if data.get("type") == "clear":
            state.add_doodle(data)
            await state.broadcast_ws_message(data, sender=sender)
            await acknowledge()
            return

        # Accept the pre-batching protocol during rolling deploys and from
        # browser tabs that still have the previous canvas script loaded.
        if data.get("type") == "draw":
            state.add_doodle(data)
            await state.broadcast_ws_message(data, sender=sender)
            await acknowledge()

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
