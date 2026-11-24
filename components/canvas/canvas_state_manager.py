"""Theater-scoped canvas coordinator.

State transitions live in sibling component classes.  This coordinator only
hydrates/persists the theater document and assembles the browser payload.
"""

from __future__ import annotations

from components.canvas.locking import synchronized

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from threading import RLock
from hashlib import sha256
from pathlib import Path
from typing import Optional, cast, TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter

from components.canvas import AudioState, AvatarState, ChatManager, ConnectionState, DoodleState, StoryState, ToolResponseState, UIState, VisualState
from components.theater_manager import Theater
from components.canvas.canvas_page import CanvasPage, DoodleAction, GameCanvasPage
from game_engine.game_runtime import RenderedNode
from game_engine.game_state import GameState
from components.notepad import Notepad
from components.story.input_gathering_cycle import story_input_cycle_duration
from components.story.output_display_cycle import story_output_cycle_duration
from utils.atomic_file import atomic_write_text
from components.canvas.canvas_assets import CanvasAssetCatalog
from components.canvas.canvas_observation import ObservationClock, observe_page
from components.canvas.game_stamps import stage_stamps
from game_engine.canvas_contract import COMMANDS, CanvasSnapshot, Page, Region

if TYPE_CHECKING:
    from services.text_beautifier import TextBeautifier

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
    avatar: Optional[AvatarState] = None
    story: Optional[StoryState] = None
    chat: Optional[ChatManager] = None
    game: GameState | None = None
    notepad: Notepad
    on_characters_updated: Callable[[list[dict[str, JsonValue]]], None] | None = None
    on_scenes_updated: Callable[[list[dict[str, JsonValue]]], None] | None = None
    on_animations_updated: Callable[[list[dict[str, JsonValue]]], None] | None = None

    def __init__(
        self, theater: Theater, *, text_beautifier: TextBeautifier | None = None,
        user_stamp_resolver: Callable[[DoodleAction], bytes | None] | None = None,
    ) -> None:
        """Omitted services leave text unstyled and restrict stamps to local/inline images."""
        self.text_beautifier = text_beautifier
        self.user_stamp_resolver = user_stamp_resolver
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
        self.doodles = DoodleState(
            self.persist, theater=self.theater, page_provider=self.visual.current_page, lock=self.canvas_lock,
            user_stamp_resolver=self.user_stamp_resolver,
        )
        self.ui = UIState(self.persist, self.notify_changed)
        self.tool_response = ToolResponseState(self.notify_changed)
        self.avatar = AvatarState(self.theater, self.notify_changed, lock=self.canvas_lock)
        story_planning_config = theater.config().get("story_planning", {})
        adventure_mode = bool(story_planning_config.get("adventure_mode", False))
        self.story = StoryState(
            self.persist,
            self.notify_changed,
            publish_audio_fn=self.connections.broadcast,
            text_beautifier=self.text_beautifier,
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

    @synchronized
    def doodles_for_page(self, page_id: str) -> DoodleState | None:
        """Route delayed drawing messages to their original history page."""
        for item in self.visual.shown_images_history:
            if item.id == page_id:
                return DoodleState(
                    self.persist, theater=self.theater, page_provider=lambda: item, lock=self.canvas_lock,
                    user_stamp_resolver=self.user_stamp_resolver,
                )
        return None

    def _ensure_initial_page(self) -> None:
        if self.visual.current_page() is None:
            legacy_doodles = self.doodles.serialize()
            if self.visual.ensure_initial_page():
                self.doodles.load(legacy_doodles)

    @synchronized
    def show_game_page(self) -> None:
        """Publish the theater's rendered game as an addressable canvas page."""
        program = self.game.program
        if program is None or program.definition.rendered_canvas is None:
            raise ValueError("This theater has no rendered game canvas.")
        game_hash = self.game.definition_hash
        existing = self.visual.game_page(game_hash)
        if existing is None:
            view_box = ""
            if program.initial_view:
                scene = RenderedNode.model_validate(program.initial_view[0])
                view_box = str(scene.props.get("viewBox") or "")
            existing = GameCanvasPage.create(game_hash, view_box)
        self._ensure_initial_page()
        surfaces = self.ui.interactive_surfaces
        with self.visual.stage_game_page(existing):
            self.ui.interactive_surfaces = {
                key: surface for key, surface in surfaces.items() if bool(surface.get("persistent", False))
            }
            try:
                self.persist()
            except BaseException:
                self.ui.interactive_surfaces = surfaces
                raise
        self.notify_changed("latest")

    def is_active_game_page(self, page_id: str, definition_hash: str) -> bool:
        page = self.visual.current_page()
        return page is not None and page.id == page_id and page.matches_game(definition_hash)

    def _on_visual_changed(self, changed: bool = True) -> None:
        if changed:
            self.ui.interactive_surfaces = {
                surface_id: surface for surface_id, surface in self.ui.interactive_surfaces.items()
                if bool(surface.get("persistent", False))
            }
        self.notify_changed("latest")

    def notify_changed(self, *domains: str) -> None:
        self.connections.notify(*domains)

    def broadcast_doodle(self, message: dict[str, JsonValue]) -> None:
        if self.connections is None or not self.connections.active_ws_connections:
            return
        loop = self.connections.state_ws_loop
        if loop is None or loop.is_closed():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        if loop is None or loop.is_closed():
            return
        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is loop:
            loop.create_task(self.connections.broadcast_doodle_message(message))
        else:
            asyncio.run_coroutine_threadsafe(self.connections.broadcast_doodle_message(message), loop)

    def notify_characters_updated(self) -> None:
        if self.theater is None:
            return
        characters = [dict(character) for character in self.theater.characters()]
        self.broadcast_doodle({"type": "theater_characters_updated", "characters": characters})
        if self.on_characters_updated is not None:
            self.on_characters_updated(characters)

    def notify_scenes_updated(self) -> None:
        if self.theater is None:
            return
        scenes = [dict(scene) for scene in self.theater.scenes()]
        self.broadcast_doodle({"type": "theater_scenes_updated", "scenes": scenes})
        if self.on_scenes_updated is not None:
            self.on_scenes_updated(scenes)

    def notify_animations_updated(self) -> None:
        if self.theater is None:
            return
        from services.theater_assets import animations as list_animations
        anim_list = [animation.model_dump(mode="json") for animation in list_animations(self.theater)]
        self.broadcast_doodle({"type": "theater_animations_updated", "animations": anim_list})
        if self.on_animations_updated is not None:
            self.on_animations_updated(anim_list)

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
        native = self.visual.current_page()
        if native is None:
            observed = CanvasSnapshot.empty(self.canvas_clock.revision)
        else:
            if "canvas_page" not in native:
                source_path = native.path
                catalog = CanvasAssetCatalog(self.theater.directory(), self.theater.get_url_for_path)
                native.initialize_geometry(catalog)
                if self.visual.shown_image_path == source_path:
                    self.visual.shown_image_path = native.path
            page = Page.model_validate(native["canvas_page"])
            regions = TypeAdapter(dict[str, Region]).validate_python(native.get("canvas_regions", {}), strict=True)
            doodles = self.doodles_for_page(page.id)
            assert doodles is not None
            doodles.canonicalize_canvas_objects(page, native.path)
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
        with self.canvas_lock, ExitStack() as staging:
            if not effects:
                yield snapshot
                return
            if self.visual.pinned:
                raise ValueError("Unpin the canvas before applying game output.")
            assert self.theater is not None and self.game is not None
            catalog = CanvasAssetCatalog(self.theater.directory(), self.theater.get_url_for_path)
            native = self.visual.current_page()
            if (native.id if native is not None else None) != (
                snapshot.page.id if snapshot.page is not None else None
            ):
                raise ValueError("The shared canvas page changed.")
            commands = COMMANDS.validate_python(effects, strict=True)
            scene = next((command for command in commands if command.op == "show_scene"), None)
            staged = snapshot
            stamp_batches: dict[str, list[JsonValue]] = {}
            for command in commands:
                if command.op == "show_scene":
                    continue
                if command.op not in {"put_stamp", "remove_stamp"}:
                    raise ValueError(f"The panel canvas host does not support {command.op} yet.")
                target = command.target.model_dump()
                if "page_id" in target:
                    stamp_batches.setdefault(target["page_id"], []).append(command.model_dump(mode="json"))
            if snapshot.page is not None and snapshot.page.id in stamp_batches:
                previous = self.doodles.doodles
                actions, staged = stage_stamps(
                    stamp_batches[snapshot.page.id], snapshot, previous, catalog, self.game.definition_hash,
                )
                assert native is not None
                native.annotations["doodles"] = actions

                @contextmanager
                def restore_doodles(page: CanvasPage, original: list[DoodleAction]) -> Iterator[None]:
                    try:
                        yield
                    except BaseException:
                        page.annotations["doodles"] = original
                        raise

                staging.enter_context(restore_doodles(native, previous))
            if scene is not None:
                asset = catalog.scene(scene.scene_id)
                new_native = staging.enter_context(self.visual.stage_game_scene(asset.path, asset.url))
                page = Page(
                    id=str(new_native["page_id"]), scene_id=asset.scene_id, asset_id=asset.asset_id,
                    media_kind="image", animation=None, width=asset.width, height=asset.height,
                )
                new_native["canvas_page"] = page.model_dump(mode="json")
                new_native["canvas_regions"] = {key: region.model_dump(mode="json") for key, region in asset.regions.items()}
                staged = observe_page(page, [], revision=snapshot.revision, regions=asset.regions)
                scene_stamps: list[JsonValue] = [
                    {**command.model_dump(mode="json"), "target": {"page_id": page.id}}
                    for command in commands
                    if command.op in {"put_stamp", "remove_stamp"} and command.target.model_dump().get("page_ref") == scene.page_ref
                ]
                if scene_stamps:
                    actions, staged = stage_stamps(scene_stamps, staged, [], catalog, self.game.definition_hash)
                    new_native.annotations["doodles"] = actions
                previous_surfaces = self.ui.interactive_surfaces
                self.ui.interactive_surfaces = {
                    surface_id: surface for surface_id, surface in previous_surfaces.items()
                    if bool(surface.get("persistent", False))
                }
            previous_clock = self.canvas_clock
            projection = staged.model_dump(mode="json")
            projection.pop("revision")
            fingerprint = sha256(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            self.canvas_clock = ObservationClock(
                revision=previous_clock.revision + (fingerprint != previous_clock.fingerprint), fingerprint=fingerprint,
            )
            try:
                staged = CanvasSnapshot.model_validate({**staged.model_dump(mode="json"), "revision": self.canvas_clock.revision})
                yield staged
            except BaseException:
                self.canvas_clock = previous_clock
                if scene is not None:
                    self.ui.interactive_surfaces = previous_surfaces
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
    def get_latest_state(self, *, username: str = "") -> dict[str, JsonValue]:
        self._ensure_initial_page()
        game = self.game.payload(username=username)
        visual = self.visual.payload()
        sticky_notes = self.notepad.get_present_sticky_notes()
        return {**visual, "music": self.audio.payload(), "music_pinned": self.audio.pinned,
                "music_orator_cursor": self.audio.payload().get("orator_cursor"),
                "doodles_enabled": self.doodles.enabled,
                "doodle_page_id": self.doodles.page_id,
                "viewer_collab_enabled": self.ui.viewer_collab_enabled,
                "tool_activity": self.tool_response.activity_payload(),
                "agent_thought": self.tool_response.agent_thought_payload(),
                "avatar": self.avatar.payload(),
                **self.story.payload(), "sticky_notes": sticky_notes,
                "interactive_surfaces": list(self.ui.interactive_surfaces.values()),
                "game_state": game}
