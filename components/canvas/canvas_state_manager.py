"""Theater-scoped canvas coordinator.

State transitions live in sibling component classes.  This coordinator only
hydrates/persists the theater document and assembles the browser payload.
"""

from __future__ import annotations

from components.canvas.locking import synchronized

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from hashlib import sha256
from pathlib import Path
from typing import Optional, cast

from pydantic import JsonValue, TypeAdapter

from components.canvas import AudioState, ChatManager, ConnectionState, DoodleState, StoryState, ToolResponseState, UIState, VisualState
from components.theater_manager import Theater
from components.canvas.doodle_state import DoodlePage
from game_engine.game_state import GameState
from components.notepad import Notepad
from components.story.input_gathering_cycle import story_input_cycle_duration
from components.story.output_display_cycle import story_output_cycle_duration
from utils.atomic_file import atomic_write_text
from components.canvas.canvas_assets import CanvasAssetCatalog
from components.canvas.canvas_observation import ObservationClock, observe_page
from components.canvas.game_stamps import stage_stamps
from game_engine.canvas_contract import CanvasSnapshot, Page, Region

logger = logging.getLogger("components.canvas_state")
MAX_AGENT_THOUGHT_LENGTH = 360


class CanvasStateManager:
    theater: Optional[Theater] = None
    theater_id: Optional[str] = None
    theater_manager: Optional[object] = None
    connections: Optional[ConnectionState] = None
    visual: Optional[VisualState] = None
    audio: Optional[AudioState] = None
    doodles: Optional[DoodleState] = None
    ui: Optional[UIState] = None
    tool_response: Optional[ToolResponseState] = None
    story: Optional[StoryState] = None
    chat: Optional[ChatManager] = None
    game: GameState | None = None
    notepad: Optional[Notepad] = None

    def __init__(self, theater: Theater) -> None:
        self.canvas_lock = RLock()
        self.theater = theater
        self.theater_id = theater.theater_id
        self.theater_manager = getattr(theater, "manager", None)
        self.connections = ConnectionState()
        self.canvas_clock = ObservationClock()
        self.canvas_clock_error = ""
        self._invalid_canvas_clock: JsonValue = None
        self._storage_notification_pending = False
        self._initialize_state()
        self.load_state_from_disk()
        self.visual.initialize_starting_image()

    def _initialize_state(self) -> None:
        theater = self.theater
        self.visual = VisualState(
            self.theater,
            notify_changed_fn=self.notify_changed,
            on_visual_changed_fn=self._on_visual_changed,
            lock=self.canvas_lock,
        )
        self.audio = AudioState(self.notify_changed)
        self.doodles = DoodleState(self.persist, theater=self.theater, page_provider=self._doodle_page, lock=self.canvas_lock)
        self.ui = UIState(self.persist, self.notify_changed)
        self.tool_response = ToolResponseState(self.notify_changed)
        story_planning_config = theater.config().get("story_planning", {})
        adventure_mode = bool(story_planning_config.get("adventure_mode", False))
        self.story = StoryState(
            self.persist,
            self.notify_changed,
            publish_audio_fn=self.connections.broadcast,
            adventure_mode=adventure_mode,
            cycle_length=story_input_cycle_duration(story_planning_config),
            output_cycle_duration=story_output_cycle_duration(story_planning_config),
        )
        self.chat = ChatManager(self.theater)
        self.game = GameState(self.theater.directory(), self.persist, self.notify_changed, canvas_host=self)
        enforce_structured = bool(story_planning_config.get("enforce_structured", True)) if adventure_mode else False
        self.notepad = Notepad(
            self.theater,
            canvas_manager=self,
            enforce_structured=enforce_structured,
        )

    def reset(self) -> None:
        """Clear session state in place so connected clients cannot retain history."""
        self.story.output_cycle.close()
        self._initialize_state()
        self.visual.initialize_starting_image()
        self.visual.shown_images_history.clear()
        self.notify_changed()

    def reload_package(self) -> None:
        """Refresh editor-changed configuration while retaining canvas history and sockets."""
        self.persist()
        self.story.output_cycle.close()
        self._initialize_state()
        self.load_state_from_disk()
        self.visual.initialize_starting_image()
        self.notify_changed()

    def _doodle_page(self) -> DoodlePage | None:
        history = self.visual.shown_images_history
        index = self.visual.orator_cursor
        if not history:
            return None
        return cast(DoodlePage, history[-1 if index is None else index])

    def doodles_for_page(self, page_id: str) -> DoodleState | None:
        """Route delayed drawing messages to their original history page."""
        for item in self.visual.shown_images_history:
            if item.get("page_id") == page_id:
                page = cast(DoodlePage, item)
                return DoodleState(self.persist, theater=self.theater, page_provider=lambda: page, lock=self.canvas_lock)
        return None

    def _on_visual_changed(self, changed: bool = True) -> None:
        if changed:
            self.ui.interactive_surfaces = {
                surface_id: surface for surface_id, surface in self.ui.interactive_surfaces.items()
                if bool(surface.get("persistent", False))
            }
        self.notify_changed("latest")

    def notify_changed(self, *domains: str) -> None:
        self.connections.notify(*domains)

    @synchronized
    def persist(self) -> None:
        self.save_local_theater_data(self.theater.directory())

    def load_state_from_disk(self) -> None:
        directory = self.theater.directory()
        source = directory / "theater.json"
        if not source.exists():
            source = directory / "theater_state.json"
        if not source.exists():
            return
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            state = data.get("canvas_state", data)
            self.visual.load(state)
            try:
                self.canvas_clock = ObservationClock.model_validate(state.get("canvas_api_state", {}))
                self.canvas_clock_error = ""
                self._invalid_canvas_clock = None
            except ValueError as exc:
                self.canvas_clock_error = "Saved canvas observation revision is invalid."
                self._invalid_canvas_clock = cast(JsonValue, state.get("canvas_api_state"))
                logger.warning("Failed to load canvas observation revision: %s", exc)
            self.audio.load(state)
            self.doodles.load(state)
            self.ui.load(state)
            self.story.load(state)
            self.game.load(state.get("game_state"))
            if self.notepad is not None:
                notepad_data = state.get("notepad")
                if isinstance(notepad_data, dict):
                    self.notepad.import_state(notepad_data)
                else:
                    sp_state = self.story.get_story_planning_state()
                    if sp_state:
                        self.notepad.import_state(sp_state)
            self.chat.messages = list(state.get("chat_messages", []))
            self.chat.load_suggestions(state.get("suggestions", []))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to load canvas state for %s: %s", self.theater_id, exc)

    @synchronized
    def serialized_state(self) -> dict[str, object]:
        audio_serialized = {k: v for k, v in self.audio.serialize().items() if k != "orator_cursor"}
        serialized = {
            **self.visual.serialize(), **audio_serialized, **self.doodles.serialize(),
            **self.ui.serialize(), **self.story.serialize(), **self.game.serialize(),
            "suggestions": self.chat.export_suggestions(), "chat_messages": self.chat.get_messages(),
            "canvas_api_state": self._invalid_canvas_clock if self.canvas_clock_error else self.canvas_clock.model_dump(mode="json"),
        }
        if self.notepad is not None:
            serialized["notepad"] = self.notepad.export_state()
        return serialized

    @synchronized
    def canvas_observation(self) -> CanvasSnapshot:
        """Observe the actual shared page, migrating legacy geometry once.

        This is the host adapter, not an evaluator binding supplied by a caller.
        Its revision tracks the exposed projection instead of viewer focus or
        animation frame clocks. The future commit host shares this projection.
        """
        assert self.visual is not None and self.theater is not None
        if self.canvas_clock_error:
            raise ValueError(self.canvas_clock_error)
        history = self.visual.shown_images_history
        if not history:
            observed = CanvasSnapshot.empty(self.canvas_clock.revision)
        else:
            index = self.visual.orator_cursor
            native = cast(dict[str, JsonValue], history[-1 if index is None else index])
            if native.get("animation"):
                raise ValueError("Canvas animation observations require the animation catalog adapter.")
            if "canvas_page" not in native:
                source_path = str(native.get("path", ""))
                catalog = CanvasAssetCatalog(self.theater.directory(), self.theater.get_url_for_path)
                asset = catalog.observe_image(source_path)
                page = Page(
                    id=str(native["page_id"]), scene_id=asset.scene_id, asset_id=asset.asset_id,
                    media_kind="image", animation=None, width=asset.width, height=asset.height,
                )
                native["canvas_page"] = page.model_dump(mode="json")
                native["canvas_regions"] = {key: region.model_dump(mode="json") for key, region in asset.regions.items()}
                native["path"], native["url"] = asset.path, asset.url
                if self.visual.shown_image_path == source_path:
                    self.visual.shown_image_path = asset.path
            page = Page.model_validate(native["canvas_page"])
            regions = TypeAdapter(dict[str, Region]).validate_python(native.get("canvas_regions", {}), strict=True)
            doodles = self.doodles_for_page(page.id)
            assert doodles is not None
            doodles.canonicalize_canvas_objects(page, str(native["path"]))
            observed = observe_page(page, doodles.doodles, revision=self.canvas_clock.revision, regions=regions)
        projection = observed.model_dump(mode="json")
        projection.pop("revision")
        fingerprint = sha256(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if fingerprint != self.canvas_clock.fingerprint:
            previous_clock = self.canvas_clock
            self.canvas_clock = ObservationClock(revision=self.canvas_clock.revision + 1, fingerprint=fingerprint)
            try:
                self.persist()
            except Exception:
                self.canvas_clock = previous_clock
                raise
            observed = CanvasSnapshot.model_validate({**observed.model_dump(mode="json"), "revision": self.canvas_clock.revision})
        return observed

    @contextmanager
    def stage_canvas_effects(self, effects: list[JsonValue], snapshot: CanvasSnapshot) -> Iterator[CanvasSnapshot]:
        """Install output until the caller saves game and canvas in one document.

        The game, visual cycle, and native doodle writers share canvas_lock.
        Exceptions from evaluation or persistence restore the original page.
        """
        with self.canvas_lock:
            if not effects:
                yield snapshot
                return
            if self.visual.pinned:
                raise ValueError("Unpin the canvas before applying game output.")
            assert self.theater is not None and self.game is not None
            native = self._doodle_page()
            if native is None or snapshot.page is None or native["page_id"] != snapshot.page.id:
                raise ValueError("The shared canvas page changed.")
            catalog = CanvasAssetCatalog(self.theater.directory(), self.theater.get_url_for_path)
            actions, staged = stage_stamps(effects, snapshot, self.doodles.doodles, catalog, self.game.definition_hash)
            previous, previous_clock = self.doodles.doodles, self.canvas_clock
            projection = staged.model_dump(mode="json")
            projection.pop("revision")
            fingerprint = sha256(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            self.canvas_clock = ObservationClock(
                revision=previous_clock.revision + (fingerprint != previous_clock.fingerprint), fingerprint=fingerprint,
            )
            self.doodles.doodles = actions
            staged = CanvasSnapshot.model_validate({**staged.model_dump(mode="json"), "revision": self.canvas_clock.revision})
            try:
                yield staged
            except BaseException:
                self.doodles.doodles, self.canvas_clock = previous, previous_clock
                raise

    @synchronized
    def save_local_theater_data(self, theater_dir: Optional[Path] = None) -> tuple[dict[str, object], list[dict[str, object]]]:
        directory = theater_dir or self.theater.directory()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "theater.json"
        document: dict[str, object] = {}
        if target.exists():
            try:
                document = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        document.setdefault("theater_id", self.theater_id)
        document.setdefault("name", self.theater_id)
        document["canvas_state"] = self.serialized_state()
        atomic_write_text(target, json.dumps(document, indent=2))
        self._storage_notification_pending = True
        self.retry_storage_notification()
        return document, []

    def retry_storage_notification(self) -> bool:
        """A mirror failure after local commitment must not undo committed state."""
        if not self._storage_notification_pending:
            return True
        assert self.theater is not None
        try:
            self.theater.storage_changed()
        except Exception:
            logger.exception("Theater %s was saved; its storage notification will be retried", self.theater_id)
            return False
        self._storage_notification_pending = False
        return True

    @synchronized
    def get_latest_state(self) -> dict[str, object]:
        visual = self.visual.payload()
        sticky_notes = self.notepad.get_present_sticky_notes() if self.notepad is not None else []
        return {**visual, "music": self.audio.payload(), "music_pinned": self.audio.pinned,
                "music_orator_cursor": self.audio.payload().get("orator_cursor"),
                "doodles_enabled": self.doodles.enabled,
                "doodle_page_id": self.doodles.page_id,
                "viewer_collab_enabled": self.ui.viewer_collab_enabled,
                "tool_activity": self.tool_response.activity_payload(),
                "agent_thought": self.tool_response.agent_thought_payload(),
                **self.story.payload(), "sticky_notes": sticky_notes,
                "interactive_surfaces": list(self.ui.interactive_surfaces.values()),
                "game_state": self.game.payload()}
