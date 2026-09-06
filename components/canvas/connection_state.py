"""Transient WebSocket connection state."""

import asyncio
from fastapi import WebSocket


class ConnectionState:
    def __init__(self) -> None:
        self.active_ws_connections: list[WebSocket] = []
        self.active_state_ws_connections: list[WebSocket] = []
        self.active_user_connections: dict[WebSocket, dict[str, object] | None] = {}
        self.state_ws_loop: asyncio.AbstractEventLoop | None = None
        self.state_revision = 0
        self.processed_doodle_message_ids: set[str] = set()

    def notify(self, *domains: str) -> None:
        """Record a bundled-state invalidation; transport owns delivery."""
        self.state_revision += 1
