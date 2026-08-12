"""Bounded process-local cache for resolved theater access grants."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional


_TTL_SECONDS = 60.0
_MAX_ENTRIES = 4_096


class TheaterAccessCache:
    """Cache ``(principal, theater_id)`` authorization results for a short TTL."""

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, str], tuple[float, Optional[dict], bool]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.invalidations = 0

    @staticmethod
    def principal_key(*, user_id: Optional[int], join_key: Optional[str]) -> str:
        # Hash the entire principal because it can contain a join key.  User
        # identity is included so a logged-in viewer and a join-key viewer do
        # not share authorization decisions.
        material = f"user:{user_id if user_id is not None else ''}|join:{join_key or ''}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get_or_resolve(
        self,
        theater_id: str,
        principal: str,
        resolve: Callable[[], tuple[Optional[dict], bool]],
    ) -> tuple[Optional[dict], bool]:
        key = (theater_id, principal)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self.hits += 1
                self._entries.move_to_end(key)
                return (dict(entry[1]) if entry[1] else None, entry[2])
            self.misses += 1
            self._entries.pop(key, None)

        deployment, allowed = resolve()
        deployment_copy = dict(deployment) if deployment else None
        with self._lock:
            self._entries[key] = (time.monotonic() + _TTL_SECONDS, deployment_copy, allowed)
            self._entries.move_to_end(key)
            while len(self._entries) > _MAX_ENTRIES:
                self._entries.popitem(last=False)
                self.evictions += 1
        return (dict(deployment_copy) if deployment_copy else None, allowed)

    def invalidate_theater(self, theater_id: str) -> None:
        with self._lock:
            keys = [key for key in self._entries if key[0] == theater_id]
            for key in keys:
                self._entries.pop(key, None)
            self.invalidations += len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


theater_access_cache = TheaterAccessCache()
