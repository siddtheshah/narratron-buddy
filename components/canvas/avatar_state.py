"""Avatar manifest metadata and transient canvas action state."""

from __future__ import annotations

import json
import logging
import re
import time
from _thread import RLock
from collections.abc import Callable
from threading import RLock as create_rlock

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from components.canvas.locking import synchronized
from components.theater_manager import Theater
from components.theater_package import safe_asset_path

logger = logging.getLogger(__name__)

_AVATAR_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)


class AvatarConfig(BaseModel):
    """Theater YAML configuration for the canvas companion avatar."""

    enabled: bool = False
    package: str = ""

    model_config = ConfigDict(extra="ignore")


class AvatarActionSpec(BaseModel):
    """Single playable action declared in an avatar package's avatar.json."""

    id: str
    label: str = ""
    description: str = ""
    clip: str = ""
    loop: bool = False
    light_color: str = Field(default="", alias="lightColor")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AvatarManifestSpec(BaseModel):
    """Subset of avatar.json needed on the server to validate and select actions."""

    id: str = ""
    title: str = ""
    idle: str = "idle"
    actions: list[AvatarActionSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class AvatarState:
    """Tracks the theater's enabled avatar manifest and active canvas action."""

    def __init__(
        self,
        theater: Theater,
        notify_changed: Callable[..., None],
        *,
        lock: RLock | None = None,
    ) -> None:
        self._theater = theater
        self._notify_changed = notify_changed
        self.canvas_lock: RLock = lock if lock is not None else create_rlock()
        self.enabled: bool = False
        self.package: str = ""
        self.title: str = ""
        self.idle_action: str = ""
        self.actions: tuple[AvatarActionSpec, ...] = ()
        self._action_ids: frozenset[str] = frozenset()
        self.current_action: str | None = None
        self.action_sequence: int = 0
        self.action_time: float = 0.0
        self._load_manifest()

    def _load_manifest(self) -> None:
        config = self._theater.config() or {}
        raw_avatar = config.get("avatar")
        if raw_avatar is None:
            return
        try:
            avatar_config = AvatarConfig.model_validate(raw_avatar)
        except ValidationError as exc:
            logger.warning(
                "Invalid avatar config for theater %s: %s",
                self._theater.theater_id,
                exc,
            )
            return
        package = avatar_config.package.strip()
        if not avatar_config.enabled or not package or not _AVATAR_PACKAGE_RE.match(package):
            return
        manifest_path = safe_asset_path(
            self._theater.directory(),
            f"avatars/{package}/avatar.json",
        )
        if not manifest_path.is_file():
            logger.warning(
                "Avatar manifest missing for theater %s package '%s': %s",
                self._theater.theater_id,
                package,
                manifest_path,
            )
            return
        try:
            manifest = AvatarManifestSpec.model_validate(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning(
                "Failed to load avatar manifest for theater %s package '%s': %s",
                self._theater.theater_id,
                package,
                exc,
            )
            return
        deduped_actions: list[AvatarActionSpec] = []
        seen_ids: set[str] = set()
        for action in manifest.actions:
            clean_id = action.id.strip()
            if not clean_id or clean_id in seen_ids:
                continue
            seen_ids.add(clean_id)
            deduped_actions.append(action.model_copy(update={"id": clean_id}))
        self.enabled = True
        self.package = package
        self.title = manifest.title.strip() or package
        self.idle_action = manifest.idle.strip()
        self.actions = tuple(deduped_actions)
        self._action_ids = frozenset(seen_ids)

    @synchronized
    def set_action(self, action_id: str) -> bool:
        """Record a new avatar action and notify clients, or ignore when avatar is disabled."""
        if not self.enabled:
            return False
        normalized = action_id.strip()
        if not normalized or normalized not in self._action_ids:
            raise ValueError(
                f"Unknown avatar action '{action_id}' for avatar package '{self.package}'. "
                f"Available actions: {sorted(self._action_ids)}."
            )
        self.current_action = normalized
        self.action_sequence += 1
        self.action_time = time.time()
        self._notify_changed("latest")
        return True

    @synchronized
    def payload(self) -> dict[str, JsonValue]:
        """Serialize the transient avatar action state for /api/latest."""
        return {
            "enabled": self.enabled,
            "package": self.package,
            "action": self.current_action,
            "sequence": self.action_sequence,
            "time": self.action_time,
        }
