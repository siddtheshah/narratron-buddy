"""Mandatory factual recording boundary and explicit game-control phase."""

from collections.abc import Callable
import json
import logging
from threading import Lock, RLock
from typing import Literal

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.agents import Agent
from google.genai import types
from jinja2 import Template
from pydantic import JsonValue, TypeAdapter

from components.canvas.game_runtime import GameAction
from components.canvas.game_state import GameState
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from components.reference_manager import ReferenceManager
from tools.story.story_handoffs import (
    CommittedGameReceipt,
    GameControlDecision,
    GameControlOutcome,
)
from tools.story.story_models import DEFAULT_STORY_PLANNING_STYLE, VertexGemini
from tools.story.story_session import StorySession
from tools.story.story_staged_notes import StagedStoryNotes
from tools.story.story_recording_journal import RecordingEvent, StoryRecordingJournal

logger = logging.getLogger(__name__)

MAX_RECORDER_READ_LORE_CALLS_PER_RUN = 4
MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN = 4

_RECORDER_INSTRUCTION_TEMPLATE = Template(
    """# Role & Mission
You are the factual recorder and game controller for an interactive adventure. You do not narrate the current scene, speak for NPCs, roll dice, or invent conditional future plans.

You own only `recorder`-owned sticky notes and validated Narratron game actions. Assimilate the committed event by deciding which recorder-owned sticky notes, if any, require an update. Use `check_schema(topic)` before updating a schema-backed sticky note and `update_sticky_note(topic, info)` once per changed note; never attempt to write `planner`-owned sticky notes or restate notes that did not change.
{% if allow_freeform -%}
Freeform sticky creation is enabled. You may also create and update shared freeform topics with `update_sticky_note(topic, info)`, using a descriptive topic and a concise plain string for established facts. No planning.yaml entry is needed for a new topic. Shared freeform notes can be updated by either recorder or planner; keep contingent plans distinct from established facts and avoid duplicating existing topics. Declared topics still require their owner's schema. Notes have a bounded capacity; older freeform topics may be evicted.
{% endif -%}
Follow your pinned RECORDER.md for adventure-specific recording and game-control rules. The responder (RESPONDER.md) owns immediate action resolution and narration, and the deep planner (PLANNER.md) owns long-horizon conditional plans and `planner`-owned sticky notes.

# Phase 1 — Factual Recording Rules
- Committed events are established facts; unconfirmed player intentions and conditional future plans are not facts.
- Reconcile responder `proposed_factual_changes` (each with `topic`, `info`, and supporting `evidence`) against established sticky notes, lore rules, actual dice results, and committed game receipts. Proposals are claims for verification, not automatic mutations: reject proposals that treat unverified intentions as outcomes or that contradict validated game state.
- Independently audit the entire committed event (player action, narration, dialogue, dice results, `planning_signals`, and game receipts) so required updates to inventory, location, stats, resources, or status are recorded even if omitted from `proposed_factual_changes`.
- Preserve causal continuity and never count a single action or its costs twice.
{% if has_game -%}

# Phase 2 — Game Control Rules
- Both completed story responses and root committed player game actions permit validated Narratron game actions during recording. Narratron game receipts are evidence to record and never open another action stage.
- Let the game's action contracts and guards determine prerequisites, including required player input, consent, or turn order. A story response does not waive those prerequisites or authorize acting for the player.
- Read the Current Game State's `instructions`, `state`, `revision`, and listed Narratron `actions` before deciding whether to act. The game runtime validates the complete underlying state and every submitted action.
- Call `take_game_action(name, arguments_json, revision)` only when a listed Narratron action is justified by the game rules and established story facts. Each successful game action advances the revision and produces a durable receipt; read the returned state and revision before taking another action.
- Only root player game-entry turns can request narration. When recording a completed story response, execute justified game actions and return narration_request=null. For significant game-entry developments, include a concise narrative brief containing only established outcomes, consequences, and narration cues. Exclude raw game state, receipts, revisions, action schemas, and mechanical instructions. Routine mechanical updates finish with narration_request=null. The orchestrator schedules narration; you cannot trigger another role.
{% endif -%}

# Lore and Reference Tools
Use `read_lore` and `search_lore` to verify mechanics, item properties, locations, and state-tracking rules, with up to {{ max_read_lore_calls }} reads and {{ max_search_lore_calls }} searches per run. Prefer established lore over invention.

# Completion
Stage all factual updates for the committed event and every game receipt before completing this one logical stage. Return JSON matching GameControlDecision, with an explicit reason and narration_request (null to skip). A requested brief contains established narrative facts only. On response-recording turns, narration_request must be null. Use reference_updates only to reconcile established canonical player, character, or scene changes; conditional future character or scene developments belong in planner notes. The application publishes staged factual updates together.
{% if style -%}

# Story-Planning Style
{{ style }}
{% endif -%}
{% if sticky_schema_json -%}

# Recorder Sticky State Schema Reference
Each recorder-owned topic below is independently validated by the notepad tool. Call `check_schema(topic)` before updating a schema-backed note; for field-backed notes, pass `info` as a JSON object encoded as a string.
{{ sticky_schema_json }}
{% endif -%}
{% if lore_context -%}

# Available Theater Lore
Available theater lore (top-level documents and directories):
{{ lore_context }}
{% endif -%}
"""
)

_RECORDER_TURN_PROMPT_TEMPLATE = Template(
    """# Recorder Work ({{ phase }})
Reconcile the committed event against the current snapshot and update only `recorder`-owned sticky notes that changed.
{% if game_state_json -%}
# Current Game State and Narratron Actions
{{ game_state_json }}
{% endif -%}

# Committed Event (`{{ event.kind }}`)
- Event ID: `{{ event.event_id }}`
- Action ID: `{{ event.action_id }}`
- Source revision: {{ event.source_revision }}
- Payload:
{{ event_payload_json }}

# Current Sticky Notes (Snapshot Revision {{ snapshot_revision }})
{% if current_notes -%}
{% for note in current_notes -%}
- [{{ note.owner }}] {{ note.topic }}: {{ note.info }}
{% endfor -%}
{% else -%}
(No active sticky notes)
{% endif -%}
"""
)


def build_recorder_instructions(
    *,
    style: str = "",
    sticky_schema_json: str = "",
    lore_context: str = "",
    has_game: bool = False,
    allow_freeform: bool = False,
    max_read_lore_calls: int = MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
    max_search_lore_calls: int = MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
) -> str:
    return _RECORDER_INSTRUCTION_TEMPLATE.render(
        style=style,
        sticky_schema_json=sticky_schema_json,
        lore_context=lore_context,
        has_game=has_game,
        allow_freeform=allow_freeform,
        max_read_lore_calls=max_read_lore_calls,
        max_search_lore_calls=max_search_lore_calls,
    ).strip()


def build_recorder_turn_prompt(
    event: RecordingEvent,
    snapshot: NoteSnapshot,
    *,
    owners: dict[str, str] | None = None,
    phase: Literal["record", "control_game"] = "record",
    game_state_json: str = "",
) -> str:
    owner_map = owners or {}
    current_notes = [
        {
            "topic": topic,
            "info": info,
            "owner": owner_map.get(topic, "unknown"),
        }
        for topic, info in snapshot.notes.items()
    ]
    return _RECORDER_TURN_PROMPT_TEMPLATE.render(
        phase=phase,
        event=event,
        event_payload_json=json.dumps(event.payload, ensure_ascii=False, indent=2),
        snapshot_revision=snapshot.revision,
        current_notes=current_notes,
        game_state_json=game_state_json,
    ).strip()


ReconcileFacts = Callable[[RecordingEvent, NoteSnapshot], dict[str, str]]
SubmitGameAction = Callable[[GameAction], dict[str, JsonValue]]
ControlGame = Callable[
    [RecordingEvent, NoteSnapshot, SubmitGameAction],
    GameControlDecision | None,
]


class StoryRecorderModule:
    """Reconcile persisted outcomes, control game responses, and commit notes atomically.

    The injected reconciler receives committed events and a consistent snapshot.
    Its returned changes are independently authorized and validated by Notepad.
    Completed response recording (`record`) can execute validated game actions
    without requesting more narration. Game-entry decisions (`control_game`)
    can also request narration. Both preserve game receipts across retries;
    recording a Narratron receipt cannot execute further actions.
    """

    def __init__(
        self,
        notepad: Notepad,
        journal: StoryRecordingJournal,
        reconcile: ReconcileFacts | None = None,
        *,
        lore_library: LoreLibrary | None = None,
        game_state: GameState | None = None,
        control_game: ControlGame | None = None,
        style: str = "",
        story_session: StorySession | None = None,
        reference_manager: ReferenceManager | None = None,
    ) -> None:
        self.scoped_notepad = notepad.for_role("recorder")  # Fail early on unmigrated ownership.
        self.notepad = notepad
        self.journal = journal
        self.lore_library = lore_library
        self.story_session = story_session
        self.reference_manager = reference_manager
        self.staged_notes = StagedStoryNotes(notepad, "recorder")
        self._reconcile = reconcile or self._reconcile_model
        self._game_state = game_state
        self._control_game = control_game
        raw_config = notepad.theater.config()
        config = raw_config.get("story_planning", raw_config)
        configured_style = style or str(config.get("style", DEFAULT_STORY_PLANNING_STYLE))
        self.style: str = configured_style.strip() or DEFAULT_STORY_PLANNING_STYLE
        self._read_lore_calls_this_run = 0
        self._search_lore_calls_this_run = 0
        self._lore_calls_lock = Lock()
        self._lock = RLock()
        self._submit_game_action: SubmitGameAction | None = None
        self.agent: Agent | None = None
        if story_session is not None:
            self.agent = Agent(
                name="story_recorder",
                model=VertexGemini(
                    model=str(config.get("recorder_model") or config.get("responder_model") or "gemini-3.7-flash"),
                    project_id=config.get("vertex_project"),
                    location=str(config.get("vertex_location") or "global"),
                ),
                instruction=self._build_recorder_instruction,
                tools=[self.read_lore, self.search_lore, self.staged_notes.check_schema,
                       self.staged_notes.update_sticky_note, self.take_game_action,
                       *([reference_manager.lookup_character, reference_manager.get_player_character]
                         if reference_manager is not None else [])],
                output_schema=GameControlDecision,
                output_key="story_recorder_output",
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
                generate_content_config=types.GenerateContentConfig(temperature=0.2),
            )
        logger.debug(
            "[StoryRecorderModule] Initialized (has_story_session=%s, has_lore=%s, has_game_state=%s, has_reference_manager=%s)",
            story_session is not None,
            lore_library is not None,
            game_state is not None,
            reference_manager is not None,
        )
        saved = journal.latest_snapshot()
        if saved is not None:
            logger.debug("[StoryRecorderModule] Restoring recorded snapshot revision=%d", saved.revision)
            notepad.restore_recorded_snapshot(saved)

    def _run_model(
        self, event: RecordingEvent, snapshot: NoteSnapshot, *,
        phase: Literal["record", "control_game"],
    ) -> GameControlDecision:
        if self.story_session is None:
            raise RuntimeError("A shared story session is required for model recording.")
        logger.debug(
            "[StoryRecorderModule] Running recorder model for event %s (action_id=%s, phase=%s, snapshot_revision=%d)",
            event.event_id,
            event.action_id,
            phase,
            snapshot.revision,
        )
        self.reset_lore_call_counts()
        self.staged_notes.updates.clear()
        prompt = self._build_recorder_turn_prompt(event, snapshot, phase=phase)
        if phase == "control_game" or event.kind == "completed_response":
            prompt += "\n# Already committed Narratron receipts (do not repeat)\n" + json.dumps([
                receipt.model_dump(mode="json") for receipt in self.journal.narratron_receipts(event.event_id)
            ], ensure_ascii=False)
        raw = self.story_session.run("recorder", prompt, stage_id=event.action_id)
        logger.debug("[StoryRecorderModule] Recorder model returned for event %s: %s", event.event_id, raw)
        if phase == "control_game":
            fields = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
            if "reason" not in fields or "narration_request" not in fields:
                raise ValueError("Game control must return an explicit narration decision and reason.")
        decision = GameControlDecision.model_validate_json(raw)
        updates = {**decision.factual_updates, **self.staged_notes.updates}
        for topic, info in updates.items():
            self.notepad.validate_role_update("recorder", topic, info)
        logger.debug(
            "[StoryRecorderModule] Model decision validated for event %s: updates=%s, narration_request=%s, reference_updates=%d",
            event.event_id,
            list(updates.keys()),
            decision.narration_request is not None,
            len(decision.reference_updates),
        )
        return decision.model_copy(update={"factual_updates": updates})

    def _reconcile_model(self, event: RecordingEvent, snapshot: NoteSnapshot) -> dict[str, str]:
        decision = self.journal.game_control_decision(event.event_id)
        if decision is None:
            logger.debug("[StoryRecorderModule] No saved decision for event %s; running recorder model", event.event_id)
            def submit(action: GameAction) -> dict[str, JsonValue]:
                return self._commit_game_action(event, action)

            self._submit_game_action = submit if event.kind == "completed_response" else None
            try:
                decision = self._run_model(event, snapshot, phase="record")
            finally:
                self._submit_game_action = None
            if decision.narration_request is not None:
                raise ValueError("Response recording cannot request another responder stage.")
            self.journal.save_game_control_decision(event.event_id, decision)
        else:
            logger.debug("[StoryRecorderModule] Reusing saved game control decision for event %s", event.event_id)
        if decision.narration_request is not None:
            raise ValueError("Response recording cannot request another responder stage.")
        return decision.factual_updates

    def _apply_reference_updates(self, event_id: str) -> None:
        decision = self.journal.game_control_decision(event_id)
        if decision is None or self.reference_manager is None:
            return
        if decision.reference_updates:
            logger.debug(
                "[StoryRecorderModule] Applying %d reference update(s) for event %s",
                len(decision.reference_updates),
                event_id,
            )
        for update in decision.reference_updates:
            logger.debug("[StoryRecorderModule] Applying reference update: kind=%s, name=%s", update.kind, update.name)
            if update.kind == "player":
                self.reference_manager.update_player_character(name=update.name, image_description=update.description)
            elif update.kind == "scene":
                self.reference_manager.create_or_update_scene(name=update.name, description=update.description)
            else:
                self.reference_manager.create_or_update_character(
                    name=update.name, description=update.description, personality=update.personality,
                    motivation=update.motivation, quirk=update.quirk,
                    gender=update.gender, voice_tags=update.voice_tags,
                )

    def take_game_action(self, name: str, arguments_json: str, revision: int) -> str:
        """Execute a validated Narratron action while recording a story response or player game event."""
        logger.debug(
            "[StoryRecorderModule] take_game_action called: name=%s, revision=%d, arguments=%s",
            name,
            revision,
            arguments_json,
        )
        if self._submit_game_action is None:
            logger.warning("[StoryRecorderModule] take_game_action rejected: no active response or game-entry recording stage")
            return "Error: Game actions require an active story-response or game-entry recording stage."
        try:
            action = GameAction(name=name, arguments=TypeAdapter(dict[str, JsonValue]).validate_json(arguments_json), revision=revision)
            result = json.dumps(self._submit_game_action(action), ensure_ascii=False)
            logger.debug("[StoryRecorderModule] take_game_action %s succeeded: %s", name, result)
            return result
        except ValueError as exc:
            logger.warning("[StoryRecorderModule] take_game_action %s failed: %s", name, exc)
            return f"Error: {exc}"

    def _control_model_stage(self, event: RecordingEvent) -> GameControlOutcome:
        """One recorder model stage covers decisions and all factual projections."""
        logger.debug("[StoryRecorderModule] _control_model_stage started for event %s (action_id=%s)", event.event_id, event.action_id)
        with self._lock:
            self.journal.enqueue(event)
            if self.journal.recorded_revision(event.event_id) is None:
                pending = self.journal.pending()
                if not pending or pending[0].event_id != event.event_id:
                    raise ValueError("An earlier event still requires recording.")
            self._recover_game_receipts(event)
            decision = self.journal.game_control_decision(event.event_id)
            if decision is None:
                def submit(action: GameAction) -> dict[str, JsonValue]:
                    return self._commit_game_action(event, action)

                self._submit_game_action = submit
                try:
                    decision = self._run_model(event, self.notepad.snapshot(), phase="control_game")
                finally:
                    self._submit_game_action = None
                if decision.narration_request is not None and not decision.narration_request.brief:
                    raise ValueError("Narration requires a concise recorder narrative brief.")
                self.journal.save_game_control_decision(event.event_id, decision)
                logger.debug(
                    "[StoryRecorderModule] Game control decision saved for event %s (narration_request=%s, factual_updates=%s)",
                    event.event_id,
                    decision.narration_request is not None,
                    list(decision.factual_updates.keys()),
                )

            revision = self.journal.recorded_revision(event.event_id)
            if revision is None:
                source = self.notepad.snapshot()

                def persist(snapshot: NoteSnapshot) -> None:
                    self.journal.commit(event.event_id, snapshot)

                revision = self.notepad.publish_role_updates(
                    "recorder", decision.factual_updates, source=source, persist=persist,
                )
                logger.debug("[StoryRecorderModule] Published recorder updates for event %s at revision %d", event.event_id, revision)
            else:
                saved = self.journal.latest_snapshot()
                if saved is not None and saved.revision > self.notepad.revision:
                    logger.debug("[StoryRecorderModule] Restoring recorded snapshot revision %d for event %s", saved.revision, event.event_id)
                    self.notepad.restore_recorded_snapshot(saved)
            receipts = self._complete_game_recording(event)
            self._apply_reference_updates(event.event_id)
            logger.debug(
                "[StoryRecorderModule] Game control model stage complete for event %s (revision=%d, receipts=%d, narration=%s)",
                event.event_id,
                revision,
                len(receipts),
                decision.narration_request is not None,
            )
            return GameControlOutcome(
                event_id=event.event_id, player_recorded_revision=revision,
                narratron_receipts=receipts, final_recorded_revision=revision,
                narration_request=decision.narration_request,
            )

    def _recover_game_receipts(self, event: RecordingEvent) -> None:
        """Recover actions persisted before an interrupted journal write."""
        if self._game_state is None:
            return
        commits = self._game_state.story_action_commits(f"{event.event_id}:narratron:")
        if commits:
            logger.debug(
                "[StoryRecorderModule] Recording %d existing Narratron commit(s) from game state for event %s",
                len(commits), event.event_id,
            )
        for receipt_id, action, receipt in commits:
            self.journal.record_narratron_receipt(CommittedGameReceipt(
                event_id=receipt_id, parent_event_id=event.event_id, action_id=event.action_id,
                source_revision=event.source_revision, action=action, receipt=receipt,
            ))

    def _complete_game_recording(self, event: RecordingEvent) -> tuple[CommittedGameReceipt, ...]:
        """Publish receipts already reconciled by this event's model invocation."""
        receipts = tuple(self.journal.narratron_receipts(event.event_id))
        for receipt in receipts:
            if self.journal.recorded_revision(receipt.event_id) is None:
                self.journal.commit(receipt.event_id, self.notepad.snapshot())
        if self.journal.game_control_decision(event.event_id) is not None:
            self.journal.complete_game_control(event.event_id)
        return receipts

    def _commit_game_action(self, event: RecordingEvent, action: GameAction) -> dict[str, JsonValue]:
        logger.debug(
            "[StoryRecorderModule] Committing Narratron game action %s (revision=%d) for event %s",
            action.name,
            action.revision,
            event.event_id,
        )
        existing = self.journal.narratron_receipt_for_revision(event.event_id, action.revision)
        if existing is not None:
            if existing.action != action:
                raise ValueError("Game revision already has a different committed Narratron action.")
            logger.debug(
                "[StoryRecorderModule] Reusing existing Narratron receipt for event %s at revision %d",
                event.event_id,
                action.revision,
            )
            return existing.receipt
        if self._game_state is None:
            raise ValueError("No game state is configured for Narratron game actions.")
        receipt = self._game_state.commit_story_action(action, f"{event.event_id}:narratron:{action.revision}")
        committed = CommittedGameReceipt(
            event_id=f"{event.event_id}:narratron:{action.revision}",
            parent_event_id=event.event_id, action_id=event.action_id,
            source_revision=event.source_revision, action=action, receipt=receipt,
        )
        self.journal.record_narratron_receipt(committed)
        logger.debug(
            "[StoryRecorderModule] Narratron game action %s committed at revision %d for event %s",
            action.name,
            action.revision,
            event.event_id,
        )
        return receipt

    def read_lore(self, document: str = "") -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story recorder."
        with self._lore_calls_lock:
            if self._read_lore_calls_this_run >= MAX_RECORDER_READ_LORE_CALLS_PER_RUN:
                logger.warning(
                    "[StoryRecorderModule] read_lore limit reached (%d/%d)",
                    self._read_lore_calls_this_run,
                    MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
                )
                return (
                    f"Error: Maximum recorder read_lore limit "
                    f"({MAX_RECORDER_READ_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize factual recording now."
                )
            self._read_lore_calls_this_run += 1
            call_count = self._read_lore_calls_this_run
        logger.debug(
            "[StoryRecorderModule] read_lore called (%d/%d): document=%r",
            call_count,
            MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
            document,
        )
        return self.lore_library.read_lore(document, role="recorder")

    def search_lore(self, query: str) -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story recorder."
        with self._lore_calls_lock:
            if self._search_lore_calls_this_run >= MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN:
                logger.warning(
                    "[StoryRecorderModule] search_lore limit reached (%d/%d)",
                    self._search_lore_calls_this_run,
                    MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
                )
                return (
                    f"Error: Maximum recorder search_lore limit "
                    f"({MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize factual recording now."
                )
            self._search_lore_calls_this_run += 1
            call_count = self._search_lore_calls_this_run
        logger.debug(
            "[StoryRecorderModule] search_lore called (%d/%d): query=%r",
            call_count,
            MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
            query,
        )
        return self.lore_library.search_lore(query)

    def reset_lore_call_counts(self) -> None:
        with self._lore_calls_lock:
            self._read_lore_calls_this_run = 0
            self._search_lore_calls_this_run = 0
        logger.debug("[StoryRecorderModule] Reset lore call counts")

    def _recorder_sticky_schema_json(self) -> str:
        recorder_definitions = {
            topic: {
                k: v
                for k, v in definition.items()
                if k in ("description", "fields", "render", "required", "owner") and v is not None
            }
            for topic, definition in self.notepad.sticky_definitions.items()
            if definition.get("owner") == "recorder"
        }
        if not recorder_definitions:
            return ""
        return json.dumps(recorder_definitions, ensure_ascii=False, indent=2)

    def _build_recorder_instruction(self, ctx: ReadonlyContext | None = None) -> str:
        lore_context = (
            self.lore_library.get_lore_context(role="recorder")
            if self.lore_library is not None
            else ""
        )
        return build_recorder_instructions(
            style=self.style,
            sticky_schema_json=self._recorder_sticky_schema_json(),
            lore_context=lore_context,
            has_game=self._game_state is not None and self._game_state.program is not None,
            allow_freeform=not self.notepad.enforce_structured,
        )

    def _build_recorder_turn_prompt(
        self,
        event: RecordingEvent,
        snapshot: NoteSnapshot,
        *,
        phase: Literal["record", "control_game"] = "record",
    ) -> str:
        owners = {
            topic: str(definition.get("owner") or "unknown")
            for topic, definition in self.notepad.sticky_definitions.items()
        }
        game_state_json = ""
        if self._game_state is not None and self._game_state.program is not None:
            game_snapshot = self._game_state.story_snapshot("narratron")
            if game_snapshot is not None:
                game_state_json = json.dumps(game_snapshot, ensure_ascii=False)
        return build_recorder_turn_prompt(
            event,
            snapshot,
            owners=owners,
            phase=phase,
            game_state_json=game_state_json,
        )

    def record(self, event: RecordingEvent) -> int:
        """Retain failed events for retry and never reconcile a recorded event twice."""
        logger.debug(
            "[StoryRecorderModule] record() called for event %s (kind=%s, action_id=%s, source_rev=%d)",
            event.event_id,
            event.kind,
            event.action_id,
            event.source_revision,
        )
        with self._lock:
            self.journal.enqueue(event)
            if event.kind == "completed_response":
                self._recover_game_receipts(event)
            recorded = self.journal.recorded_revision(event.event_id)
            if recorded is not None:
                logger.debug(
                    "[StoryRecorderModule] Event %s already recorded at revision %d; skipping reconcile",
                    event.event_id,
                    recorded,
                )
                saved = self.journal.latest_snapshot()
                if saved is not None and saved.revision > self.notepad.revision:
                    self.notepad.restore_recorded_snapshot(saved)
                if event.kind == "completed_response":
                    self._complete_game_recording(event)
                self._apply_reference_updates(event.event_id)
                return recorded
            pending = self.journal.pending()
            if not pending or pending[0].event_id != event.event_id:
                raise ValueError("An earlier event still requires recording.")
            source = self.notepad.snapshot()
            logger.debug(
                "[StoryRecorderModule] Reconciling event %s against snapshot revision %d",
                event.event_id,
                source.revision,
            )
            updates = self._reconcile(event, source)
            logger.debug(
                "[StoryRecorderModule] Reconciled %d update(s) for event %s: %s",
                len(updates),
                event.event_id,
                list(updates.keys()),
            )

            def persist(snapshot: NoteSnapshot) -> None:
                self.journal.commit(event.event_id, snapshot)

            revision = self.notepad.publish_role_updates(
                "recorder", updates, source=source, persist=persist,
            )
            logger.debug(
                "[StoryRecorderModule] Published recorded revision %d for event %s",
                revision,
                event.event_id,
            )
            if event.kind == "completed_response":
                self._complete_game_recording(event)
            self._apply_reference_updates(event.event_id)
            return revision

    def control_game(self, event: RecordingEvent) -> GameControlOutcome:
        """Record a player game move, execute Narratron responses once, and optionally request narration."""
        logger.debug(
            "[StoryRecorderModule] control_game() called for event %s (action_id=%s, kind=%s)",
            event.event_id,
            event.action_id,
            event.kind,
        )
        if event.kind != "committed_game_action":
            raise ValueError("Completed responses cannot enter game control.")
        if event.source_event_ids or event.payload.get("actor") == "narratron":
            raise ValueError("Narratron game receipts cannot recursively enter game control.")
        if self.story_session is not None:
            return self._control_model_stage(event)
        with self._lock:
            player_revision = self.record(event)
            decision = self.journal.game_control_decision(event.event_id)
            if decision is None:
                def submit_game_action(action: GameAction) -> dict[str, JsonValue]:
                    existing = self.journal.narratron_receipt_for_revision(
                        event.event_id, action.revision,
                    )
                    if existing is not None:
                        if existing.action != action:
                            raise ValueError(
                                "Game revision already has a different committed Narratron action."
                            )
                        logger.debug(
                            "[StoryRecorderModule] Reusing existing Narratron receipt for event %s at revision %d",
                            event.event_id,
                            action.revision,
                        )
                        return existing.receipt
                    if self._game_state is None:
                        raise ValueError(
                            "No game state is configured for Narratron game actions."
                        )
                    logger.debug(
                        "[StoryRecorderModule] Executing Narratron game action %s (revision=%d) for event %s",
                        action.name,
                        action.revision,
                        event.event_id,
                    )
                    self._game_state.act(action, actor="narratron")
                    snapshot = self._game_state.story_snapshot("responder")
                    if snapshot is None:
                        raise ValueError("Game state did not return a committed receipt.")
                    committed = CommittedGameReceipt(
                        event_id=f"{event.event_id}:narratron:{action.revision}",
                        parent_event_id=event.event_id,
                        action_id=event.action_id,
                        source_revision=player_revision,
                        action=action,
                        receipt=snapshot,
                    )
                    self.journal.record_narratron_receipt(committed)
                    logger.debug(
                        "[StoryRecorderModule] Narratron game action %s committed at revision %d for event %s",
                        action.name,
                        action.revision,
                        event.event_id,
                    )
                    return snapshot

                raw_decision = (
                    self._control_game(event, self.notepad.snapshot(), submit_game_action)
                    if self._control_game is not None
                    else None
                )
                decision = raw_decision if raw_decision is not None else GameControlDecision()
                self.journal.save_game_control_decision(event.event_id, decision)
                logger.debug(
                    "[StoryRecorderModule] Saved game control decision for event %s (narration_request=%s, factual_updates=%s)",
                    event.event_id,
                    decision.narration_request is not None,
                    list(decision.factual_updates.keys()),
                )

            receipts = tuple(self.journal.narratron_receipts(event.event_id))
            final_revision = player_revision
            for committed in receipts:
                final_revision = self.record(
                    self.journal.receipt_recording_event(committed)
                )

            if not self.journal.is_game_control_completed(event.event_id):
                self.journal.complete_game_control(event.event_id)

            logger.debug(
                "[StoryRecorderModule] Game control completed for event %s: player_rev=%d, receipts=%d, final_rev=%d, narration=%s",
                event.event_id,
                player_revision,
                len(receipts),
                final_revision,
                decision.narration_request is not None,
            )
            return GameControlOutcome(
                event_id=event.event_id,
                player_recorded_revision=player_revision,
                narratron_receipts=receipts,
                final_recorded_revision=final_revision,
                narration_request=decision.narration_request,
            )

    def retry_pending(self) -> None:
        """Drain in causal queue order, stopping on the first unresolved failure."""
        pending = self.journal.pending()
        logger.debug("[StoryRecorderModule] retry_pending() called with %d pending event(s)", len(pending))
        for event in pending:
            self.record(event)
