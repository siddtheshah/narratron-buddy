"""Composition root and public facade for story behavior."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import RLock
from uuid import uuid4
from datetime import datetime, timezone
import logging
import time
from typing import Callable, Optional

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from components.canvas.story_state import CharacterState, PlayerCharacterState, StoryState
from components.canvas.game_runtime import GameAction
from tools.story.story_snapshot import (
    ImageRecord,
    StoryStateDiff,
    StoryStateSnapshot,
    create_snapshot as create_story_snapshot,
    diff as compute_story_diff,
)
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import SpeechProvider, TextResponseProvider
from tools.base_tool import BaseTools, with_cycle_cooldown
from tools.tool_metadata import terminal
from components.reference_manager import ReferenceManager
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot, StaleNoteSnapshot
from tools.story.story_planning_module import StoryPlanningModule
from tools.story.story_response_module import GameEntryNarrationRejected, StoryResponseModule
from tools.story.story_handoffs import ProposedFactChange
from tools.story.story_recorder_module import StoryRecorderModule
from tools.story.story_recording_journal import RecordingEvent, StoryRecordingJournal
from tools.story.story_session import StorySession
from tools.story.story_turn_journal import StoryTurn, StoryTurnJournal


logger = logging.getLogger(__name__)
STORY_LOG_CONTEXT_LINES = 200


class StoryResponseOutput(BaseModel):
    narration: str = ""
    dialogue: list[dict[str, JsonValue]] = Field(default_factory=list)
    die_rolls: list[dict[str, JsonValue]] = Field(default_factory=list)
    scene_reference: Optional[str] = None
    proposed_factual_changes: list[ProposedFactChange] = Field(default_factory=list)
    planning_request: str | None = None


class StoryLogEntry(BaseModel):
    type: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: Optional[str] = None
    output: Optional[StoryResponseOutput] = None


class StoryTool(BaseTools):
    """Public story tool that owns and wires the story subsystem.

    ``StoryResponseModule`` and ``StoryPlanningModule`` deliberately receive
    their dependencies instead of constructing one another. This class is the
    single composition root for the character provider, shared lore library,
    reference manager, and ADK session context.
    """

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
        reference_manager: ReferenceManager,
        speech_provider: SpeechProvider | None = None,
        lore_library: Optional[LoreLibrary] = None,
    ) -> None:
        if text_response_provider is None:
            raise ValueError("text_response_provider is required.")
        if reference_manager is None:
            raise ValueError("reference_manager is required.")

        self.theater = theater
        super().__init__(theater, canvas_manager)
        self.text_response_provider = text_response_provider

        raw_config = theater.config() or {}
        self.config = raw_config.get("story_planning", raw_config)
        self.cooldown_duration = float(self.config.get("cooldown_duration", self.config.get("action_cooldown_base_seconds", 15.0)))
        self.action_cooldown_base_seconds = self.cooldown_duration
        self.notepad = canvas_manager.notepad or Notepad(
            theater, canvas_manager=canvas_manager,
        )
        canvas_manager.notepad = self.notepad
        self._story_log: list[dict[str, JsonValue]] = []
        self._on_scene_reaction: Callable[[dict[str, JsonValue]], None] | None = None
        self._load_story_log()
        self.lore_library = lore_library if lore_library is not None else LoreLibrary(theater=theater)
        self.reference_manager = reference_manager
        if speech_provider is not None and self.reference_manager.speech_provider is None:
            self.reference_manager.speech_provider = speech_provider

        journal_path = theater.output_dir() / "story.sqlite"
        self.recording_journal = StoryRecordingJournal(journal_path)
        self.turn_journal = StoryTurnJournal(journal_path)
        self.story_session = StorySession(
            theater.output_dir() / "story_adk.sqlite",
            str(self.config.get("session_id") or f"story_{theater.theater_id}"),
        )
        self.response_module = StoryResponseModule(
            theater=theater, canvas_manager=canvas_manager, lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            session_service=self.story_session.session_service,
            session_id=self.story_session.session_id, story_session=self.story_session,
        )
        self.planning_module = StoryPlanningModule(
            theater=theater, canvas_manager=canvas_manager, lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            session_service=self.story_session.session_service,
            session_id=self.story_session.session_id, story_session=self.story_session,
            story_log_context_fn=self.format_recent_story_log, game_state=canvas_manager.game,
        )
        self.recorder_module = StoryRecorderModule(
            self.notepad, self.recording_journal,
            lore_library=self.lore_library, game_state=canvas_manager.game,
            story_session=self.story_session, reference_manager=self.reference_manager,
        )
        assert self.recorder_module.agent is not None
        self.story_session.bind([
            self.response_module._responder_agent,
            self.recorder_module.agent,
            self.planning_module._deep_planner_agent,
        ])
        self._state_lock = RLock()
        self._work_lock = RLock()
        self._foreground_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-turn")
        self._planner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-plan")
        self._foreground_work: dict[str, Future[dict[str, JsonValue]]] = {}
        self._planning_work: dict[str, Future[None]] = {}
        self._closed = False
        self.notepad.on_change = self.response_module.save_to_session_state
        self.response_module.on_failure = lambda: self.record_tool_failure("process_user_action")
        if canvas_manager.game is not None:
            canvas_manager.game.before_player_action = self._wait_for_foreground
            canvas_manager.game.on_player_action = self._handle_player_game_action
            for action, receipt in canvas_manager.game.pending_story_actions():
                self._handle_player_game_action(action, receipt)
        self.sync_character_bindings()
        self.retry_pending_turns()

    def sync_character_voice_tags(self) -> None:
        self.sync_character_bindings()

    def sync_character_bindings(self) -> None:
        story = self.canvas_manager.story
        if story is None:
            return
        characters = self.reference_manager.export_characters()
        story.update_character_voice_tags({char.name: char.voice_tags or [char.gender] for char in characters})
        for char in characters:
            if char.voice_id:
                story.assign_character_voice(char.name, str(char.voice_id))

    def _load_story_log(self) -> None:
        if not self.theater.theater_id:
            return
        for line in self.theater.read_output_file_lines("story_log.jsonl")[-STORY_LOG_CONTEXT_LINES:]:
            try:
                self._story_log.append(StoryLogEntry.model_validate_json(line).model_dump(mode="json"))
            except ValueError:
                logger.warning("[StoryTool] Ignoring invalid story log line.")

    def append_story_log_entry(self, entry: dict[str, JsonValue]) -> None:
        parsed = StoryLogEntry.model_validate(entry)
        self._story_log.append(parsed.model_dump(mode="json"))
        self._story_log = self._story_log[-STORY_LOG_CONTEXT_LINES:]
        self.theater.append_output_file("story_log.jsonl", parsed.model_dump_json() + "\n")

    def format_recent_story_log(self) -> str:
        lines: list[str] = []
        for raw in self._story_log:
            entry = StoryLogEntry.model_validate(raw)
            if entry.type == "user_action" and entry.action:
                lines.append(f"Player: {entry.action}")
            elif entry.output is not None:
                if entry.output.narration:
                    lines.append(f"Narration: {entry.output.narration}")
                for dialogue in entry.output.dialogue:
                    if dialogue.get("text"):
                        lines.append(f"Dialogue — {dialogue.get('speaker', '')}: {dialogue['text']}")
        return "\n".join(lines)

    def _schedule_turn(self, turn: StoryTurn, *, retry_earlier: bool = False) -> Future[dict[str, JsonValue]]:
        with self._work_lock:
            existing = self._foreground_work.get(turn.turn_id)
            if existing is not None and not existing.done():
                return existing
            future = self._foreground_executor.submit(
                self._run_turn, turn.turn_id, retry_earlier=retry_earlier,
            )
            self._foreground_work[turn.turn_id] = future
            return future

    def _recording_event(self, turn: StoryTurn) -> RecordingEvent:
        return RecordingEvent(
            event_id=f"{turn.turn_id}:recording", action_id=turn.turn_id,
            source_event_ids=(f"{turn.turn_id}:responder",) if turn.ingress == "communicated_action" else (),
            kind="committed_game_action" if turn.ingress == "game_action" else "completed_response",
            source_revision=int((turn.response or {}).get("notepad_revision_used", turn.source_revision)),
            payload=turn.game_payload if turn.ingress == "game_action" else (turn.response or {}),
        )

    def _set_user_action_processing(self, active: bool) -> None:
        """Publish foreground story-turn activity to the canvas."""
        tool_response = self.canvas_manager.tool_response
        if tool_response is not None:
            tool_response.set_activity("user_action", active=active)

    def _run_turn(
        self, turn_id: str, *, retry_earlier: bool = False, release_cycle: bool = True,
    ) -> dict[str, JsonValue]:
        turn = self.turn_journal.get(turn_id)
        assert turn is not None
        self._set_user_action_processing(True)
        try:
            with self._state_lock:
                if retry_earlier:
                    for previous in self.turn_journal.turns():
                        if previous.position < turn.position and (
                            not previous.foreground_ready
                            or (previous.response is not None and not previous.delivered)
                        ):
                            self._run_turn(previous.turn_id, release_cycle=False)
                if any(previous.position < turn.position and not previous.foreground_ready for previous in self.turn_journal.turns()):
                    raise RuntimeError("An earlier story turn still requires factual recording or narration.")
                if turn.ingress == "communicated_action":
                    if turn.response is None:
                        result = self.response_module._resolve_user_action(
                            turn.action, nudge=turn.nudge, publish=False, stage_id=turn.turn_id,
                        )
                        if result.get("error"):
                            raise RuntimeError(str(result["error"]))
                        turn.response = TypeAdapter(dict[str, JsonValue]).validate_python(result)
                        self.turn_journal.save(turn)
                    if not turn.recorder_completed:
                        self.recorder_module.record(self._recording_event(turn))
                        turn.recorder_completed = True
                        self.turn_journal.save(turn)
                else:
                    if not turn.recorder_completed:
                        event = self._recording_event(turn)
                        outcome = self.recorder_module.control_game(event)
                        decision = self.recording_journal.game_control_decision(event.event_id)
                        assert decision is not None
                        turn.recorder_completed = True
                        turn.narration_reason = decision.reason
                        turn.responder_skipped = outcome.narration_request is None
                        turn.narrative_brief = outcome.narration_request.brief if outcome.narration_request is not None else None
                        self.turn_journal.save(turn)
                    if turn.response is None and not turn.responder_skipped:
                        result = self.response_module._resolve_user_action(
                            "Narrate the recorder-established outcome.",
                            nudge=turn.narrative_brief or "", publish=False, game_entry=True,
                            stage_id=turn.turn_id,
                        )
                        if result.get("error"):
                            raise RuntimeError(str(result["error"]))
                        turn.response = TypeAdapter(dict[str, JsonValue]).validate_python(result)
                        self.turn_journal.save(turn)
                if turn.response is not None and not turn.delivered:
                    self._handle_scene_reaction(turn.response)
                    turn.delivered = True
                    self.turn_journal.save(turn)
                self.sync_character_bindings()
                self._schedule_planning(turn)
            return turn.response or {"status": "completed", "narration_skipped": True, "reason": turn.narration_reason}
        except GameEntryNarrationRejected:
            logger.warning(
                "[StoryTool] Turn %s awaits corrected game-entry narration; retry remains pending",
                turn_id,
            )
            return {"status": "pending", "story_turn_id": turn_id}
        except Exception as exc:
            logger.exception("[StoryTool] Turn %s remains pending", turn_id)
            self.record_tool_failure("process_user_action")
            result: dict[str, JsonValue] = {"error": str(exc), "story_turn_id": turn_id}
            if self._on_scene_reaction is not None:
                self._on_scene_reaction(result)
            return result
        finally:
            self._set_user_action_processing(False)
            if release_cycle and turn.ingress == "communicated_action":
                self._on_cycle_tool_completed("process_user_action")

    def _schedule_planning(self, turn: StoryTurn) -> None:
        if turn.planner_completed:
            return
        with self._work_lock:
            existing = self._planning_work.get(turn.turn_id)
            if existing is None or existing.done():
                self._planning_work[turn.turn_id] = self._planner_executor.submit(self._plan_turn, turn.turn_id)

    def _plan_turn(self, turn_id: str) -> None:
        try:
            for attempt in range(3):
                with self._state_lock:
                    turn = self.turn_journal.get(turn_id)
                    assert turn is not None
                    if turn.planner_completed:
                        return
                    if not turn.foreground_ready:
                        raise RuntimeError("Planning requires the foreground stages' committed handoffs.")
                    source = self.notepad.snapshot()
                    handoff = turn.model_dump(mode="json")
                    handoff["source_event_ids"] = [f"{turn.turn_id}:recording"]
                    handoff["audience"] = ["planner"]
                    game = self.canvas_manager.game
                    game_snapshot = game.story_snapshot("narratron") if game is not None else None
                    handoff["game_state"] = game_snapshot
                updates = self.planning_module.plan_recorded_turn(handoff, source)

                def persist(snapshot: NoteSnapshot) -> None:
                    self.turn_journal.commit_planning(turn, snapshot)

                try:
                    with self._state_lock:
                        current_game = game.story_snapshot("narratron") if game is not None else None
                        if current_game != game_snapshot:
                            raise StaleNoteSnapshot("Game state changed during planning; recheck the guidance.")
                        self.notepad.publish_role_updates("planner", updates, source=source, persist=persist)
                    return
                except StaleNoteSnapshot:
                    if attempt == 2:
                        raise
        except Exception:
            logger.exception("[StoryTool] Planner stage %s remains pending", turn_id)

    def _handle_scene_reaction(self, result: dict[str, JsonValue]) -> None:
        """Delivery cannot open a turn or schedule another role."""
        self.response_module.deliver_response(result)
        self.append_story_log_entry({"type": "story_response", "output": result})
        if self._on_scene_reaction is not None:
            self._on_scene_reaction(result)

    def _handle_player_game_action(self, action: GameAction, new_state: dict[str, JsonValue]) -> None:
        self.retry_pending_turns()
        event_id = str(new_state.get("story_event_id") or f"game:{action.revision}")
        turn = self.turn_journal.accept(StoryTurn(
            turn_id=event_id, ingress="game_action", action="Game panel action",
            source_revision=self.notepad.revision,
            game_payload={"action": action.model_dump(mode="json"), "receipt": new_state, "actor": "player"},
        ))
        self._schedule_turn(turn, retry_earlier=True)
        if self.canvas_manager.game is not None and new_state.get("story_event_id"):
            self.canvas_manager.game.acknowledge_story_action(event_id)

    def retry_pending_turns(self) -> None:
        """Restart unfinished stages with the original turn and committed effects."""
        for turn in self.turn_journal.turns():
            if not turn.foreground_ready or (turn.response is not None and not turn.delivered):
                self._schedule_turn(turn)
            elif not turn.planner_completed:
                self._schedule_planning(turn)

    def _wait_for_foreground(self) -> None:
        """Game commits cannot race preceding causal factual stages."""
        self.retry_pending_turns()
        with self._work_lock:
            foreground = list(self._foreground_work.items())
        for turn_id, future in foreground:
            result = future.result(timeout=180)
            turn = self.turn_journal.get(turn_id)
            if result.get("error") and (turn is None or not turn.foreground_ready):
                raise RuntimeError("A preceding story turn requires recovery before another game action.")
        if any(not turn.foreground_ready for turn in self.turn_journal.turns()):
            raise RuntimeError("A preceding story turn requires recovery before another game action.")

    def resolve_action(self, user_action: str, nudge: str = "", *, drain: bool = False) -> dict[str, JsonValue]:
        """Harness entry using production scheduling with an optional deterministic drain."""
        turn = self.turn_journal.accept(StoryTurn(
            turn_id=uuid4().hex, ingress="communicated_action", action=user_action, nudge=nudge,
        ))
        result = self._schedule_turn(turn).result()
        if drain:
            self.wait_for_pending_events(timeout=300)
        return result

    def wait_for_pending_events(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._work_lock:
                foreground = [f for f in self._foreground_work.values() if not f.done()]
            if foreground:
                _, pending = wait(foreground, timeout=max(0.0, deadline - time.monotonic()))
                if pending:
                    return False
                continue
            with self._work_lock:
                planning = [f for f in self._planning_work.values() if not f.done()]
            if planning:
                _, pending = wait(planning, timeout=max(0.0, deadline - time.monotonic()))
                if pending:
                    return False
                continue
            if self.is_in_flight("process_user_action") and time.monotonic() < deadline:
                time.sleep(0.01)
                continue
            return not self.is_in_flight("process_user_action") and all(
                turn.completed for turn in self.turn_journal.turns()
            )

    def wait_for_deep_planning(self, timeout: float = 5.0, target_turn_id: int | None = None) -> bool:
        return self.wait_for_pending_events(timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel_pending_cycle_call("process_user_action")
        for timer in self._cooldown_timers.values():
            timer.cancel()
        self._foreground_executor.shutdown(wait=True)
        self._planner_executor.shutdown(wait=True)
        if self.canvas_manager.game is not None:
            self.canvas_manager.game.on_player_action = None
            self.canvas_manager.game.before_player_action = None
        self.story_session.close()
        self.turn_journal.close()
        self.recording_journal.close()

    def __getattr__(self, name: str) -> Callable:
        """Expose the response module's existing public tool surface."""
        response_module = self.__dict__.get("response_module")
        if response_module is not None:
            try:
                return getattr(response_module, name)
            except AttributeError:
                pass
        planning_module = self.__dict__.get("planning_module")
        if planning_module is not None:
            try:
                return getattr(planning_module, name)
            except AttributeError:
                pass
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


    @terminal
    @with_cycle_cooldown(action_desc="resolving story update", tool_name="process_user_action", hold_until_released=True)
    def process_user_action(self, user_action: str, nudge: str = "") -> dict[str, JsonValue] | str:
        action = user_action.strip()
        if not action:
            return {"error": "User action cannot be empty."}
        if action == "Narrate the recorder-established outcome.":
            return {"error": "Game-entry narration is already resolved; do not submit it to process_user_action."}
        if len(action) > 2000 or len(nudge) > 1000:
            return {"error": "Story action or nudge exceeds its size limit."}
        if not self.response_module.adventure_mode:
            return {"error": "Adventure Mode is not enabled for this theater."}
        self.retry_pending_turns()
        for pending in self.turn_journal.turns():
            if not pending.foreground_ready and pending.ingress == "communicated_action" and pending.action == action:
                return {"status": "processing", "story_turn_id": pending.turn_id, "message": "Resuming the pending story turn."}
        if not self.response_module.consume_user_input():
            return {"error": "No input from the orator was detected."}
        turn = self.turn_journal.accept(StoryTurn(
            turn_id=uuid4().hex, ingress="communicated_action", action=action, nudge=nudge,
        ))
        self.append_story_log_entry({"type": "user_action", "action": action})
        self._schedule_turn(turn, retry_earlier=True)
        return {"status": "processing", "story_turn_id": turn.turn_id, "message": "Story turn queued."}

    def get_tools(self) -> list[Callable]:
        return [self.process_user_action]

    @property
    def is_action_in_flight(self) -> bool:
        return self.is_in_flight("process_user_action")

    @property
    def on_scene_reaction(self) -> Optional[Callable[[dict[str, JsonValue]], None]]:
        return self.__dict__.get("_on_scene_reaction")

    @on_scene_reaction.setter
    def on_scene_reaction(self, callback: Optional[Callable[[dict[str, JsonValue]], None]]) -> None:
        self._on_scene_reaction = callback

    @property
    def on_story_response_completed(self) -> Optional[Callable[[], None]]:
        return self.response_module.on_story_response_completed

    @on_story_response_completed.setter
    def on_story_response_completed(self, callback: Optional[Callable[[], None]]) -> None:
        self.response_module.on_story_response_completed = callback

    @property
    def on_cooldown_expired(self) -> Optional[Callable[[str], None]]:
        return self.__dict__.get("_on_cooldown_expired")

    @on_cooldown_expired.setter
    def on_cooldown_expired(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_cooldown_expired = callback

    @property
    def on_after_tool_call(self) -> Optional[Callable[[str, dict[str, JsonValue]], None]]:
        return self.__dict__.get("_on_after_tool_call")

    @on_after_tool_call.setter
    def on_after_tool_call(
        self, callback: Optional[Callable[[str, dict[str, JsonValue]], None]]
    ) -> None:
        self._on_after_tool_call = callback

    @property
    def max_sticky_notes(self) -> int:
        return self.notepad.max_sticky_notes

    @max_sticky_notes.setter
    def max_sticky_notes(self, value: int) -> None:
        self.notepad.max_sticky_notes = max(1, int(value))

    @property
    def max_stickies(self) -> int:
        return self.notepad.max_stickies

    @terminal
    def create_or_update_scene(self, name: str, description: str = "") -> str:
        """Create or update a scene reference visual by name and concept description."""
        return self.response_module.create_or_update_scene(name=name, description=description)

    @terminal
    def update_sticky_note(self, topic: str, info: str) -> str:
        """Insert or replace one sticky note in the current scene."""
        if not self.response_module.adventure_mode:
            return self.notepad.update_sticky_note(topic, info)
        return "Error: Adventure sticky writes belong to the recorder and planner stages."

    def get_present_sticky_notes(self) -> list[dict[str, str]]:
        return self.notepad.get_present_sticky_notes()

    def get_present_elements(self) -> list[dict[str, str]]:
        return self.notepad.get_present_elements()

    def get_present_structured_sticky_notes(self) -> dict[str, JsonValue]:
        return self.notepad.get_present_structured_sticky_notes()

    def get_required_sticky_notes(self) -> list[str]:
        return self.notepad.get_required_sticky_notes()

    def create_snapshot(
        self,
        *,
        stickies: Optional[dict[str, str]] = None,
        player_character: Optional[PlayerCharacterState] = None,
        characters: Optional[dict[str, CharacterState]] = None,
        known_image_names: Optional[set[str]] = None,
        notepad_revision: Optional[int] = None,
    ) -> StoryStateSnapshot:
        """Create a point-in-time snapshot built from StoryState and Notepad together."""
        story = (
            self.canvas_manager.story
            if self.canvas_manager is not None and self.canvas_manager.story is not None
            else StoryState()
        )
        return create_story_snapshot(
            story,
            self.notepad,
            stickies=stickies,
            player_character=player_character,
            characters=characters,
            known_image_names=known_image_names,
            notepad_revision=notepad_revision,
        )

    def diff(
        self,
        previous: StoryStateSnapshot,
        *,
        current_stickies: Optional[dict[str, str]] = None,
        current_pc: Optional[PlayerCharacterState] = None,
        current_chars: Optional[dict[str, CharacterState]] = None,
        recent_images: Optional[list[ImageRecord]] = None,
        current_notepad_revision: Optional[int] = None,
        current_through_turn: int = 0,
        current_snapshot: Optional[StoryStateSnapshot] = None,
    ) -> StoryStateDiff:
        """Compute diffs between a previous snapshot and current story state + notepad."""
        story = self.canvas_manager.story if self.canvas_manager is not None else None
        return compute_story_diff(
            previous=previous,
            story_state=story,
            notepad=self.notepad,
            current_stickies=current_stickies,
            current_pc=current_pc,
            current_chars=current_chars,
            recent_images=recent_images,
            current_notepad_revision=current_notepad_revision,
            current_through_turn=current_through_turn,
            current_snapshot=current_snapshot,
        )



__all__ = [
    "StoryTool",
    "StoryLogEntry",
    "StoryResponseOutput",
]
