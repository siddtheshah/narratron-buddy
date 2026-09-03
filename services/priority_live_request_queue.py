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
    DEFAULT_LIVE_TOOL_BUDGET = 3

    def __init__(self, live_tool_budget: int = DEFAULT_LIVE_TOOL_BUDGET):
        super().__init__()
        self._current_non_audio_queue: asyncio.Queue = asyncio.Queue()
        self._current_audio_queue: asyncio.Queue = asyncio.Queue()
        self._future_non_audio_queue: asyncio.Queue = asyncio.Queue()
        self._state = self._NON_AUDIO
        self._audio_turn_pending = False
        self._notify_event: asyncio.Event = asyncio.Event()
        self._live_tool_budget = max(0, int(live_tool_budget))
        self._remaining_live_tool_budget = 0
        self._post_vad_window_active = False
        self._defer_non_audio_until_next_vad = False

    @property
    def remaining_live_tool_budget(self) -> int:
        """Model function calls still allowed before post-VAD state changes."""
        return self._remaining_live_tool_budget

    @property
    def live_tool_window_active(self) -> bool:
        """Whether the model is in the post-speech tool-call window."""
        return self._post_vad_window_active and self._remaining_live_tool_budget > 0

    def record_model_tool_calls(self, count: int = 1) -> None:
        """Charge model-emitted function calls to the active post-VAD window.

        This deliberately observes function-call events rather than wrapping
        tool implementations: the budget governs how long the live model can
        continue receiving notifications, not whether a tool may execute.
        """
        if not self._post_vad_window_active:
            return
        self._remaining_live_tool_budget = max(
            0,
            self._remaining_live_tool_budget - max(0, count),
        )
        if self._remaining_live_tool_budget == 0:
            self._post_vad_window_active = False
            if self._live_tool_budget == 0:
                # A zero budget disables the post-VAD window altogether, so
                # fall back to ordinary non-audio delivery rather than holding
                # notifications forever.
                self._state = self._NON_AUDIO
                self._defer_non_audio_until_next_vad = False
                self._promote_future_non_audio()
            else:
                self._defer_non_audio_until_next_vad = True
                self._defer_current_non_audio()
            self._notify_event.set()

    def _promote_future_non_audio(self) -> None:
        """Make notifications deferred during audio available for delivery."""
        if self._future_non_audio_queue.empty():
            return
        promoted = asyncio.Queue()
        while not self._current_non_audio_queue.empty():
            promoted.put_nowait(self._current_non_audio_queue.get_nowait())
        while not self._future_non_audio_queue.empty():
            promoted.put_nowait(self._future_non_audio_queue.get_nowait())
        self._current_non_audio_queue = promoted

    def _defer_current_non_audio(self) -> None:
        """Hold any undispatched current notifications for the next VAD turn."""
        if self._current_non_audio_queue.empty():
            return
        deferred = asyncio.Queue()
        while not self._current_non_audio_queue.empty():
            deferred.put_nowait(self._current_non_audio_queue.get_nowait())
        while not self._future_non_audio_queue.empty():
            deferred.put_nowait(self._future_non_audio_queue.get_nowait())
        self._future_non_audio_queue = deferred

    def _get_current_audio_nowait(self) -> LiveRequest:
        """Return the next audio/VAD request and arm post-VAD notifications."""
        req = self._current_audio_queue.get_nowait()
        if req.activity_start is not None:
            self._post_vad_window_active = False
        elif req.activity_end is not None:
            # Leave audio priority in place until the model has used its
            # bounded tool-call allowance.  During that window it can keep
            # receiving tool/result notifications before ordinary non-audio
            # scheduling resumes.
            self._remaining_live_tool_budget = self._live_tool_budget
            self._post_vad_window_active = True
            self._defer_non_audio_until_next_vad = False
            self._promote_future_non_audio()
            if self._remaining_live_tool_budget == 0:
                self.record_model_tool_calls(0)
        return req

    def _queue_activity_start(self, req: LiveRequest) -> None:
        """Begin audio priority immediately and defer queued notifications."""
        self._post_vad_window_active = False
        self._remaining_live_tool_budget = 0
        self._defer_non_audio_until_next_vad = False
        self._defer_current_non_audio()
        self._state = self._AUDIO
        self._audio_turn_pending = False
        self._queue_audio(req)

    def _queue_audio(self, req: LiveRequest) -> None:
        self._current_audio_queue.put_nowait(req)

    def _queue_non_audio(self, req: LiveRequest) -> None:
        if (
            self._defer_non_audio_until_next_vad
            or (self._state == self._NON_AUDIO and self._audio_turn_pending)
        ):
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
        Alternate between the current non-audio and audio queues. After a VAD
        end, current notifications remain eligible until the model spends its
        tool-call budget; later notifications wait in the future queue.
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
            if (
                self._post_vad_window_active
                and not self._current_non_audio_queue.empty()
            ):
                return self._current_non_audio_queue.get_nowait()

            if not self._current_audio_queue.empty():
                return self._get_current_audio_nowait()

            if (
                self._post_vad_window_active
                and not self._future_non_audio_queue.empty()
            ):
                return self._future_non_audio_queue.get_nowait()

            await self._notify_event.wait()
