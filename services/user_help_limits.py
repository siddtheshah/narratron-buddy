"""Bound free help traffic across canvas and front-page entry points."""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
import math
from threading import Lock
import time


class HelpRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UserHelpLimits:
    """Thread-safe, per-process limits; rejected calls never consume model work."""

    cooldown_seconds = 15
    hourly_principal_limit = 20
    hourly_address_limit = 60
    hourly_total_limit = 300
    max_concurrent = 3
    window_seconds = 3600
    max_tracked_keys = 10_000

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[str, deque[float]] = {}
        self._total_requests: deque[float] = deque()
        self._active: set[str] = set()

    @contextmanager
    def claim(self, principal: str, address: str) -> Iterator[None]:
        """Reserve a bounded research slot, releasing it even on cancellation."""
        with self._lock:
            self._reserve(principal, address, time.monotonic())
        try:
            yield
        finally:
            with self._lock:
                self._active.discard(principal)

    def _reserve(self, principal: str, address: str, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, history in list(self._requests.items()):
            while history and history[0] <= cutoff:
                history.popleft()
            if not history:
                del self._requests[key]
        while self._total_requests and self._total_requests[0] <= cutoff:
            self._total_requests.popleft()

        if principal in self._active or len(self._active) >= self.max_concurrent:
            raise HelpRateLimitError("Help is busy. Please wait before asking another question.", self.cooldown_seconds)
        history = self._requests.get(principal)
        if history and now - history[-1] < self.cooldown_seconds:
            remaining = math.ceil(self.cooldown_seconds - (now - history[-1]))
            raise HelpRateLimitError(f"Please wait {remaining} seconds before asking another help question.", remaining)

        for key, limit in ((principal, self.hourly_principal_limit), (address, self.hourly_address_limit)):
            history = self._requests.get(key)
            if history and len(history) >= limit:
                remaining = max(1, math.ceil(history[0] + self.window_seconds - now))
                raise HelpRateLimitError("The hourly help limit has been reached. Please try again later.", remaining)
        if len(self._total_requests) >= self.hourly_total_limit:
            remaining = max(1, math.ceil(self._total_requests[0] + self.window_seconds - now))
            raise HelpRateLimitError("Free help has reached its hourly capacity. Please try again later.", remaining)
        keys = {principal, address}
        if len(self._requests) + len(keys.difference(self._requests)) > self.max_tracked_keys:
            raise HelpRateLimitError("Help is busy. Please try again later.", 60)
        for key in keys:
            self._requests.setdefault(key, deque()).append(now)
        self._total_requests.append(now)
        self._active.add(principal)
