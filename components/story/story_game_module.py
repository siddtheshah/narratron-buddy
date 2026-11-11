"""Single-pass game-control module for Narratron game actions and narration gating."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import logging
from threading import Lock, RLock
import time
from typing import Literal

from jinja2 import Template
from pydantic import JsonValue, TypeAdapter

from components.lore_library import LoreLibrary, ROLE_DOCUMENTS
from components.notepad import Notepad, NoteSnapshot
from components.story.story_handoffs import (
    CommittedGameReceipt,
    GameControlDecision,
    GameControlOutcome,
    NarrationRequest,
)
from components.story.story_models import DEFAULT_STORY_PLANNING_STYLE
from components.story.story_recording_journal import (
    DEFAULT_HISTORY_EVENT_LIMIT,
    RecordingEvent,
    StoryRecordingJournal,
    default_history_chunk_size,
)
from game_engine.game_runtime import GameAction
from game_engine.game_state import GameState
from providers.decision_provider import (
    ChoiceQuestion,
    DecisionProvider,
    DecisionQuestion,
    DecisionRequest,
    PredicateQuestion,
    ScoreQuestion,
)
from providers.text_response_provider import TextResponseProvider, TextResponseRequest

logger = logging.getLogger(__name__)

MAX_GAME_READ_LORE_CALLS_PER_RUN = 4
MAX_GAME_SEARCH_LORE_CALLS_PER_RUN = 4
GAME_ACTION_TIMEOUT_SECONDS = 35.0
# Single-pass decisions whose Narratron actions the runtime rejects are re-prompted with
# the rejection; a decision is persisted only once its actions commit (or it proposes none).
MAX_GAME_DECISION_ATTEMPTS = 3

GamePhase = Literal["control_game", "apply_story_response"]

_GAME_INSTRUCTION_TEMPLATE = Template(
    """# Role & Mission
You are the single-pass game controller (`story_game`) for an interactive adventure. You do not narrate the current scene, speak for NPCs, roll dice, write sticky notes, or invent conditional future plans.

You own validated Narratron game actions and game-to-story narration gating. Follow your pinned GAME.md for adventure-specific game rules, opponent strategy, and narration thresholds. The responder (RESPONDER.md) owns immediate action resolution and narration, the factual recorder (RECORDER.md) owns `recorder`-owned sticky notes and canonical references, and the deep planner (PLANNER.md) owns long-horizon conditional plans and `planner`-owned sticky notes.

# Game Control Rules
- Both completed story responses (`apply_story_response`) and root committed player game actions (`control_game`) permit validated Narratron game actions during game control. Narratron game receipts are evidence to record and never open another action stage.
- Let the game's action contracts and guards determine prerequisites, including required player input, consent, or turn order. A story response does not waive those prerequisites or authorize acting for the player.
- Read the Current Game State's `instructions`, `state`, `revision`, and listed Narratron `actions` before deciding whether to act. The game runtime validates the complete underlying state and every submitted action.
- Return any justified Narratron actions in `game_actions` (each with `name`, `arguments`, and the game's integer `revision` from Current Game State, advancing `revision` by 1 for each subsequent chained action).
- Only root player game-entry turns (`control_game`) can request narration. When applying a completed story response (`apply_story_response`), return any justified `game_actions` and set `narration_request` to null. For significant game-entry developments, include a concise narrative brief containing only established outcomes, consequences, and narration cues. Exclude raw game state, receipts, revisions, action schemas, and mechanical instructions. Routine mechanical updates finish with `narration_request` set to null. The orchestrator schedules narration and recording; you cannot trigger another role directly.

# Completion
Respond ONLY with valid JSON matching GameControlDecision, including `game_actions`, an explicit `reason`, and `narration_request` (null to skip). A requested brief contains established narrative facts only. On story-response game turns (`apply_story_response`), `narration_request` must be null.
{% if style -%}

# Story-Planning Style
{{ style }}
{% endif -%}
{% if lore_context -%}

# Available Theater Lore
Available theater lore (top-level documents and directories):
{{ lore_context }}
{% endif -%}
"""
)

_GAME_TURN_PROMPT_TEMPLATE = Template(
    """# Game Work ({{ phase }})
Evaluate the committed event against the current game state and sticky notes, specify any justified Narratron game actions in `game_actions`, and return your GameControlDecision.
{% if game_state_json -%}
# Current Game State and Narratron Actions
{{ game_state_json }}
{% endif -%}
{% if relevant_lore -%}

# Relevant Lore Context
{{ relevant_lore }}
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


def build_game_instructions(
    *,
    style: str = "",
    lore_context: str = "",
    max_read_lore_calls: int = MAX_GAME_READ_LORE_CALLS_PER_RUN,
    max_search_lore_calls: int = MAX_GAME_SEARCH_LORE_CALLS_PER_RUN,
) -> str:
    return _GAME_INSTRUCTION_TEMPLATE.render(
        style=style,
        lore_context=lore_context,
        max_read_lore_calls=max_read_lore_calls,
        max_search_lore_calls=max_search_lore_calls,
    ).strip()


def build_game_turn_prompt(
    event: RecordingEvent,
    snapshot: NoteSnapshot,
    *,
    owners: dict[str, str] | None = None,
    phase: GamePhase = "control_game",
    game_state_json: str = "",
    relevant_lore: str = "",
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
    return _GAME_TURN_PROMPT_TEMPLATE.render(
        phase=phase,
        event=event,
        event_payload_json=json.dumps(event.payload, ensure_ascii=False, indent=2),
        snapshot_revision=snapshot.revision,
        current_notes=current_notes,
        game_state_json=game_state_json,
        relevant_lore=relevant_lore,
    ).strip()


def _build_game_control_json_schema(
    game_snapshot: dict[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    """Build a JSON Schema constraining game_actions to the active Narratron contracts."""
    if game_snapshot is None:
        return None
    raw_actions = game_snapshot.get("actions")
    if type(raw_actions) is not dict:
        return None
    action_variants: list[JsonValue] = []
    for name, contract in raw_actions.items():
        if type(name) is not str or type(contract) is not dict:
            return None
        parameters = contract.get("parameters")
        if type(parameters) is not dict:
            return None
        action_variants.append(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": [name]},
                    "arguments": parameters,
                    "revision": {"type": "integer", "minimum": 0},
                },
                "required": ["name", "arguments", "revision"],
                "additionalProperties": False,
            }
        )
    if len(action_variants) == 1:
        game_actions_schema: dict[str, JsonValue] = {
            "type": "array",
            "items": action_variants[0],
        }
    elif action_variants:
        game_actions_schema = {
            "type": "array",
            "items": {"anyOf": action_variants},
        }
    else:
        game_actions_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "revision": {"type": "integer", "minimum": 0},
                },
                "required": ["name", "revision"],
                "additionalProperties": False,
            },
            "maxItems": 0,
        }
    narration_schema = TypeAdapter(dict[str, JsonValue]).validate_python(
        NarrationRequest.model_json_schema()
    )
    return {
        "type": "object",
        "properties": {
            "game_actions": game_actions_schema,
            "narration_request": {
                "anyOf": [narration_schema, {"type": "null"}],
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000,
            },
        },
        "required": ["game_actions", "narration_request", "reason"],
        "additionalProperties": False,
    }


SubmitGameAction = Callable[[GameAction], dict[str, JsonValue]]
ControlGame = Callable[
    [RecordingEvent, NoteSnapshot, SubmitGameAction],
    GameControlDecision | None,
]


class StoryGameModule:
    """Single-pass module encapsulating Narratron game actions and narration gating."""

    def __init__(
        self,
        notepad: Notepad,
        journal: StoryRecordingJournal,
        *,
        lore_library: LoreLibrary | None = None,
        game_state: GameState | None = None,
        text_response_provider: TextResponseProvider | None = None,
        control_game: ControlGame | None = None,
        decision_provider: DecisionProvider | None = None,
        style: str = "",
    ) -> None:
        self.notepad = notepad
        self.journal = journal
        self.lore_library = lore_library
        self.text_response_provider = text_response_provider
        self._game_state = game_state
        self._control_game = control_game
        self._decision_provider = decision_provider
        raw_config = notepad.theater.config() or {}
        config = raw_config.get("story_planning", raw_config)
        self.config = config
        configured_style = style or str(config.get("style", DEFAULT_STORY_PLANNING_STYLE))
        self.style: str = configured_style.strip() or DEFAULT_STORY_PLANNING_STYLE
        self.game_model: str | None = (
            config.get("game_model")
            or config.get("responder_model")
            or config.get("recorder_model")
            or config.get("model")
        )
        self.game_action_timeout_seconds: float = GAME_ACTION_TIMEOUT_SECONDS
        self._game_stage_id: str = ""
        self._read_lore_calls_this_run = 0
        self._search_lore_calls_this_run = 0
        self._lore_calls_lock = Lock()
        self._lock = RLock()
        self._submit_game_action: SubmitGameAction | None = None
        logger.debug(
            "[StoryGameModule] Initialized (has_text_provider=%s, has_lore=%s, has_game_state=%s)",
            text_response_provider is not None,
            lore_library is not None,
            game_state is not None,
        )

    @property
    def game_state(self) -> GameState | None:
        return self._game_state

    @game_state.setter
    def game_state(self, value: GameState | None) -> None:
        self._game_state = value

    @property
    def decision_provider(self) -> DecisionProvider | None:
        return self._decision_provider

    @decision_provider.setter
    def decision_provider(self, value: DecisionProvider | None) -> None:
        self._decision_provider = value

    def take_game_action(self, name: str, arguments_json: str, revision: int) -> str:
        """Execute a validated Narratron action during an active game-control stage.
        The `revision` argument must be the integer revision from Current Game State (not the story snapshot revision).
        """
        logger.debug(
            "[StoryGameModule] take_game_action called: name=%s, revision=%d, arguments=%s",
            name,
            revision,
            arguments_json,
        )
        if self._submit_game_action is None:
            logger.warning(
                "[StoryGameModule] take_game_action rejected: no active story-response or game-entry stage"
            )
            return "Error: Game actions require an active story-response or game-entry stage."
        try:
            action = GameAction(
                name=name,
                arguments=TypeAdapter(dict[str, JsonValue]).validate_json(arguments_json),
                revision=revision,
            )
            result = json.dumps(self._submit_game_action(action), ensure_ascii=False)
            logger.debug("[StoryGameModule] take_game_action %s succeeded: %s", name, result)
            return result
        except ValueError as exc:
            logger.warning("[StoryGameModule] take_game_action %s failed: %s", name, exc)
            return f"Error: {exc}"

    def recover_game_receipts(self, event: RecordingEvent) -> None:
        """Recover actions persisted before an interrupted journal write."""
        if self._game_state is None:
            return
        commits = self._game_state.story_action_commits(f"{event.event_id}:narratron:")
        if commits:
            logger.debug(
                "[StoryGameModule] Recording %d existing Narratron commit(s) from game state for event %s",
                len(commits),
                event.event_id,
            )
        for receipt_id, action, receipt in commits:
            self.journal.record_narratron_receipt(
                CommittedGameReceipt(
                    event_id=receipt_id,
                    parent_event_id=event.event_id,
                    action_id=event.action_id,
                    source_revision=event.source_revision,
                    action=action,
                    receipt=receipt,
                )
            )

    def _commit_game_action(self, event: RecordingEvent, action: GameAction) -> dict[str, JsonValue]:
        logger.debug(
            "[StoryGameModule] Committing Narratron game action %s (revision=%d) for event %s",
            action.name,
            action.revision,
            event.event_id,
        )
        existing = self.journal.narratron_receipt_for_revision(event.event_id, action.revision)
        if existing is not None:
            if existing.action != action:
                raise ValueError("Game revision already has a different committed Narratron action.")
            logger.debug(
                "[StoryGameModule] Reusing existing Narratron receipt for event %s at revision %d",
                event.event_id,
                action.revision,
            )
            return existing.receipt
        if self._game_state is None:
            raise ValueError("No game state is configured for Narratron game actions.")
        receipt = self._game_state.commit_story_action(
            action, f"{event.event_id}:narratron:{action.revision}"
        )
        committed = CommittedGameReceipt(
            event_id=f"{event.event_id}:narratron:{action.revision}",
            parent_event_id=event.event_id,
            action_id=event.action_id,
            source_revision=event.source_revision,
            action=action,
            receipt=receipt,
        )
        self.journal.record_narratron_receipt(committed)
        logger.debug(
            "[StoryGameModule] Narratron game action %s committed at revision %d for event %s",
            action.name,
            action.revision,
            event.event_id,
        )
        return receipt

    def _submit_standalone_game_action(
        self, event: RecordingEvent, action: GameAction, source_revision: int
    ) -> dict[str, JsonValue]:
        existing = self.journal.narratron_receipt_for_revision(event.event_id, action.revision)
        if existing is not None:
            if existing.action != action:
                raise ValueError("Game revision already has a different committed Narratron action.")
            return existing.receipt
        if self._game_state is None:
            raise ValueError("No game state is configured for Narratron game actions.")
        self._game_state.act(action, actor="narratron")
        snapshot = self._game_state.story_snapshot("responder")
        if snapshot is None:
            raise ValueError("Game state did not return a committed receipt.")
        committed = CommittedGameReceipt(
            event_id=f"{event.event_id}:narratron:{action.revision}",
            parent_event_id=event.event_id,
            action_id=event.action_id,
            source_revision=source_revision,
            action=action,
            receipt=snapshot,
        )
        self.journal.record_narratron_receipt(committed)
        return snapshot

    def collect_relevant_lore(
        self,
        event: RecordingEvent,
        sticky_snapshot: NoteSnapshot | None = None,
        contextual_documents: frozenset[str] = frozenset(),
    ) -> str:
        """Select up to two relevant shared lore documents in one decision pass."""
        if self._decision_provider is None or self.lore_library is None:
            return ""
        visible_docs = self.lore_library._visible_documents("game")
        candidate_docs = [
            doc for doc in visible_docs
            if not doc.endswith("/") and doc != ROLE_DOCUMENTS["game"]
        ]
        if not candidate_docs:
            return ""

        active_snapshot = sticky_snapshot if sticky_snapshot is not None else self.notepad.snapshot()
        elements = active_snapshot.present_notes(self.notepad.max_sticky_notes)
        sticky_lines = [f"- {elem.get('topic', '')}: {elem.get('info', '')}" for elem in elements]
        input_parts = [
            f"Committed Event ({event.kind}):\n{json.dumps(event.payload, ensure_ascii=False)}",
            "Active Sticky Notes:\n" + ("\n".join(sticky_lines) if sticky_lines else "(No active sticky notes)"),
        ]
        pruned_candidates = candidate_docs[:254]
        choices = ("none", *pruned_candidates)
        questions: list[DecisionQuestion] = [
            ChoiceQuestion(
                name="primary_lore_document",
                instructions=(
                    "Select the single lore document most relevant to evaluating this game event "
                    "given the current sticky notes state, or 'none' if no lore document is needed."
                ),
                choices=choices,
            ),
        ]
        if len(pruned_candidates) > 1:
            questions.append(
                ChoiceQuestion(
                    name="secondary_lore_document",
                    instructions=(
                        "Select an additional relevant lore document if the game event "
                        "touches a second distinct subject, or 'none' if no additional lore is needed."
                    ),
                    choices=choices,
                )
            )
        request = DecisionRequest(input_text="\n\n".join(input_parts), questions=tuple(questions))
        result = self._decision_provider.decide(request)

        selected_docs: list[str] = []
        primary = result.get_choice("primary_lore_document").choice.strip()
        if primary != "none" and primary in candidate_docs:
            selected_docs.append(primary)
        if len(pruned_candidates) > 1:
            secondary = result.get_choice("secondary_lore_document").choice.strip()
            if secondary != "none" and secondary in candidate_docs and secondary not in selected_docs:
                selected_docs.append(secondary)

        if not selected_docs:
            return ""

        sections: list[str] = []
        for doc_path in selected_docs:
            if doc_path in contextual_documents:
                continue
            content = self.read_lore(doc_path)
            if content and not content.startswith("Error:"):
                sections.append(f"## Lore Document: {doc_path}\n{content.strip()}")
        return "\n\n".join(sections)

    def _run_model(
        self,
        event: RecordingEvent,
        snapshot: NoteSnapshot,
        *,
        phase: GamePhase,
    ) -> GameControlDecision:
        if self.text_response_provider is None:
            raise RuntimeError("A text_response_provider is required for single-pass game control.")
        started = time.perf_counter()
        self.reset_lore_call_counts()
        self._game_stage_id = event.action_id
        event_limit = TypeAdapter(int).validate_python(
            self.config.get("history_event_limit", DEFAULT_HISTORY_EVENT_LIMIT),
        )
        configured_chunk_size = self.config.get("history_chunk_size")
        chunk_size = (
            default_history_chunk_size(event_limit)
            if configured_chunk_size is None
            else TypeAdapter(int).validate_python(configured_chunk_size)
        )
        history, contextual_documents = self.journal.prompt_context_snapshot(
            "game", event_limit, chunk_size,
        )
        relevant_lore = self.collect_relevant_lore(
            event, sticky_snapshot=snapshot, contextual_documents=contextual_documents,
        )
        system_instruction = self._build_game_instruction()
        rejections: list[str] = []
        for attempt in range(1, MAX_GAME_DECISION_ATTEMPTS + 1):
            # Rebuild each attempt: a partially committed chain advances the game revision.
            game_snapshot = (
                self._game_state.story_snapshot("narratron")
                if self._game_state is not None and self._game_state.program is not None
                else None
            )
            prompt = self._build_game_turn_prompt(
                event, snapshot, phase=phase, relevant_lore=relevant_lore,
                game_snapshot=game_snapshot,
            )
            prompt += "\n# Already committed Narratron receipts (do not repeat)\n" + json.dumps(
                [
                    receipt.model_dump(mode="json")
                    for receipt in self.journal.narratron_receipts(event.event_id)
                ],
                ensure_ascii=False,
            )
            if rejections:
                prompt += (
                    "\n\n# Rejected Narratron game actions\n"
                    "The game runtime rejected the following proposed actions and did not apply them. "
                    "The Current Game State above is authoritative. Choose valid listed Narratron actions "
                    "at its current revision, or return no `game_actions` if none is justified.\n"
                    + "\n".join(rejections)
                )
            if history:
                prompt = f"{history}\n\n{prompt}"

            response_json_schema = _build_game_control_json_schema(game_snapshot)
            request = TextResponseRequest(
                prompt=prompt,
                system_instruction=system_instruction,
                model=self.game_model,
                temperature=0.2,
                response_schema=None if response_json_schema is not None else GameControlDecision,
                response_json_schema=response_json_schema,
            )
            decision = self._generate_decision(request, phase=phase, started=started)
            rejection = self._commit_decision_actions(event, decision)
            if rejection is None:
                return decision
            rejections.append(f"- Attempt {attempt}: {rejection}")
        raise ValueError(
            f"Narratron game actions were rejected after {MAX_GAME_DECISION_ATTEMPTS} attempts for event "
            f"{event.event_id}:\n" + "\n".join(rejections)
        )

    def _generate_decision(
        self, request: TextResponseRequest, *, phase: GamePhase, started: float,
    ) -> GameControlDecision:
        assert self.text_response_provider is not None
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-game")
        pending = executor.submit(self.text_response_provider.generate, request)
        try:
            response = pending.result(timeout=self.game_action_timeout_seconds)
        except (FutureTimeoutError, TimeoutError) as exc:
            logger.warning(
                "[StoryGameModule] Generation timed out after %.3fs",
                time.perf_counter() - started,
            )
            raise RuntimeError(
                f"Story game module timed out after {self.game_action_timeout_seconds} seconds."
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if phase == "control_game" and response.parsed is None:
            fields = TypeAdapter(dict[str, JsonValue]).validate_json(response.text)
            if "reason" not in fields or "narration_request" not in fields:
                raise ValueError("Game control must return an explicit narration decision and reason.")
        return (
            GameControlDecision.model_validate(response.parsed)
            if response.parsed is not None
            else GameControlDecision.model_validate_json(response.text)
        )

    def _commit_decision_actions(self, event: RecordingEvent, decision: GameControlDecision) -> str | None:
        """Commit a chained action list, returning the first rejection for corrective feedback.

        Later actions in a chain depend on earlier revisions, so a rejection stops the chain.
        """
        for index, action in enumerate(decision.game_actions):
            try:
                self._commit_game_action(event, action)
            except ValueError as exc:
                logger.warning(
                    "[StoryGameModule] Rejected invalid game action %s (args=%s) (revision=%d) for event %s: %s",
                    action.name,
                    action.arguments,
                    action.revision,
                    event.event_id,
                    exc,
                )
                skipped = len(decision.game_actions) - index - 1
                return (
                    f"{action.model_dump_json(include={'name', 'arguments', 'revision'})} -> {exc}"
                    + (f" ({skipped} dependent action(s) not attempted)" if skipped else "")
                )
        return None

    def should_apply_story_response(self, event: RecordingEvent) -> bool:
        """Analyze current game state, story response, and game rules to decide if a game action is warranted."""
        if event.kind != "completed_response":
            raise ValueError("Only completed story responses can be evaluated for game impact.")
        if self._game_state is None or self._game_state.program is None:
            return False
        game_snapshot = self._game_state.story_snapshot("narratron")
        if game_snapshot is None:
            return False
        if self._decision_provider is None:
            return True
        lore_context = (
            self.lore_library.get_lore_context(role="game")
            if self.lore_library is not None
            else ""
        )
        input_sections = [
            f"Current Game State:\n{json.dumps(game_snapshot, ensure_ascii=False)}",
            f"Story Response:\n{json.dumps(event.payload, ensure_ascii=False)}",
        ]
        if lore_context:
            input_sections.append(f"Game Rules (GAME.md):\n{lore_context}")
        request = DecisionRequest(
            input_text="\n\n".join(input_sections),
            questions=(
                PredicateQuestion(
                    name="should_take_game_action",
                    instructions=(
                        "Analyze the current game state, the story response, and the game rules. "
                        "Is a Narratron game action justified in response to this story outcome?"
                    ),
                ),
            ),
        )
        result = self._decision_provider.decide(request)
        return result.get_predicate("should_take_game_action").passed(0.50)

    def control_story_response(self, event: RecordingEvent) -> GameControlOutcome:
        """Execute warranted Narratron game actions in response to a completed story response."""
        if event.kind != "completed_response":
            raise ValueError("Only completed story responses can enter control_story_response.")
        with self._lock:
            self.journal.enqueue(event)
            if self.text_response_provider is not None:
                self.recover_game_receipts(event)
            decision = self.journal.game_control_decision(event.event_id)
            if decision is None:
                if self.text_response_provider is not None:
                    def submit(action: GameAction) -> dict[str, JsonValue]:
                        return self._commit_game_action(event, action)

                    self._submit_game_action = submit
                    try:
                        decision = self._run_model(
                            event, self.notepad.snapshot(), phase="apply_story_response"
                        )
                    finally:
                        self._submit_game_action = None
                else:
                    def submit_game_action(action: GameAction) -> dict[str, JsonValue]:
                        return self._submit_standalone_game_action(
                            event, action, self.notepad.revision
                        )

                    raw_decision = (
                        self._control_game(event, self.notepad.snapshot(), submit_game_action)
                        if self._control_game is not None
                        else None
                    )
                    decision = raw_decision if raw_decision is not None else GameControlDecision()
                if decision.narration_request is not None:
                    logger.warning(
                        "[StoryGameModule] Ignoring narration request during story-response game control for event %s",
                        event.event_id,
                    )
                    decision = decision.model_copy(update={"narration_request": None})
                self.journal.save_game_control_decision(event.event_id, decision)
            receipts = tuple(self.journal.narratron_receipts(event.event_id))
            revision = self.notepad.revision
            return GameControlOutcome(
                event_id=event.event_id,
                player_recorded_revision=revision,
                narratron_receipts=receipts,
                final_recorded_revision=revision,
                narration_request=None,
                narration_triggered=False,
            )

    def control_game(self, event: RecordingEvent) -> GameControlOutcome:
        """Evaluate a player game move in one pass, execute Narratron counter-actions, and gate narration."""
        logger.debug(
            "[StoryGameModule] control_game() called for event %s (action_id=%s, kind=%s)",
            event.event_id,
            event.action_id,
            event.kind,
        )
        if event.kind != "committed_game_action":
            raise ValueError("Completed responses cannot enter game control.")
        if event.source_event_ids or event.payload.get("actor") == "narratron":
            raise ValueError("Narratron game receipts cannot recursively enter game control.")
        with self._lock:
            self.journal.enqueue(event)
            if self.text_response_provider is not None:
                self.recover_game_receipts(event)
            decision = self.journal.game_control_decision(event.event_id)
            if decision is None:
                if self.text_response_provider is not None:
                    def submit(action: GameAction) -> dict[str, JsonValue]:
                        return self._commit_game_action(event, action)

                    self._submit_game_action = submit
                    try:
                        decision = self._run_model(
                            event, self.notepad.snapshot(), phase="control_game"
                        )
                    finally:
                        self._submit_game_action = None
                    if decision.narration_request is not None and not decision.narration_request.brief:
                        raise ValueError("Narration requires a concise game narrative brief.")
                    self.journal.save_game_control_decision(event.event_id, decision)
                elif self._decision_provider is not None:
                    input_text = (
                        str(
                            event.payload.get("summary")
                            or event.payload.get("action")
                            or event.payload.get("name")
                            or json.dumps(event.payload, ensure_ascii=False)
                        ).strip()
                        or "Game move committed"
                    )
                    request = DecisionRequest(
                        input_text=input_text,
                        questions=(
                            PredicateQuestion(
                                name="should_narrate_move",
                                instructions=(
                                    "Is this game move a dramatic capture, blunder, "
                                    "kinging, or momentum swing worth immediate in-character narration?"
                                ),
                            ),
                            ScoreQuestion(
                                name="drama_level",
                                instructions="Rate the narrative significance of this board-game move.",
                                levels=("routine", "notable", "turning_point", "climactic"),
                            ),
                            ChoiceQuestion(
                                name="narrator_reaction_tone",
                                instructions="How should Narratron react in character to this player move?",
                                choices=("unbothered", "impressed", "rattled", "triumphant"),
                            ),
                        ),
                    )
                    result = self._decision_provider.decide(request)
                    narrate = result.get_predicate("should_narrate_move")
                    drama = result.get_score("drama_level")
                    tone = result.get_choice("narrator_reaction_tone")
                    wake_responder = narrate.passed(0.60) or drama.score >= 2.0
                    if wake_responder:
                        narration_request = NarrationRequest(
                            user_action=str(
                                event.payload.get("action")
                                or event.payload.get("name")
                                or event.payload.get("summary")
                                or "Game move committed"
                            ),
                            nudge=f"React in character with a {tone.choice} tone.",
                            brief=f"Dramatic game move: drama={drama.score:.2f}, tone={tone.choice}.",
                        )
                        decision = GameControlDecision(
                            narration_request=narration_request,
                            reason=(
                                f"Move exceeded drama threshold: drama={drama.score:.2f}, "
                                f"should_narrate={narrate.probability:.0%}, tone={tone.choice}."
                            ),
                        )
                    else:
                        decision = GameControlDecision(
                            narration_request=None,
                            reason="Routine move below narration threshold.",
                        )
                    self.journal.save_game_control_decision(event.event_id, decision)
                else:
                    def submit_game_action(action: GameAction) -> dict[str, JsonValue]:
                        return self._submit_standalone_game_action(
                            event, action, self.notepad.revision
                        )

                    raw_decision = (
                        self._control_game(event, self.notepad.snapshot(), submit_game_action)
                        if self._control_game is not None
                        else None
                    )
                    decision = raw_decision if raw_decision is not None else GameControlDecision()
                    self.journal.save_game_control_decision(event.event_id, decision)

            receipts = tuple(self.journal.narratron_receipts(event.event_id))
            revision = self.notepad.revision
            return GameControlOutcome(
                event_id=event.event_id,
                player_recorded_revision=revision,
                narratron_receipts=receipts,
                final_recorded_revision=revision,
                narration_request=decision.narration_request,
                narration_triggered=(
                    (decision.narration_request is not None)
                    if (self.text_response_provider is None and self._decision_provider is not None)
                    else False
                ),
            )

    def read_lore(self, document: str = "") -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story game module."
        with self._lore_calls_lock:
            if self._read_lore_calls_this_run >= MAX_GAME_READ_LORE_CALLS_PER_RUN:
                logger.warning(
                    "[StoryGameModule] read_lore limit reached (%d/%d)",
                    self._read_lore_calls_this_run,
                    MAX_GAME_READ_LORE_CALLS_PER_RUN,
                )
                return (
                    f"Error: Maximum game read_lore limit "
                    f"({MAX_GAME_READ_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize game control now."
                )
            self._read_lore_calls_this_run += 1
            call_count = self._read_lore_calls_this_run
        logger.debug(
            "[StoryGameModule] read_lore called (%d/%d): document=%r",
            call_count,
            MAX_GAME_READ_LORE_CALLS_PER_RUN,
            document,
        )
        result = self.lore_library.read_lore(document, role="game")
        self.journal.record_lore_read("game", document, result)
        return result

    def search_lore(self, query: str) -> str:
        if self.lore_library is None:
            return "No lore library is configured for the story game module."
        with self._lore_calls_lock:
            if self._search_lore_calls_this_run >= MAX_GAME_SEARCH_LORE_CALLS_PER_RUN:
                logger.warning(
                    "[StoryGameModule] search_lore limit reached (%d/%d)",
                    self._search_lore_calls_this_run,
                    MAX_GAME_SEARCH_LORE_CALLS_PER_RUN,
                )
                return (
                    f"Error: Maximum game search_lore limit "
                    f"({MAX_GAME_SEARCH_LORE_CALLS_PER_RUN}) reached. "
                    "Finalize game control now."
                )
            self._search_lore_calls_this_run += 1
            call_count = self._search_lore_calls_this_run
        logger.debug(
            "[StoryGameModule] search_lore called (%d/%d): query=%r",
            call_count,
            MAX_GAME_SEARCH_LORE_CALLS_PER_RUN,
            query,
        )
        return self.lore_library.search_lore(query)

    def reset_lore_call_counts(self) -> None:
        with self._lore_calls_lock:
            self._read_lore_calls_this_run = 0
            self._search_lore_calls_this_run = 0
        logger.debug("[StoryGameModule] Reset lore call counts")

    def _build_game_instruction(self) -> str:
        lore_context = (
            self.lore_library.get_lore_context(role="game")
            if self.lore_library is not None
            else ""
        )
        return build_game_instructions(
            style=self.style,
            lore_context=lore_context,
        )

    def _build_game_turn_prompt(
        self,
        event: RecordingEvent,
        snapshot: NoteSnapshot,
        *,
        phase: GamePhase = "control_game",
        relevant_lore: str = "",
        game_snapshot: dict[str, JsonValue] | None = None,
    ) -> str:
        owners = {
            topic: str(definition.get("owner") or "unknown")
            for topic, definition in self.notepad.sticky_definitions.items()
        }
        game_state_json = ""
        resolved_snapshot = (
            game_snapshot
            if game_snapshot is not None
            else (
                self._game_state.story_snapshot("narratron")
                if self._game_state is not None and self._game_state.program is not None
                else None
            )
        )
        if resolved_snapshot is not None:
            game_state_json = json.dumps(resolved_snapshot, ensure_ascii=False)
        return build_game_turn_prompt(
            event,
            snapshot,
            owners=owners,
            phase=phase,
            game_state_json=game_state_json,
            relevant_lore=relevant_lore,
        )
