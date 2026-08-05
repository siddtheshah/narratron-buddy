import asyncio

from google.adk.agents.live_request_queue import LiveRequest, LiveRequestQueue
from google.genai import types


class PriorityLiveRequestQueue(LiveRequestQueue):
    """
    Queue used to send LiveRequests with priority scheduling for orator audio events.

    Requests are delivered by explicit non-audio and audio/VAD states.
    """

    _NON_AUDIO = "non_audio"
    _AUDIO = "audio"

    def __init__(self):
        super().__init__()
        self._current_non_audio_queue: asyncio.Queue = asyncio.Queue()
        self._current_audio_queue: asyncio.Queue = asyncio.Queue()
        self._future_non_audio_queue: asyncio.Queue = asyncio.Queue()
        self._state = self._NON_AUDIO
        self._audio_turn_pending = False
        self._notify_event: asyncio.Event = asyncio.Event()

    def _get_current_audio_nowait(self) -> LiveRequest:
        """Return audio/VAD request and switch to non-audio after VAD end."""
        req = self._current_audio_queue.get_nowait()
        if req.activity_end is not None:
            self._state = self._NON_AUDIO
        return req

    def _queue_activity_start(self, req: LiveRequest) -> None:
        """Queue VAD start after the current non-audio batch."""
        self._queue_audio(req)
        if self._state == self._NON_AUDIO:
            self._audio_turn_pending = True

    def _queue_audio(self, req: LiveRequest) -> None:
        self._current_audio_queue.put_nowait(req)

    def _queue_non_audio(self, req: LiveRequest) -> None:
        if self._state == self._NON_AUDIO and self._audio_turn_pending:
            self._future_non_audio_queue.put_nowait(req)
        else:
            self._current_non_audio_queue.put_nowait(req)

    def send_realtime(self, blob: types.Blob) -> None:
        """Send realtime blob. Prioritize audio blobs over non-audio blobs."""
        is_audio = bool(blob and getattr(blob, "mime_type", "").startswith("audio/"))
        req = LiveRequest(blob=blob)
        if is_audio:
            self._queue_audio(req)
        else:
            self._queue_non_audio(req)
        self._notify_event.set()

    def send_content(self, content: types.Content) -> None:
        """Send content (e.g. system notifications, canvas updates, user text)."""
        req = LiveRequest(content=content)
        self._queue_non_audio(req)
        self._notify_event.set()

    def send_activity_start(self) -> None:
        """Sends an activity start signal marking user input start."""
        req = LiveRequest(activity_start=types.ActivityStart())
        self._queue_activity_start(req)
        self._notify_event.set()

    def send_activity_end(self) -> None:
        """Sends an activity end signal."""
        req = LiveRequest(activity_end=types.ActivityEnd())
        self._queue_audio(req)
        self._notify_event.set()

    def close(self) -> None:
        """Close signal for request queue."""
        req = LiveRequest(close=True)
        self._queue_non_audio(req)
        self._notify_event.set()

    def send(self, req: LiveRequest) -> None:
        """Send arbitrary LiveRequest, routing it according to queue state."""
        is_audio = bool(req.blob and getattr(req.blob, "mime_type", "").startswith("audio/"))
        if req.activity_start is not None:
            self._queue_activity_start(req)
        elif req.activity_end is not None:
            self._queue_audio(req)
        elif is_audio:
            self._queue_audio(req)
        else:
            self._queue_non_audio(req)
        self._notify_event.set()

    async def get(self) -> LiveRequest:
        """
        Alternate between the current non-audio and audio queues. The future
        non-audio queue is promoted when the audio queue becomes active.
        """
        while True:
            if self._state == self._NON_AUDIO:
                if not self._current_non_audio_queue.empty():
                    return self._current_non_audio_queue.get_nowait()

                if self._current_audio_queue.empty():
                    self._notify_event.clear()
                    if not self._current_non_audio_queue.empty() or not self._current_audio_queue.empty():
                        continue
                    await self._notify_event.wait()
                    continue

                self._state = self._AUDIO
                self._audio_turn_pending = False
                self._current_non_audio_queue = self._future_non_audio_queue
                self._future_non_audio_queue = asyncio.Queue()
                continue

            self._notify_event.clear()
            if not self._current_audio_queue.empty():
                return self._get_current_audio_nowait()

            await self._notify_event.wait()
