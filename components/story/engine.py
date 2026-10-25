"""Theater-bound story processing, consumed exclusively through the canvas cycle."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Lock
from uuid import uuid4
from datetime import datetime, timezone
import logging
from typing import Callable, Optional

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from components.canvas.story_state import CharacterState, PlayerCharacterState, StoryState
from components.canvas.game_runtime import GameAction
from components.story.story_snapshot import (
    ImageRecord,
    StoryStateDiff,
    StoryStateSnapshot,
    create_snapshot as create_story_snapshot,
    diff as compute_story_diff,
)
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers import SpeechProvider, TextResponseProvider
from components.reference_manager import ReferenceManager
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot, StaleNoteSnapshot
from components.story.story_planning_module import StoryPlanningModule
from components.story.story_response_module import GameEntryNarrationRejected, StoryResponseModule
from components.story.story_handoffs import ProposedFactChange
from components.story.story_recorder_module import StoryRecorderModule
from components.story.story_recording_journal import RecordingEvent, StoryRecordingJournal
from components.story.story_session import StorySession
from components.story.story_turn_journal import StoryTurn, StoryTurnJournal
from components.story.cycle import StoryCycle, story_cycle_length


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


class StoryEngine:
    """Own and wire the story subsystem independently of live-agent tools.

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
        story = canvas_manager.story
        if story is None:
            raise ValueError("StoryEngine requires canvas story state.")
        if story.cycle.is_closed:
            story.cycle = StoryCycle()
        if story.cycle.is_bound:
            raise ValueError("Canvas story cycle already has an engine.")

        self.theater = theater
        self.canvas_manager = canvas_manager
        self._intake_lock = Lock()
        self.text_response_provider = text_response_provider

        raw_config = theater.config() or {}
        self.config = raw_config.get("story_planning", raw_config)
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
        self.cycle = story.cycle
        self.cycle.cycle_length = story_cycle_length(self.config)
        self._closed = False
        self.notepad.on_change = self.response_module.save_to_session_state
        self.sync_character_bindings()
        self.cycle.bind(self._process_cycle)
        if canvas_manager.game is not None:
            canvas_manager.game.on_player_action = self._handle_player_game_action
            for action, receipt in canvas_manager.game.pending_story_actions():
                self._handle_player_game_action(action, receipt)
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
        try:
            self.theater.append_output_file("story_log.jsonl", parsed.model_dump_json() + "\n")
        except OSError:
            logger.exception("Story log write failed; durable turn progress is retained")

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

    def _schedule_turn(self, turn: StoryTurn) -> Future[dict[str, JsonValue]]:
        return self.cycle.submit(turn.turn_id)

    def _recording_event(self, turn: StoryTurn) -> RecordingEvent:
        return RecordingEvent(
            event_id=f"{turn.turn_id}:recording", action_id=turn.turn_id,
            source_event_ids=(f"{turn.turn_id}:responder",) if turn.ingress == "communicated_action" else (),
            kind="committed_game_action" if turn.ingress == "game_action" else "completed_response",
            source_revision=int((turn.response or {}).get("notepad_revision_used", turn.source_revision)),
            payload=turn.game_payload if turn.ingress == "game_action" else (turn.response or {}),
        )

    def _set_user_action_processing(self, active: bool) -> None:
        try:
            if self.canvas_manager.tool_response is not None:
                self.canvas_manager.tool_response.set_activity("user_action", active=active)
        except Exception:
            logger.exception("Story activity notification failed")

    def _process_cycle(self, turn_ids: list[str]) -> dict[str, dict[str, JsonValue]]:
        """Recover earlier batches, then process one frozen batch of new input.

        No engine/cycle lock is held during model work, game access, or callbacks.
        Only the canvas cycle can invoke this consumer, with one worker.
        """
        accepted = self.turn_journal.turns()
        boundary = max(turn.position for turn in accepted if turn.turn_id in turn_ids)
        unbound: list[str] = []
        for turn in accepted:
            if turn.turn_id not in turn_ids or turn.cycle_id is not None:
                continue
            if turn.response is not None or turn.recorder_completed:
                # Saved turns from the pre-cycle scheduler already have
                # separate model outputs/receipts and must resume separately.
                self.turn_journal.bind_cycle([turn.turn_id])
            else:
                unbound.append(turn.turn_id)
        if unbound:
            self.turn_journal.bind_cycle(unbound)
        groups: dict[str, list[StoryTurn]] = {}
        accepted = self.turn_journal.turns()
        selected_cycles = {turn.cycle_id for turn in accepted if turn.position <= boundary and turn.cycle_id is not None}
        for turn in accepted:
            if turn.position <= boundary or turn.cycle_id in selected_cycles:
                groups.setdefault(turn.cycle_id or turn.turn_id, []).append(turn)
        results: dict[str, dict[str, JsonValue]] = {}
        blocked = False
        for turns in groups.values():
            if blocked:
                result: dict[str, JsonValue] = {"error": "An earlier story cycle requires recovery."}
            elif all(turn.completed and (turn.response is None or turn.delivered) for turn in turns):
                result = next((turn.response for turn in turns if turn.response is not None), {"status": "completed", "narration_skipped": True})
            else:
                result = self._run_batch(turns)
                refreshed = [self.turn_journal.get(turn.turn_id) for turn in turns]
                blocked = any(turn is None or not turn.foreground_ready or (turn.response is not None and not turn.delivered) for turn in refreshed)
            for turn in turns:
                results[turn.turn_id] = result
        return {turn_id: results[turn_id] for turn_id in turn_ids}

    def _run_batch(self, turns: list[StoryTurn]) -> dict[str, JsonValue]:
        self._set_user_action_processing(True)
        try:
            # Pending windows must not leak future player actions into the
            # current responder/planner's recent-story context.
            for turn in turns:
                if turn.ingress == "communicated_action" and not turn.action_logged:
                    turn.action_logged = True
                    self.turn_journal.save(turn)
                    self.append_story_log_entry({"type": "user_action", "action": turn.action})
            # Establish every committed game outcome before interpreting speech.
            for turn in turns:
                if turn.ingress != "game_action" or turn.recorder_completed:
                    continue
                event = self._recording_event(turn)
                outcome = self.recorder_module.control_game(event)
                decision = self.recording_journal.game_control_decision(event.event_id)
                assert decision is not None
                turn.recorder_completed = True
                turn.narration_reason = decision.reason
                turn.responder_skipped = outcome.narration_request is None
                turn.narrative_brief = outcome.narration_request.brief if outcome.narration_request is not None else None
                self.turn_journal.save(turn)

            spoken = [turn for turn in turns if turn.ingress == "communicated_action"]
            narrated = [turn for turn in turns if turn.ingress == "game_action" and not turn.responder_skipped]
            owner = spoken[0] if spoken else (narrated[0] if narrated else None)
            if owner is not None:
                if owner.response is None:
                    action = "\n".join(turn.action for turn in spoken) if spoken else "Narrate the recorder-established outcome."
                    nudges = [turn.nudge for turn in spoken if turn.nudge]
                    nudges.extend(turn.narrative_brief for turn in narrated if turn.narrative_brief)
                    if spoken and any(turn.ingress == "game_action" for turn in turns):
                        nudges.append("Combine the spoken actions with this cycle's already committed game outcomes. Do not resolve those game actions again.")
                    result = self.response_module._resolve_user_action(
                        action, nudge="\n".join(nudges), publish=False,
                        game_entry=not spoken, stage_id=owner.turn_id,
                    )
                    if result.get("error"):
                        raise RuntimeError(str(result["error"]))
                    owner.response = TypeAdapter(dict[str, JsonValue]).validate_python(result)
                    self.turn_journal.save(owner)
                if spoken and not owner.recorder_completed:
                    self.recorder_module.record(self._recording_event(owner))
                    owner.recorder_completed = True
                    self.turn_journal.save(owner)
                for turn in turns:
                    if turn.turn_id != owner.turn_id:
                        turn.response_owner_id = owner.turn_id
                        if turn.ingress == "communicated_action":
                            turn.recorder_completed = True
                        self.turn_journal.save(turn)
                if not owner.delivered:
                    self._handle_scene_reaction(owner.response)
                    owner.delivered = True
                    self.turn_journal.save(owner)
            self.sync_character_bindings()
            self._plan_batch(turns, owner or turns[0])
            return owner.response if owner is not None and owner.response is not None else {
                "status": "completed", "narration_skipped": True, "reason": turns[0].narration_reason,
            }
        except GameEntryNarrationRejected:
            logger.warning("Story cycle %s awaits corrected game-entry narration", turns[0].cycle_id)
            return {"status": "pending", "story_turn_id": turns[0].turn_id}
        except Exception as exc:
            logger.exception("Story cycle %s remains pending", turns[0].cycle_id)
            result = {"error": str(exc), "story_turn_id": turns[0].turn_id}
            self._notify_scene_reaction(result)
            return result
        finally:
            self._set_user_action_processing(False)

    def _plan_batch(self, turns: list[StoryTurn], owner: StoryTurn) -> None:
        if all(turn.planner_completed for turn in turns):
            return
        try:
            for attempt in range(3):
                source = self.notepad.snapshot()
                handoff = owner.model_dump(mode="json")
                handoff["cycle_inputs"] = [turn.model_dump(mode="json") for turn in turns]
                handoff["source_event_ids"] = [f"{turn.turn_id}:recording" for turn in turns if turn.response_owner_id is None or turn.ingress == "game_action"]
                handoff["audience"] = ["planner"]
                game = self.canvas_manager.game
                game_snapshot = game.story_snapshot("narratron") if game is not None else None
                handoff["game_state"] = game_snapshot
                updates = self.planning_module.plan_recorded_turn(handoff, source)

                def persist(snapshot: NoteSnapshot) -> None:
                    self.turn_journal.commit_cycle_planning(turns, snapshot)

                try:
                    if game is not None and game.story_snapshot("narratron") != game_snapshot:
                        raise StaleNoteSnapshot("Game state changed during planning; recheck the guidance.")
                    self.notepad.publish_role_updates("planner", updates, source=source, persist=persist)
                    return
                except StaleNoteSnapshot:
                    if attempt == 2:
                        raise
        except Exception:
            logger.exception("Story cycle planner %s remains pending", owner.turn_id)

    def _notify_scene_reaction(self, result: dict[str, JsonValue]) -> None:
        if self._on_scene_reaction is not None:
            try:
                self._on_scene_reaction(result)
            except Exception:
                logger.exception("Story result notification failed")

    def _handle_scene_reaction(self, result: dict[str, JsonValue]) -> None:
        self.response_module.deliver_response(result)
        self.append_story_log_entry({"type": "story_response", "output": result})
        self._notify_scene_reaction(result)

    def _handle_player_game_action(self, action: GameAction, new_state: dict[str, JsonValue]) -> None:
        game = self.canvas_manager.game
        acknowledgements: list[str] = []
        with self._intake_lock:
            if self._closed:
                return
            # Post-commit callbacks can arrive out of order. Drain the durable
            # game outbox in revision order before accepting the callback itself.
            pending = game.pending_story_actions() if game is not None else []
            if not any(receipt == new_state for _, receipt in pending):
                pending.append((action, new_state))
            for player_action, receipt in sorted(pending, key=lambda item: item[0].revision):
                event_id = str(receipt.get("story_event_id") or f"game:{player_action.revision}")
                turn = self.turn_journal.accept(StoryTurn(
                    turn_id=event_id, ingress="game_action", action="Game panel action",
                    source_revision=self.notepad.revision,
                    game_payload={"action": player_action.model_dump(mode="json"), "receipt": receipt, "actor": "player"},
                ))
                self._schedule_turn(turn)
                if receipt.get("story_event_id"):
                    acknowledgements.append(event_id)
        if game is not None:
            for event_id in acknowledgements:
                game.acknowledge_story_action(event_id)

    def retry_pending_turns(self) -> None:
        for turn in self.turn_journal.turns():
            if not turn.completed or (turn.response is not None and not turn.delivered):
                self._schedule_turn(turn)

    def submit_action(self, user_action: str, nudge: str = "", *, require_input: bool = True) -> tuple[str, Future[dict[str, JsonValue]]]:
        with self._intake_lock:
            return self._accept_action(user_action, nudge, require_input=require_input)

    def _accept_action(self, user_action: str, nudge: str, *, require_input: bool) -> tuple[str, Future[dict[str, JsonValue]]]:
        if self._closed:
            raise ValueError("Story engine is closed.")
        action = user_action.strip()
        if not action:
            raise ValueError("User action cannot be empty.")
        if action == "Narrate the recorder-established outcome.":
            raise ValueError("Game-entry narration is already resolved; do not submit it to process_user_action.")
        if len(action) > 2000 or len(nudge) > 1000:
            raise ValueError("Story action or nudge exceeds its size limit.")
        if not self.response_module.adventure_mode:
            raise ValueError("Adventure Mode is not enabled for this theater.")
        for pending in self.turn_journal.turns():
            if not pending.foreground_ready and pending.ingress == "communicated_action" and pending.action == action and pending.nudge == nudge:
                return pending.turn_id, self._schedule_turn(pending)
        if require_input and not self.response_module.consume_user_input():
            raise ValueError("No input from the orator was detected.")
        try:
            turn = self.turn_journal.accept(StoryTurn(
                turn_id=uuid4().hex, ingress="communicated_action", action=action, nudge=nudge,
            ))
        except Exception:
            if require_input:
                self.response_module.record_user_input()
            raise
        return turn.turn_id, self._schedule_turn(turn)

    def resolve_action(self, user_action: str, nudge: str = "", *, drain: bool = False) -> dict[str, JsonValue]:
        _, future = self.submit_action(user_action, nudge, require_input=False)
        self.cycle.flush()
        result = future.result(timeout=300)
        if drain:
            self.wait_for_pending_events(300)
        return result

    def process_user_action(self, user_action: str, nudge: str = "") -> dict[str, JsonValue]:
        """Submit a communicated action through the canvas cycle's input gate."""
        try:
            turn_id, _ = self.submit_action(user_action, nudge)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"status": "processing", "story_turn_id": turn_id, "message": "Story action queued for the next cycle."}

    def process_system_action(self, user_action: str, message_type: str, nudge: str = "") -> dict[str, JsonValue]:
        """Auto-begin and resume use the same serialized intake as spoken input."""
        try:
            turn_id, _ = self.submit_action(user_action, nudge, require_input=False)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"status": "processing", "story_turn_id": turn_id, "message": message_type}

    def wait_for_pending_events(self, timeout: float = 5.0) -> bool:
        return self.cycle.wait(timeout) and all(
            turn.completed and (turn.response is None or turn.delivered) for turn in self.turn_journal.turns()
        )

    def wait_for_deep_planning(self, timeout: float = 5.0, target_turn_id: int | None = None) -> bool:
        return self.wait_for_pending_events(timeout)

    @property
    def is_action_in_flight(self) -> bool:
        return self.cycle.is_active

    def close(self) -> None:
        with self._intake_lock:
            if self._closed:
                return
            self._closed = True
        game = self.canvas_manager.game
        if game is not None and game.on_player_action == self._handle_player_game_action:
            game.on_player_action = None
        self.cycle.close()
        self.notepad.on_change = None
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


    @property
    def on_scene_reaction(self) -> Optional[Callable[[dict[str, JsonValue]], None]]:
        return self._on_scene_reaction

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
    def max_sticky_notes(self) -> int:
        return self.notepad.max_sticky_notes

    @max_sticky_notes.setter
    def max_sticky_notes(self, value: int) -> None:
        self.notepad.max_sticky_notes = max(1, int(value))

    @property
    def max_stickies(self) -> int:
        return self.notepad.max_stickies

    def create_or_update_scene(self, name: str, description: str = "") -> str:
        """Create or update a scene reference visual by name and concept description."""
        return self.response_module.create_or_update_scene(name=name, description=description)

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
    "StoryEngine",
    "StoryLogEntry",
    "StoryResponseOutput",
]
