"""Mandatory factual recording boundary for sticky notes and canonical references."""

from collections.abc import Callable
import json
import logging
from threading import Lock, RLock

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types
from jinja2 import Template
from pydantic import JsonValue

from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from components.reference_manager import ReferenceManager
from components.story.story_handoffs import (
    CommittedGameReceipt,
    GameControlDecision,
)
from components.story.story_models import DEFAULT_STORY_PLANNING_STYLE, VertexGemini
from components.story.story_recording_journal import RecordingEvent, StoryRecordingJournal
from components.story.story_session import StorySession
from components.story.story_staged_notes import StagedStoryNotes

logger = logging.getLogger(__name__)

MAX_RECORDER_READ_LORE_CALLS_PER_RUN = 4
MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN = 4

_RECORDER_INSTRUCTION_TEMPLATE = Template(
    """# Role & Mission
You are the factual recorder for an interactive adventure. You do not narrate the current scene, speak for NPCs, roll dice, take game actions, or invent conditional future plans.

You own only `recorder`-owned sticky notes and canonical reference updates. Assimilate the committed event by deciding which recorder-owned sticky notes, if any, require an update. Use `check_schema(topic)` before updating a schema-backed sticky note and `update_sticky_note(topic, info)` once per changed note; never attempt to write `planner`-owned sticky notes or restate notes that did not change.
{% if allow_freeform -%}
Freeform sticky creation is enabled. You may also create and update shared freeform topics with `update_sticky_note(topic, info)`, using a descriptive topic and a concise plain string for established facts. No planning.yaml entry is needed for a new topic. Shared freeform notes can be updated by either recorder or planner; keep contingent plans distinct from established facts and avoid duplicating existing topics. Declared topics still require their owner's schema. Notes have a bounded capacity; older freeform topics may be evicted.
{% endif -%}
Follow your pinned RECORDER.md for adventure-specific recording rules. The responder (RESPONDER.md) owns immediate action resolution and narration, the game controller (GAME.md) owns validated Narratron game actions and game-to-story narration gating, and the deep planner (PLANNER.md) owns long-horizon conditional plans and `planner`-owned sticky notes.

# Factual Recording Rules
- Committed events are established facts; unconfirmed player intentions and conditional future plans are not facts.
- Reconcile responder `proposed_factual_changes` (each with `topic`, `info`, and supporting `evidence`) against established sticky notes, lore rules, actual dice results, and committed game receipts. Proposals are claims for verification, not automatic mutations: reject proposals that treat unverified intentions as outcomes or that contradict validated game state.
- Independently audit the entire committed event (player action, narration, dialogue, dice results, `planning_signals`, and game receipts) so required updates to inventory, location, stats, resources, or status are recorded even if omitted from `proposed_factual_changes`.
- Preserve causal continuity and never count a single action or its costs twice.

# Lore and Reference Tools
Use `read_lore` and `search_lore` to verify mechanics, item properties, locations, and state-tracking rules, with up to {{ max_read_lore_calls }} reads and {{ max_search_lore_calls }} searches per run. Prefer established lore over invention.

# Completion
Stage all factual updates for the committed event and every associated game receipt before completing this one logical stage. Return JSON matching GameControlDecision with narration_request=null and an explicit reason. Use reference_updates only to reconcile established canonical player, character, or scene changes; conditional future character or scene developments belong in planner notes. The application publishes staged factual updates together.
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
    """# Recorder Work (record)
Reconcile the committed event against the current snapshot and update only `recorder`-owned sticky notes that changed.

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
    allow_freeform: bool = False,
    max_read_lore_calls: int = MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
    max_search_lore_calls: int = MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
) -> str:
    return _RECORDER_INSTRUCTION_TEMPLATE.render(
        style=style,
        sticky_schema_json=sticky_schema_json,
        lore_context=lore_context,
        allow_freeform=allow_freeform,
        max_read_lore_calls=max_read_lore_calls,
        max_search_lore_calls=max_search_lore_calls,
    ).strip()


def build_recorder_turn_prompt(
    event: RecordingEvent,
    snapshot: NoteSnapshot,
    *,
    owners: dict[str, str] | None = None,
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
        event=event,
        event_payload_json=json.dumps(event.payload, ensure_ascii=False, indent=2),
        snapshot_revision=snapshot.revision,
        current_notes=current_notes,
    ).strip()


ReconcileFacts = Callable[[RecordingEvent, NoteSnapshot], dict[str, str]]


class StoryRecorderModule:
    """Reconcile persisted outcomes and commit recorder-owned sticky notes atomically.

    The injected reconciler receives committed events and a consistent snapshot.
    Its returned changes are independently authorized and validated by Notepad.
    """

    def __init__(
        self,
        notepad: Notepad,
        journal: StoryRecordingJournal,
        reconcile: ReconcileFacts | None = None,
        *,
        lore_library: LoreLibrary | None = None,
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
        self._active_story_response: dict[str, JsonValue] | None = None
        raw_config = notepad.theater.config()
        config = raw_config.get("story_planning", raw_config)
        configured_style = style or str(config.get("style", DEFAULT_STORY_PLANNING_STYLE))
        self.style: str = configured_style.strip() or DEFAULT_STORY_PLANNING_STYLE
        self._read_lore_calls_this_run = 0
        self._search_lore_calls_this_run = 0
        self._lore_calls_lock = Lock()
        self._lock = RLock()
        self.agent: Agent | None = None
        if story_session is not None:
            self.agent = Agent(
                name="story_recorder",
                model=VertexGemini(
                    model=str(config.get("recorder_model") or config.get("responder_model") or "gemini-3.5-flash-lite"),
                    project_id=config.get("vertex_project"),
                    location=str(config.get("vertex_location") or "global"),
                ),
                instruction=self._build_recorder_instruction,
                tools=[
                    self.read_lore,
                    self.search_lore,
                    self.staged_notes.check_schema,
                    self.staged_notes.update_sticky_note,
                    *(
                        [reference_manager.lookup_character, reference_manager.get_player_character]
                        if reference_manager is not None
                        else []
                    ),
                ],
                output_schema=GameControlDecision,
                output_key="story_recorder_output",
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
                generate_content_config=types.GenerateContentConfig(temperature=0.2),
            )
        logger.debug(
            "[StoryRecorderModule] Initialized (has_story_session=%s, has_lore=%s, has_reference_manager=%s)",
            story_session is not None,
            lore_library is not None,
            reference_manager is not None,
        )
        saved = journal.latest_snapshot()
        if saved is not None:
            logger.debug("[StoryRecorderModule] Restoring recorded snapshot revision=%d", saved.revision)
            notepad.restore_recorded_snapshot(saved)

    def _run_model(self, event: RecordingEvent, snapshot: NoteSnapshot) -> GameControlDecision:
        if self.story_session is None:
            raise RuntimeError("A shared story session is required for model recording.")
        logger.debug(
            "[StoryRecorderModule] Running recorder model for event %s (action_id=%s, snapshot_revision=%d)",
            event.event_id,
            event.action_id,
            snapshot.revision,
        )
        self.reset_lore_call_counts()
        self.staged_notes.updates.clear()
        prompt = self._build_recorder_turn_prompt(event, snapshot)
        if self._active_story_response is not None:
            prompt += "\n# Associated Story Response\n" + json.dumps(
                self._active_story_response,
                ensure_ascii=False,
            )
        receipts = self.journal.narratron_receipts(event.event_id)
        if receipts:
            prompt += "\n# Committed Narratron Game Receipts\n" + json.dumps(
                [receipt.model_dump(mode="json") for receipt in receipts],
                ensure_ascii=False,
            )
        raw = self.story_session.run("recorder", prompt, stage_id=event.action_id)
        logger.debug("[StoryRecorderModule] Recorder model returned for event %s: %s", event.event_id, raw)
        decision = GameControlDecision.model_validate_json(raw)
        updates = {**decision.factual_updates, **self.staged_notes.updates}
        for topic, info in updates.items():
            self.notepad.validate_role_update("recorder", topic, info)
        logger.debug(
            "[StoryRecorderModule] Model decision validated for event %s: updates=%s, reference_updates=%d",
            event.event_id,
            list(updates.keys()),
            len(decision.reference_updates),
        )
        return decision.model_copy(update={"factual_updates": updates})

    def _reconcile_model(self, event: RecordingEvent, snapshot: NoteSnapshot) -> dict[str, str]:
        decision = self.journal.recorder_decision(event.event_id)
        if decision is None:
            logger.debug("[StoryRecorderModule] No saved decision for event %s; running recorder model", event.event_id)
            decision = self._run_model(event, snapshot)
            if decision.narration_request is not None:
                logger.warning(
                    "[StoryRecorderModule] Ignoring narration request during factual recording for event %s (kind=%s)",
                    event.event_id,
                    event.kind,
                )
                decision = decision.model_copy(update={"narration_request": None})
            self.journal.save_recorder_decision(event.event_id, decision)
        else:
            logger.debug("[StoryRecorderModule] Reusing saved recorder decision for event %s", event.event_id)
        return decision.factual_updates

    def _apply_reference_updates(self, event_id: str) -> None:
        decision = self.journal.recorder_decision(event_id)
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
                    name=update.name,
                    description=update.description,
                    personality=update.personality,
                    motivation=update.motivation,
                    quirk=update.quirk,
                    gender=update.gender,
                    voice_tags=update.voice_tags,
                )

    def _complete_game_recording(self, event: RecordingEvent) -> tuple[CommittedGameReceipt, ...]:
        """Publish receipts already reconciled by this event's recording invocation."""
        receipts = tuple(self.journal.narratron_receipts(event.event_id))
        if receipts:
            logger.debug(
                "[StoryRecorderModule] Completing %d Narratron game receipt(s) for event %s",
                len(receipts),
                event.event_id,
            )
        for receipt in receipts:
            if self.journal.recorded_revision(receipt.event_id) is None:
                logger.debug(
                    "[StoryRecorderModule] Committing receipt %s (rev=%d) for event %s",
                    receipt.event_id,
                    self.notepad.revision,
                    event.event_id,
                )
                self.journal.commit(receipt.event_id, self.notepad.snapshot())
        if self.journal.game_control_decision(event.event_id) is not None:
            logger.debug("[StoryRecorderModule] Completing game control decision for event %s", event.event_id)
            self.journal.complete_game_control(event.event_id)
        return receipts

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
        result = self.lore_library.read_lore(document, role="recorder")
        self.journal.record_lore_read("recorder", document, result)
        return result

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
            allow_freeform=not self.notepad.enforce_structured,
        )

    def _build_recorder_turn_prompt(
        self,
        event: RecordingEvent,
        snapshot: NoteSnapshot,
    ) -> str:
        owners = {
            topic: str(definition.get("owner") or "unknown")
            for topic, definition in self.notepad.sticky_definitions.items()
        }
        return build_recorder_turn_prompt(
            event,
            snapshot,
            owners=owners,
        )

    def record(
        self,
        event: RecordingEvent,
        *,
        story_response: dict[str, JsonValue] | None = None,
    ) -> int:
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
                self._complete_game_recording(event)
                self._apply_reference_updates(event.event_id)
                return recorded
            pending = self.journal.pending()
            if not pending or pending[0].event_id != event.event_id:
                logger.error(
                    "[StoryRecorderModule] Cannot record %s: oldest pending event is %s (pending_count=%d)",
                    event.event_id,
                    pending[0].event_id if pending else None,
                    len(pending),
                )
                raise ValueError("An earlier event still requires recording.")
            source = self.notepad.snapshot()
            logger.debug(
                "[StoryRecorderModule] Reconciling event %s against snapshot revision %d",
                event.event_id,
                source.revision,
            )
            self._active_story_response = story_response
            try:
                updates = self._reconcile(event, source)
            finally:
                self._active_story_response = None
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
            self._complete_game_recording(event)
            self._apply_reference_updates(event.event_id)
            return revision

    def retry_pending(self) -> None:
        """Drain in causal queue order, stopping on the first unresolved failure."""
        pending = self.journal.pending()
        logger.debug("[StoryRecorderModule] retry_pending() called with %d pending event(s)", len(pending))
        for event in pending:
            self.record(event)
