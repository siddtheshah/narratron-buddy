"""Mandatory factual recording boundary and explicit game-control phase."""

from collections.abc import Callable
import json
from threading import Lock, RLock
from typing import Literal

from google.adk.agents.readonly_context import ReadonlyContext
from jinja2 import Template
from pydantic import JsonValue

from components.canvas.game_runtime import GameAction
from components.canvas.game_state import GameState
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from tools.story.story_handoffs import (
    CommittedGameReceipt,
    GameControlDecision,
    GameControlOutcome,
    NarrationRequest,
)
from tools.story.story_models import DEFAULT_STORY_PLANNING_STYLE
from tools.story.story_recording_journal import RecordingEvent, StoryRecordingJournal


MAX_RECORDER_READ_LORE_CALLS_PER_RUN = 4
MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN = 4

_RECORDER_INSTRUCTION_TEMPLATE = Template(
    """# Role & Mission
You are the factual recorder and game controller for an interactive adventure. You do not narrate the current scene, speak for NPCs, roll dice, or invent conditional future plans.

You own only `recorder`-owned sticky notes and validated Narratron game actions. Assimilate the committed event by deciding which recorder-owned sticky notes, if any, require an update. Use `check_schema(topic)` before updating a schema-backed sticky note and `update_sticky_note(topic, info)` once per changed note; never attempt to write `planner`-owned sticky notes or restate notes that did not change.
Follow your pinned RECORDER.md for adventure-specific recording and game-control rules. The responder (RESPONDER.md) owns immediate action resolution and narration, and the deep planner (PLANNER.md) owns long-horizon conditional plans and `planner`-owned sticky notes.

# Phase 1 — Factual Recording Rules
- Committed events are established facts; unconfirmed player intentions and conditional future plans are not facts.
- Reconcile responder `proposed_factual_changes` (each with `topic`, `info`, and supporting `evidence`) against established sticky notes, lore rules, actual dice results, and committed game receipts. Proposals are claims for verification, not automatic mutations: reject proposals that treat unverified intentions as outcomes or that contradict validated game state.
- Independently audit the entire committed event (player action, narration, dialogue, dice results, `planning_signals`, and game receipts) so required updates to inventory, location, stats, resources, or status are recorded even if omitted from `proposed_factual_changes`.
- Preserve causal continuity and never count a single action or its costs twice.
{% if has_game -%}

# Phase 2 — Game Control Rules
- Only root committed player game actions enter the game-control phase. Completed responder reactions and Narratron game receipts never re-enter game control.
- Read the Current Game State's `instructions`, `state`, `revision`, and listed Narratron `actions` before deciding whether to act. The game runtime validates the complete underlying state and every submitted action.
- Call `take_game_action(name, arguments_json, revision)` only when a listed Narratron action is justified by the game rules and established story facts. Each successful game action advances the revision and produces a durable receipt; read the returned state and revision before taking another action.
- When responding to a Player Game Action, request a story response (`trigger_story_response(user_action, nudge)`) only if the player's move or resulting game development warrants immediate scene narration or NPC dialogue. Routine mechanical updates finish without narration.
{% endif -%}

# Lore and Reference Tools
Use `read_lore` and `search_lore` to verify mechanics, item properties, locations, and state-tracking rules, with up to {{ max_read_lore_calls }} reads and {{ max_search_lore_calls }} searches per run. Prefer established lore over invention.

# Completion
After completing required tool updates, respond with a brief confirmation. Do not include a sticky-notes JSON payload in the final response.
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
    max_read_lore_calls: int = MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
    max_search_lore_calls: int = MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
) -> str:
    return _RECORDER_INSTRUCTION_TEMPLATE.render(
        style=style,
        sticky_schema_json=sticky_schema_json,
        lore_context=lore_context,
        has_game=has_game,
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
TriggerNarration = Callable[[NarrationRequest], dict[str, JsonValue] | str | None]


class StoryRecorderModule:
    """Reconcile persisted outcomes, control game responses, and commit notes atomically.

    The injected reconciler receives committed events and a consistent snapshot.
    Its returned changes are independently authorized and validated by Notepad.
    Response recording (`record`) deliberately has no game-action or narration
    capability. Game-control decisions (`control_game`) run only for root player
    game events and preserve committed Narratron game receipts across retries.
    """

    def __init__(
        self,
        notepad: Notepad,
        journal: StoryRecordingJournal,
        reconcile: ReconcileFacts,
        *,
        lore_library: LoreLibrary | None = None,
        game_state: GameState | None = None,
        control_game: ControlGame | None = None,
        trigger_narration: TriggerNarration | None = None,
        style: str = "",
    ) -> None:
        self.scoped_notepad = notepad.for_role("recorder")  # Fail early on unmigrated ownership.
        self.notepad = notepad
        self.journal = journal
        self.lore_library = lore_library
        self._reconcile = reconcile
        self._game_state = game_state
        self._control_game = control_game
        self._trigger_narration = trigger_narration
        configured_style = style or str(notepad.config.get("style", DEFAULT_STORY_PLANNING_STYLE))
        self.style: str = configured_style.strip() or DEFAULT_STORY_PLANNING_STYLE
        self._read_lore_calls_this_run = 0
        self._search_lore_calls_this_run = 0
        self._lore_calls_lock = Lock()
        self._lock = RLock()
        saved = journal.latest_snapshot()
        if saved is not None:
            notepad.restore_recorded_snapshot(saved)

    def read_lore(self, document: str = "") -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story recorder."
        with self._lore_calls_lock:
            if self._read_lore_calls_this_run >= MAX_RECORDER_READ_LORE_CALLS_PER_RUN:
                return (
                    f"Error: Maximum recorder read_lore limit "
                    f"({MAX_RECORDER_READ_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize factual recording now."
                )
            self._read_lore_calls_this_run += 1
        return self.lore_library.read_lore(document, role="recorder")

    def search_lore(self, query: str) -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story recorder."
        with self._lore_calls_lock:
            if self._search_lore_calls_this_run >= MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN:
                return (
                    f"Error: Maximum recorder search_lore limit "
                    f"({MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize factual recording now."
                )
            self._search_lore_calls_this_run += 1
        return self.lore_library.search_lore(query)

    def reset_lore_call_counts(self) -> None:
        with self._lore_calls_lock:
            self._read_lore_calls_this_run = 0
            self._search_lore_calls_this_run = 0

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
        if phase == "control_game" and self._game_state is not None and self._game_state.program is not None:
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
        with self._lock:
            self.journal.enqueue(event)
            recorded = self.journal.recorded_revision(event.event_id)
            if recorded is not None:
                saved = self.journal.latest_snapshot()
                if saved is not None and saved.revision > self.notepad.revision:
                    self.notepad.restore_recorded_snapshot(saved)
                return recorded
            pending = self.journal.pending()
            if not pending or pending[0].event_id != event.event_id:
                raise ValueError("An earlier event still requires recording.")
            source = self.notepad.snapshot()
            updates = self._reconcile(event, source)

            def persist(snapshot: NoteSnapshot) -> None:
                self.journal.commit(event.event_id, snapshot)

            return self.notepad.publish_role_updates(
                "recorder", updates, source=source, persist=persist,
            )

    def control_game(self, event: RecordingEvent) -> GameControlOutcome:
        """Record a player game move, execute Narratron responses once, and optionally request narration."""
        if event.kind != "committed_game_action":
            raise ValueError("Completed responses cannot enter game control.")
        if event.source_event_ids or event.payload.get("actor") == "narratron":
            raise ValueError("Narratron game receipts cannot recursively enter game control.")
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
                        return existing.receipt
                    if self._game_state is None:
                        raise ValueError(
                            "No game state is configured for Narratron game actions."
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
                    return snapshot

                raw_decision = (
                    self._control_game(event, self.notepad.snapshot(), submit_game_action)
                    if self._control_game is not None
                    else None
                )
                decision = raw_decision if raw_decision is not None else GameControlDecision()
                self.journal.save_game_control_decision(event.event_id, decision)

            receipts = tuple(self.journal.narratron_receipts(event.event_id))
            final_revision = player_revision
            for committed in receipts:
                final_revision = self.record(
                    self.journal.receipt_recording_event(committed)
                )

            narration_triggered = False
            if decision.narration_request is not None:
                if not self.journal.is_game_control_completed(event.event_id):
                    if self._trigger_narration is None:
                        raise ValueError(
                            "No narration trigger is configured for game control."
                        )
                    narration_result = self._trigger_narration(decision.narration_request)
                    if type(narration_result) is dict and "error" in narration_result:
                        raise RuntimeError(str(narration_result["error"]))
                    self.journal.complete_game_control(event.event_id)
                narration_triggered = True
            elif not self.journal.is_game_control_completed(event.event_id):
                self.journal.complete_game_control(event.event_id)

            return GameControlOutcome(
                event_id=event.event_id,
                player_recorded_revision=player_revision,
                narratron_receipts=receipts,
                final_recorded_revision=final_revision,
                narration_request=decision.narration_request,
                narration_triggered=narration_triggered,
            )

    def retry_pending(self) -> None:
        """Drain in causal queue order, stopping on the first unresolved failure."""
        for event in self.journal.pending():
            self.record(event)
