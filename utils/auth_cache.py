"""Small, bounded cache for authentication lookups.

The cache is intentionally process-local.  Deployments with multiple web
instances should replace this with a shared Redis/Memorystore implementation.
Raw bearer tokens are never retained as cache keys.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional


_VALID_TTL_SECONDS = 60.0
_INVALID_TTL_SECONDS = 5.0
_MAX_ENTRIES = 2_048


class AuthSessionCache:
    """Thread-safe TTL cache keyed by a SHA-256 token digest."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, tuple[float, Optional[dict], Optional[int]]] = OrderedDict()
        self._keys_by_user: dict[int, set[str]] = defaultdict(set)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.stale_account_invalidations = 0

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _valid_ttl(user: dict, now: float) -> float:
        expires_at = user.get("expires_at")
        if not expires_at:
            return _VALID_TTL_SECONDS
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return min(_VALID_TTL_SECONDS, max(0.0, expiry.timestamp() - now))
        except (TypeError, ValueError):
            return _VALID_TTL_SECONDS

    def get_or_validate(self, token: str, validate: Callable[[], Optional[dict]]) -> Optional[dict]:
        key = self._key(token)
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry[0] > now:
                self.hits += 1
                self._entries.move_to_end(key)
                return dict(entry[1]) if entry[1] else None
            self.misses += 1
            if entry:
                self._remove(key)

        user = validate()
        now = time.time()
        ttl = self._valid_ttl(user, now) if user else _INVALID_TTL_SECONDS
        if ttl <= 0:
            return None
        user_copy = dict(user) if user else None
        user_id = user_copy.get("id") if user_copy else None
        with self._lock:
            self._entries[key] = (now + ttl, user_copy, user_id)
            self._entries.move_to_end(key)
            if user_id is not None:
                self._keys_by_user[user_id].add(key)
            while len(self._entries) > _MAX_ENTRIES:
                self._remove(next(iter(self._entries)))
                self.evictions += 1
        return dict(user_copy) if user_copy else None

    def invalidate_token(self, token: str) -> None:
        with self._lock:
            self._remove(self._key(token))

    def invalidate_user(self, user_id: int) -> None:
        with self._lock:
            keys = tuple(self._keys_by_user.get(user_id, ()))
            for key in keys:
                self._remove(key)
            self.stale_account_invalidations += len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._keys_by_user.clear()

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry and entry[2] is not None:
            keys = self._keys_by_user[entry[2]]
            keys.discard(key)
            if not keys:
                self._keys_by_user.pop(entry[2], None)


auth_session_cache = AuthSessionCache()
