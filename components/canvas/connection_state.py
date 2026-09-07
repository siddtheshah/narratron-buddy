"""Transient WebSocket connection state."""

import asyncio
from fastapi import WebSocket
from typing import Any
from typing import Optional
import logging

logger = logging.getLogger(__name__)


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
    
    def register_websocket(self, websocket: WebSocket, user: Optional[dict[str, Any]] = None):
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

    def register_state_websocket(self, websocket: WebSocket):
        if websocket not in self.active_state_ws_connections:
            self.active_state_ws_connections.append(websocket)
        self.state_ws_loop = asyncio.get_running_loop()

    def unregister_state_websocket(self, websocket: WebSocket):
        if websocket in self.active_state_ws_connections:
            self.active_state_ws_connections.remove(websocket)
    
    async def broadcast_state_ws_message(self, message: dict[str, Any]):
        """Send a state notification to every client concurrently.

        WebSocket I/O is already asynchronous and must remain on the event
        loop that owns the socket.  Starting all sends before awaiting any of
        them prevents one slow recipient from serializing delivery to the
        rest of the canvas audience.
        """
        connections = list(self.active_state_ws_connections)
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in connections),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results):
            if isinstance(result, BaseException):
                self.unregister_state_websocket(connection)

    def get_active_viewers(self) -> list[dict[str, Any]]:
        """Return list of distinct authenticated users currently connected as active canvas viewers."""
        if not hasattr(self, "active_user_connections"):
            return []
        users_by_id = {}
        for u in self.active_user_connections.values():
            if u and "id" in u:
                users_by_id[u["id"]] = {"id": u["id"], "username": u.get("username", "")}
        return list(users_by_id.values())


    async def broadcast_ws_message(self, message: dict[str, Any], sender: Optional[WebSocket] = None):
        """Fan out a canvas event concurrently without blocking on each peer.

        This deliberately does not use a worker thread: ``send_json`` is
        event-loop-bound async I/O.  ``gather`` lets every socket make
        progress concurrently while retaining a completion point for callers
        that need delivery work to finish before they acknowledge an action.
        """
        connections = [
            connection
            for connection in list(self.active_ws_connections)
            if connection != sender
        ]
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in connections),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results):
            if isinstance(result, BaseException):
                self.unregister_websocket(connection)

    def broadcast(self, message: dict[str, Any]) -> None:
        logger.info("[ConnectionState] Broadcasting message")
        loop = self.state_ws_loop
        if not self.active_state_ws_connections or not loop or loop.is_closed():
            return
        try:
            running_loop = None
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            if running_loop is loop:
                loop.create_task(self.broadcast_state_ws_message(message))
            else:
                asyncio.run_coroutine_threadsafe(self.broadcast_state_ws_message(message), loop)
        except RuntimeError:
            pass
