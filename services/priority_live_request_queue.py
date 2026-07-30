import asyncio
import logging
import time
from typing import Optional

from google.adk.agents.live_request_queue import LiveRequest, LiveRequestQueue
from google.genai import types

logger = logging.getLogger(__name__)


class PriorityLiveRequestQueue(LiveRequestQueue):
    """
    Queue used to send LiveRequests with priority scheduling for orator audio events.

    Audio chunks from the orator always have priority while input is being detected,
    with a retention window (default at least 0.5s) holding priority before yielding to
    system notifications (tool cooldowns, canvas updates, etc.).
    """

    def __init__(self, retention_window: float = 0.5):
        super().__init__()
        self.retention_window: float = retention_window
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._system_queue: asyncio.Queue = asyncio.Queue()
        self._last_input_time: Optional[float] = None
        self._notify_event: asyncio.Event = asyncio.Event()

    def record_input_detected(self) -> None:
        """Mark that orator input has been detected to start or renew the priority window."""
        self._last_input_time = time.monotonic()
        self._notify_event.set()

    def send_realtime(self, blob: types.Blob) -> None:
        """Send realtime blob. Prioritize audio blobs over non-audio blobs."""
        is_audio = bool(blob and getattr(blob, "mime_type", "").startswith("audio/"))
        req = LiveRequest(blob=blob)
        if is_audio:
            self._last_input_time = time.monotonic()
            self._audio_queue.put_nowait(req)
        else:
            self._system_queue.put_nowait(req)
        self._notify_event.set()

    def send_content(self, content: types.Content) -> None:
        """Send content (e.g. system notifications, canvas updates, user text)."""
        req = LiveRequest(content=content)
        self._system_queue.put_nowait(req)
        self._notify_event.set()

    def send_activity_start(self) -> None:
        """Sends an activity start signal marking user input start."""
        self._last_input_time = time.monotonic()
        req = LiveRequest(activity_start=types.ActivityStart())
        self._audio_queue.put_nowait(req)
        self._notify_event.set()

    def send_activity_end(self) -> None:
        """Sends an activity end signal."""
        req = LiveRequest(activity_end=types.ActivityEnd())
        self._audio_queue.put_nowait(req)
        self._notify_event.set()

    def close(self) -> None:
        """Close signal for request queue."""
        req = LiveRequest(close=True)
        self._system_queue.put_nowait(req)
        self._notify_event.set()

    def send(self, req: LiveRequest) -> None:
        """Send arbitrary LiveRequest, routing audio/activity to audio queue."""
        is_audio = bool(req.blob and getattr(req.blob, "mime_type", "").startswith("audio/"))
        if is_audio or req.activity_start is not None:
            self._last_input_time = time.monotonic()
            self._audio_queue.put_nowait(req)
        else:
            self._system_queue.put_nowait(req)
        self._notify_event.set()

    async def get(self) -> LiveRequest:
        """
        Dequeue next LiveRequest according to priority:
        1. If audio queue has items, return audio chunk immediately.
        2. If audio queue is empty but priority window (retention_window) is active,
           wait up to remaining window for new audio chunks before yielding to system queue.
        3. If priority window is inactive (or expired) and system queue has items, return system item.
        4. If both queues are empty, wait for next event notification.
        """
        while True:
            # 1. Immediate priority for audio chunks
            if not self._audio_queue.empty():
                return self._audio_queue.get_nowait()

            now = time.monotonic()
            is_priority_active = False
            remaining_window = 0.0

            if self._last_input_time is not None:
                elapsed = now - self._last_input_time
                if elapsed < self.retention_window:
                    is_priority_active = True
                    remaining_window = self.retention_window - elapsed

            if is_priority_active:
                self._notify_event.clear()
                # Re-check audio queue after clearing event
                if not self._audio_queue.empty():
                    return self._audio_queue.get_nowait()

                try:
                    await asyncio.wait_for(self._notify_event.wait(), timeout=remaining_window)
                    # Woken up by new item or input trigger; loop back to check audio queue first
                    continue
                except asyncio.TimeoutError:
                    # Priority window expired; loop back, next iteration will yield to system queue
                    continue

            # 2. Priority not active -> yield to system notifications
            if not self._system_queue.empty():
                return self._system_queue.get_nowait()

            # 3. Both queues empty -> wait for next event
            self._notify_event.clear()
            # Double check queues after clearing event
            if not self._audio_queue.empty():
                return self._audio_queue.get_nowait()
            if not self._system_queue.empty():
                return self._system_queue.get_nowait()

            await self._notify_event.wait()
