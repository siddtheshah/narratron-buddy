"""Unit tests for StoryGameModule game control and decision-model gating."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from components.canvas_state import CanvasStateManager
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from components.story.story_game_module import (
    MAX_GAME_DECISION_ATTEMPTS,
    MAX_GAME_READ_LORE_CALLS_PER_RUN,
    MAX_GAME_SEARCH_LORE_CALLS_PER_RUN,
    StoryGameModule,
    SubmitGameAction,
)
from components.story.story_handoffs import GameControlDecision, NarrationRequest
from components.story.story_recorder_module import StoryRecorderModule
from components.story.story_recording_journal import RecordingEvent, StoryRecordingJournal
from components.theater_manager import Theater
from game_engine.game_runtime import GameAction
from game_engine.game_state import GameState
from testing.game_fixtures import make_example_canvas
from providers.decision_provider import (
    ChoiceAnswer,
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
    PredicateAnswer,
    ScoreAnswer,
)
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    TextResponseResult,
)


@pytest.fixture
def pad(tmp_path: Path) -> Notepad:
    theater = Mock(spec=Theater)
    theater.config.return_value = {"story_planning": {"adventure_mode": True, "enforce_structured": False}}
    theater.directory.return_value = tmp_path
    theater.read_planning_schema.return_value = {
        "Location": {"owner": "recorder", "initial": "Gate"},
        "Inventory": {"owner": "recorder", "initial": "Key"},
        "Ambush": {"owner": "planner", "initial": "Wait"},
    }
    return Notepad(theater, canvas_manager=Mock(spec=CanvasStateManager))


def response(event_id: str = "response-1") -> RecordingEvent:
    return RecordingEvent(
        event_id=event_id,
        action_id=f"action-{event_id}",
        source_revision=0,
        kind="completed_response",
        payload={"narration": "The key breaks in the courtyard."},
    )


def test_retrying_projection_preserves_narration_handoff_without_peer_triggering(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_state = Mock(spec=GameState)
    game_state.story_action_commits.return_value = []
    game_state.story_snapshot.return_value = {"revision": 2, "state": {"tile": "B2"}}
    player_event = RecordingEvent(
        event_id="game-1",
        action_id="player-1",
        kind="committed_game_action",
        source_revision=0,
        payload={"action": {"name": "step"}, "receipt": {"revision": 1, "state": {"tile": "A2"}}},
    )
    narratron_action = GameAction(name="counter", arguments={"tile": "B2"}, revision=1)
    reconcile = Mock(
        side_effect=[
            RuntimeError("Transient projection failure"),
            {"Location": "Square B2"},
        ]
    )

    def control(
        event: RecordingEvent, snapshot: NoteSnapshot, submit: SubmitGameAction
    ) -> GameControlDecision:
        receipt = submit(narratron_action)
        assert receipt == {"revision": 2, "state": {"tile": "B2"}}
        return GameControlDecision(
            narration_request=NarrationRequest(
                user_action="The warden blocks the corridor at B2.",
                nudge="Describe the counter-move.",
            )
        )

    control_mock = Mock(side_effect=control)
    game_module = StoryGameModule(
        pad,
        journal,
        game_state=game_state,
        control_game=control_mock,
    )
    recorder = StoryRecorderModule(pad, journal, reconcile)
    try:
        outcome = game_module.control_game(player_event)
        assert len(outcome.narratron_receipts) == 1
        assert outcome.narration_request is not None
        assert outcome.narration_triggered is False
        game_state.act.assert_called_once_with(narratron_action, actor="narratron")

        with pytest.raises(RuntimeError, match="Transient projection failure"):
            recorder.record(player_event)
        assert not journal.is_game_control_completed("game-1")

        assert recorder.record(player_event) == 1
        assert pad.snapshot().notes["Location"] == "Square B2"
        assert journal.is_game_control_completed("game-1")
        assert journal.pending() == []
        game_state.act.assert_called_once()
        control_mock.assert_called_once()
        assert reconcile.call_count == 2

        repeated = game_module.control_game(player_event)
        assert repeated.narration_request == outcome.narration_request
        assert repeated.narratron_receipts == outcome.narratron_receipts
        control_mock.assert_called_once()
    finally:
        journal.close()


def test_interrupted_controller_reuses_committed_receipt_and_rejects_conflicting_action(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_state = Mock(spec=GameState)
    game_state.story_action_commits.return_value = []
    game_state.story_snapshot.return_value = {"revision": 2, "state": {"tile": "B2"}}
    player_event = RecordingEvent(
        event_id="game-1",
        action_id="player-1",
        kind="committed_game_action",
        source_revision=0,
        payload={"action": {"name": "step"}, "receipt": {"revision": 1, "state": {"tile": "A2"}}},
    )
    first_action = GameAction(name="counter", arguments={"tile": "B2"}, revision=1)
    conflicting_action = GameAction(name="counter", arguments={"tile": "C3"}, revision=1)
    attempts = 0

    def control(
        event: RecordingEvent, snapshot: NoteSnapshot, submit: SubmitGameAction
    ) -> GameControlDecision:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            submit(first_action)
            raise RuntimeError("Controller interrupted after committing game action")
        if attempts == 2:
            submit(conflicting_action)
            return GameControlDecision()
        assert submit(first_action) == {"revision": 2, "state": {"tile": "B2"}}
        return GameControlDecision()

    game_module = StoryGameModule(
        pad,
        journal,
        game_state=game_state,
        control_game=control,
    )
    recorder = StoryRecorderModule(pad, journal, Mock(return_value={}))
    try:
        with pytest.raises(RuntimeError, match="Controller interrupted"):
            game_module.control_game(player_event)
        game_state.act.assert_called_once_with(first_action, actor="narratron")

        with pytest.raises(ValueError, match="different committed Narratron action"):
            game_module.control_game(player_event)
        game_state.act.assert_called_once()

        outcome = game_module.control_game(player_event)
        assert len(outcome.narratron_receipts) == 1
        assert outcome.narration_triggered is False
        recorder.record(player_event)
        assert journal.pending() == []
        game_state.act.assert_called_once()
    finally:
        journal.close()


def test_completed_response_and_narratron_receipt_cannot_enter_game_control(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(pad, journal)
    narratron_event = RecordingEvent(
        event_id="game-1:narratron:1",
        action_id="player-1",
        source_event_ids=("game-1",),
        kind="committed_game_action",
        source_revision=0,
        payload={"actor": "narratron", "action": {"name": "counter"}, "receipt": {"revision": 2}},
    )
    try:
        with pytest.raises(ValueError, match="Completed responses cannot enter game control"):
            game_module.control_game(response())
        with pytest.raises(ValueError, match="Narratron game receipts cannot recursively"):
            game_module.control_game(narratron_event)
    finally:
        journal.close()


def test_game_instructions_combine_source_template_and_pinned_game_md(
    pad: Notepad, tmp_path: Path
) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "game-test"
    theater.lore_documents.return_value = [
        "GAME.md",
        "RECORDER.md",
        "RESPONDER.md",
        "PLANNER.md",
        "rules/checkers.txt",
    ]
    contents = {
        "GAME.md": "GAME PRIORITY: play Ivory pieces and narrate double jumps.",
        "RECORDER.md": "RECORDER PRIORITY: verify brass key usage against archive rules.",
        "RESPONDER.md": "RESPONDER PRIORITY: narrate sensory details.",
        "PLANNER.md": "PLANNER SECRET: archivist counter-plan.",
        "rules/checkers.txt": "Mandatory jumps apply to both players.",
    }
    theater.read_lore_document.side_effect = contents.__getitem__
    lore_library = LoreLibrary(theater)
    game_state = Mock(spec=GameState)
    game_state.program = Mock()
    game_state.story_snapshot.return_value = {
        "revision": 2,
        "state": {"turn": "narratron"},
        "instructions": "Advance the patrol when the bell rings.",
        "actions": {"advance_patrol": {"description": "Move patrol"}},
    }
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad,
        journal,
        lore_library=lore_library,
        game_state=game_state,
        style="grounded mystery",
    )
    try:
        instruction = game_module._build_game_instruction()
        assert "# Role & Mission" in instruction
        assert "# Game Control Rules" in instruction
        assert "grounded mystery" in instruction
        assert "# Pinned Game Instructions (GAME.md)" in instruction
        assert contents["GAME.md"] in instruction
        assert contents["RECORDER.md"] not in instruction
        assert contents["RESPONDER.md"] not in instruction
        assert contents["PLANNER.md"] not in instruction
        assert "RESPONDER.md" in instruction
        assert "RECORDER.md" in instruction
        assert "private" in game_module.read_lore("RESPONDER.md")
        assert "private" in game_module.read_lore("PLANNER.md")
        assert "private" in game_module.read_lore("RECORDER.md")
        assert contents["GAME.md"] in game_module.read_lore("GAME.md")

        turn_prompt = game_module._build_game_turn_prompt(
            response(), pad.snapshot(), phase="control_game"
        )
        assert "# Game Work (control_game)" in turn_prompt
        assert "Advance the patrol when the bell rings." in turn_prompt
        assert "- [recorder] Location: Gate" in turn_prompt
        assert "- [planner] Ambush: Wait" in turn_prompt
    finally:
        journal.close()


def test_game_lore_budgets_and_reset(pad: Notepad, tmp_path: Path) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "game-budget"
    theater.lore_documents.return_value = ["rules.txt"]
    theater.read_lore_document.return_value = " Checkers rules."
    lore_library = LoreLibrary(theater)
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad, journal, lore_library=lore_library,
    )
    try:
        for _ in range(MAX_GAME_READ_LORE_CALLS_PER_RUN):
            assert "Checkers rules." in game_module.read_lore("rules.txt")
        assert "Maximum game read_lore limit" in game_module.read_lore("rules.txt")

        for _ in range(MAX_GAME_SEARCH_LORE_CALLS_PER_RUN):
            assert "rules.txt" in game_module.search_lore("Checkers")
        assert "Maximum game search_lore limit" in game_module.search_lore("Checkers")

        game_module.reset_lore_call_counts()
        assert "Checkers rules." in game_module.read_lore("rules.txt")
        assert "rules.txt" in game_module.search_lore("Checkers")
    finally:
        journal.close()


def test_control_game_with_decision_provider_passes_predicate(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    mock_provider = Mock(spec=DecisionProvider)
    mock_provider.decide.return_value = DecisionResult(
        provider="mock",
        model="fast-decide",
        answers={
            "should_narrate_move": PredicateAnswer(name="should_narrate_move", probability=0.85),
            "drama_level": ScoreAnswer(name="drama_level", score=1.5),
            "narrator_reaction_tone": ChoiceAnswer(name="narrator_reaction_tone", choice="rattled"),
        },
    )
    game_module = StoryGameModule(
        pad,
        journal,
        decision_provider=mock_provider,
    )
    try:
        assert game_module.decision_provider is mock_provider

        player_event = RecordingEvent(
            event_id="game-1",
            action_id="player-1",
            kind="committed_game_action",
            source_revision=0,
            payload={"summary": "Double jump captures two kings."},
        )
        outcome = game_module.control_game(player_event)
        mock_provider.decide.assert_called_once()
        call_req: DecisionRequest = mock_provider.decide.call_args[0][0]
        assert call_req.input_text == "Double jump captures two kings."
        assert len(call_req.questions) == 3
        assert call_req.questions[0].name == "should_narrate_move"
        assert call_req.questions[1].name == "drama_level"
        assert call_req.questions[2].name == "narrator_reaction_tone"

        assert outcome.narration_triggered is True
        assert outcome.narration_request is not None
        assert outcome.narration_request.user_action == "Double jump captures two kings."
        assert outcome.narration_request.nudge == "React in character with a rattled tone."
        assert outcome.narration_request.brief == "Dramatic game move: drama=1.50, tone=rattled."

        saved_decision = journal.game_control_decision("game-1")
        assert saved_decision is not None
        assert "Move exceeded drama threshold" in saved_decision.reason
        assert "should_narrate=85%" in saved_decision.reason

        repeated = game_module.control_game(player_event)
        assert repeated == outcome
        mock_provider.decide.assert_called_once()
    finally:
        journal.close()


def test_control_game_with_decision_provider_high_drama_level(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    mock_provider = Mock(spec=DecisionProvider)
    mock_provider.decide.return_value = DecisionResult(
        provider="mock",
        model="fast-decide",
        answers={
            "should_narrate_move": PredicateAnswer(name="should_narrate_move", probability=0.40),
            "drama_level": ScoreAnswer(name="drama_level", score=2.5),
            "narrator_reaction_tone": ChoiceAnswer(name="narrator_reaction_tone", choice="impressed"),
        },
    )
    game_module = StoryGameModule(
        pad,
        journal,
        decision_provider=mock_provider,
    )
    try:
        player_event = RecordingEvent(
            event_id="game-2",
            action_id="player-2",
            kind="committed_game_action",
            source_revision=0,
            payload={"action": "Queen sacrifice for checkmate sequence"},
        )
        outcome = game_module.control_game(player_event)
        mock_provider.decide.assert_called_once()
        assert outcome.narration_triggered is True
        assert outcome.narration_request is not None
        assert outcome.narration_request.user_action == "Queen sacrifice for checkmate sequence"
        assert outcome.narration_request.nudge == "React in character with a impressed tone."
        assert outcome.narration_request.brief == "Dramatic game move: drama=2.50, tone=impressed."
    finally:
        journal.close()


def test_control_game_with_decision_provider_below_narration_threshold(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    mock_provider = Mock(spec=DecisionProvider)
    mock_provider.decide.return_value = DecisionResult(
        provider="mock",
        model="fast-decide",
        answers={
            "should_narrate_move": PredicateAnswer(name="should_narrate_move", probability=0.20),
            "drama_level": ScoreAnswer(name="drama_level", score=0.8),
            "narrator_reaction_tone": ChoiceAnswer(name="narrator_reaction_tone", choice="unbothered"),
        },
    )
    game_module = StoryGameModule(
        pad,
        journal,
        decision_provider=mock_provider,
    )
    try:
        player_event = RecordingEvent(
            event_id="game-3",
            action_id="player-3",
            kind="committed_game_action",
            source_revision=0,
            payload={"name": "Pawn moves e2 to e3"},
        )
        outcome = game_module.control_game(player_event)
        mock_provider.decide.assert_called_once()
        assert outcome.narration_triggered is False
        assert outcome.narration_request is None

        saved_decision = journal.game_control_decision("game-3")
        assert saved_decision is not None
        assert saved_decision.narration_request is None
        assert saved_decision.reason == "Routine move below narration threshold."
    finally:
        journal.close()


def test_control_game_without_decision_provider_preserves_existing_behavior(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    custom_decision = GameControlDecision(
        narration_request=NarrationRequest(
            user_action="Manual action",
            nudge="Manual nudge",
            brief="Manual brief",
        ),
        reason="Manual callback reason",
    )
    control_mock = Mock(return_value=custom_decision)
    game_module = StoryGameModule(
        pad,
        journal,
        control_game=control_mock,
        decision_provider=None,
    )
    try:
        assert game_module.decision_provider is None
        player_event = RecordingEvent(
            event_id="game-4",
            action_id="player-4",
            kind="committed_game_action",
            source_revision=0,
            payload={"action": "step"},
        )
        outcome = game_module.control_game(player_event)
        control_mock.assert_called_once()
        assert outcome.narration_triggered is False
        assert outcome.narration_request == custom_decision.narration_request
    finally:
        journal.close()


@pytest.mark.parametrize("probability,expected", [(0.80, True), (0.20, False)])
def test_should_apply_story_response_uses_decision_provider_with_game_state_response_and_rules(
    pad: Notepad, tmp_path: Path, probability: float, expected: bool
) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "game-decision-test"
    theater.lore_documents.return_value = ["GAME.md"]
    theater.read_lore_document.return_value = "Concede when the player solves the riddle."
    lore_library = LoreLibrary(theater)
    game_state = Mock(spec=GameState)
    game_state.program = Mock()
    game_state.story_snapshot.return_value = {
        "revision": 3,
        "state": {"winner": ""},
        "instructions": "Call narratron_concede if the riddle is solved.",
        "actions": [{"name": "narratron_concede"}],
    }
    mock_provider = Mock(spec=DecisionProvider)
    mock_provider.decide.return_value = DecisionResult(
        provider="mock",
        model="fast-decide",
        answers={
            "should_take_game_action": PredicateAnswer(
                name="should_take_game_action", probability=probability
            ),
        },
    )
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad,
        journal,
        lore_library=lore_library,
        game_state=game_state,
        decision_provider=mock_provider,
    )
    try:
        event = RecordingEvent(
            event_id="resp-1",
            action_id="act-1",
            kind="completed_response",
            payload={"narration": "The Keeper bows and concedes the match."},
        )
        assert game_module.should_apply_story_response(event) is expected
        mock_provider.decide.assert_called_once()
        call_req: DecisionRequest = mock_provider.decide.call_args[0][0]
        assert "Current Game State:" in call_req.input_text
        assert "narratron_concede" in call_req.input_text
        assert "The Keeper bows and concedes the match." in call_req.input_text
        assert "Concede when the player solves the riddle." in call_req.input_text
        assert len(call_req.questions) == 1
        assert call_req.questions[0].name == "should_take_game_action"
    finally:
        journal.close()


def test_single_pass_text_response_provider_commits_game_actions_and_preloads_lore(
    pad: Notepad, tmp_path: Path
) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "game-single-pass"
    theater.lore_documents.return_value = ["GAME.md", "rules/checkers.txt"]
    contents = {
        "GAME.md": "Always respond with a legal move on narratron's turn.",
        "rules/checkers.txt": "Mandatory jumps apply to all pieces.",
    }
    theater.read_lore_document.side_effect = contents.__getitem__
    lore_library = LoreLibrary(theater)

    game_state = Mock(spec=GameState)
    game_state.program = Mock()
    game_state.story_action_commits.return_value = []
    game_state.commit_story_action.return_value = {
        "revision": 2,
        "state": {"turn": "player"},
    }
    game_state.story_snapshot.return_value = {
        "revision": 2,
        "state": {"turn": "narratron"},
        "instructions": "Play Ivory pieces.",
        "actions": [{"name": "narratron_move"}],
    }

    text_provider = Mock(spec=TextResponseProvider)
    decision_payload = GameControlDecision(
        game_actions=(
            GameAction(
                name="narratron_move",
                arguments={"from": "c6", "to": "d5"},
                revision=1,
            ),
        ),
        narration_request=NarrationRequest(
            user_action="The Archivist slides an ivory piece to d5.",
            nudge="Describe the quiet click of the piece.",
            brief="Archivist counter-move on d5.",
        ),
        reason="Legal counter-move on d5.",
    )
    text_provider.generate.return_value = TextResponseResult(
        text=decision_payload.model_dump_json(),
        provider="mock",
        model="fast-game",
    )

    decision_provider = Mock(spec=DecisionProvider)
    decision_provider.decide.return_value = DecisionResult(
        provider="mock",
        model="fast-decide",
        answers={
            "primary_lore_document": ChoiceAnswer(
                name="primary_lore_document", choice="rules/checkers.txt"
            ),
            "secondary_lore_document": ChoiceAnswer(
                name="secondary_lore_document", choice="none"
            ),
        },
    )

    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad,
        journal,
        lore_library=lore_library,
        game_state=game_state,
        text_response_provider=text_provider,
        decision_provider=decision_provider,
    )
    try:
        player_event = RecordingEvent(
            event_id="game-single-1",
            action_id="player-1",
            kind="committed_game_action",
            source_revision=0,
            payload={"action": {"name": "player_move"}, "receipt": {"revision": 1}},
        )
        outcome = game_module.control_game(player_event)
        text_provider.generate.assert_called_once()
        req: TextResponseRequest = text_provider.generate.call_args[0][0]
        assert req.response_schema is GameControlDecision
        assert "Mandatory jumps apply to all pieces." in req.prompt
        assert "Always respond with a legal move on narratron's turn." in req.system_instruction
        game_state.commit_story_action.assert_called_once()
        assert len(outcome.narratron_receipts) == 1
        assert outcome.narration_request == decision_payload.narration_request
    finally:
        journal.close()


def _rejecting_game_module(
    pad: Notepad, tmp_path: Path, decisions: list[GameControlDecision], commits: list[dict[str, JsonValue] | Exception],
) -> tuple[StoryGameModule, Mock, Mock, StoryRecordingJournal]:
    game_state = Mock(spec=GameState)
    game_state.program = Mock()
    game_state.story_action_commits.return_value = []
    game_state.commit_story_action.side_effect = commits
    game_state.story_snapshot.return_value = {
        "revision": 1,
        "state": {"turn": "narratron"},
        "instructions": "Narratron must answer every player move.",
        "actions": {"narratron_move": {"description": "Move a narratron piece."}},
    }
    text_provider = Mock(spec=TextResponseProvider)
    text_provider.generate.side_effect = [
        TextResponseResult(text=decision.model_dump_json(), provider="mock", model="fast-game")
        for decision in decisions
    ]
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad, journal, game_state=game_state, text_response_provider=text_provider,
    )
    return game_module, game_state, text_provider, journal


def _player_move(event_id: str) -> RecordingEvent:
    return RecordingEvent(
        event_id=event_id,
        action_id=f"player-{event_id}",
        kind="committed_game_action",
        source_revision=0,
        payload={"action": {"name": "player_move"}, "receipt": {"revision": 1}},
    )


def test_rejected_narratron_action_is_corrected_instead_of_stalling_the_game(
    pad: Notepad, tmp_path: Path,
) -> None:
    illegal = GameAction(name="narratron_move", arguments={"to": "z9"}, revision=1)
    legal = GameAction(name="narratron_move", arguments={"to": "d5"}, revision=1)
    game_module, game_state, text_provider, journal = _rejecting_game_module(
        pad, tmp_path,
        decisions=[
            GameControlDecision(game_actions=(illegal,), reason="Counter on z9."),
            GameControlDecision(game_actions=(legal,), reason="Counter on d5."),
        ],
        commits=[
            ValueError("This action is not permitted in the current game state."),
            {"revision": 2, "state": {"turn": "player"}},
        ],
    )
    try:
        event = _player_move("game-reject-1")
        outcome = game_module.control_game(event)
        assert text_provider.generate.call_count == 2
        retry_prompt: str = text_provider.generate.call_args_list[1][0][0].prompt
        assert "Rejected Narratron game actions" in retry_prompt
        assert "not permitted in the current game state" in retry_prompt
        assert '"arguments":{"to":"z9"}' in retry_prompt
        assert game_state.commit_story_action.call_count == 2
        assert [receipt.action for receipt in outcome.narratron_receipts] == [legal]
        saved = journal.game_control_decision(event.event_id)
        assert saved is not None and saved.game_actions == (legal,)
    finally:
        journal.close()


def test_persistently_rejected_narratron_action_escalates_without_persisting_decision(
    pad: Notepad, tmp_path: Path,
) -> None:
    illegal = GameAction(name="narratron_move", arguments={"to": "z9"}, revision=1)
    rejection = ValueError("This action is not permitted in the current game state.")
    game_module, game_state, text_provider, journal = _rejecting_game_module(
        pad, tmp_path,
        decisions=[GameControlDecision(game_actions=(illegal,))] * MAX_GAME_DECISION_ATTEMPTS,
        commits=[rejection] * MAX_GAME_DECISION_ATTEMPTS,
    )
    try:
        event = _player_move("game-reject-2")
        with pytest.raises(ValueError, match="rejected"):
            game_module.control_game(event)
        assert text_provider.generate.call_count == MAX_GAME_DECISION_ATTEMPTS
        assert journal.game_control_decision(event.event_id) is None
        assert journal.narratron_receipts(event.event_id) == []
    finally:
        journal.close()


def test_example_adventure_game_control_uses_concrete_action_argument_schemas_and_retries_missing_args(
    pad: Notepad, tmp_path: Path,
) -> None:
    canvas = make_example_canvas(tmp_path / "theater")
    game = canvas.game
    assert game is not None
    game.act(
        GameAction(
            name="move",
            arguments={"source": 40, "target": 33},
            revision=0,
            canvas_revision=canvas.canvas_observation().revision,
        ),
        actor="player",
    )
    assert game.revision == 1

    bad_move = GameAction(name="narratron_move", arguments={}, revision=1)
    good_move = GameAction(name="narratron_move", arguments={"source": 17, "target": 26}, revision=1)
    text_provider = Mock(spec=TextResponseProvider)
    text_provider.generate.side_effect = [
        TextResponseResult(
            text=GameControlDecision(game_actions=(bad_move,), reason="Missing args.").model_dump_json(),
            provider="mock",
            model="fast-game",
        ),
        TextResponseResult(
            text=GameControlDecision(game_actions=(good_move,), reason="Ivory b6 to c5.").model_dump_json(),
            provider="mock",
            model="fast-game",
        ),
    ]
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad, journal, game_state=game, text_response_provider=text_provider,
    )
    try:
        player_event = RecordingEvent(
            event_id="player:checkers:1:recording",
            action_id="player:checkers:1",
            kind="committed_game_action",
            source_revision=0,
            payload={
                "action": {"name": "move", "arguments": {"source": 40, "target": 33}, "revision": 0},
                "receipt": game.story_snapshot("narratron") or {},
            },
        )
        outcome = game_module.control_game(player_event)
        assert text_provider.generate.call_count == 2
        first_req: TextResponseRequest = text_provider.generate.call_args_list[0][0][0]
        assert first_req.response_schema is None
        assert first_req.response_json_schema is not None
        game_actions_schema = first_req.response_json_schema["properties"]["game_actions"]
        variants = game_actions_schema["items"]["anyOf"]
        move_variant = next(variant for variant in variants if variant["properties"]["name"]["enum"] == ["narratron_move"])
        assert move_variant["properties"]["arguments"]["required"] == ["source", "target"]
        assert "source" in move_variant["properties"]["arguments"]["properties"]
        assert "target" in move_variant["properties"]["arguments"]["properties"]

        retry_req: TextResponseRequest = text_provider.generate.call_args_list[1][0][0]
        assert "'source' is a required property" in retry_req.prompt
        assert [receipt.action.arguments for receipt in outcome.narratron_receipts] == [{"source": 17, "target": 26}]
        assert game.revision == 2
    finally:
        journal.close()


def test_empty_narratron_actions_constrains_game_actions_to_empty_array(
    pad: Notepad, tmp_path: Path,
) -> None:
    canvas = make_example_canvas(tmp_path / "theater")
    game = canvas.game
    assert game is not None
    # Revision 0 is Red's turn, so no Narratron actions are available.
    text_provider = Mock(spec=TextResponseProvider)
    text_provider.generate.return_value = TextResponseResult(
        text=GameControlDecision(reason="Red's turn.").model_dump_json(),
        provider="mock",
        model="fast-game",
    )
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_module = StoryGameModule(
        pad, journal, game_state=game, text_response_provider=text_provider,
    )
    try:
        player_event = RecordingEvent(
            event_id="player:checkers:0:recording",
            action_id="player:checkers:0",
            kind="committed_game_action",
            source_revision=0,
            payload={"action": {"name": "reset", "arguments": {}, "revision": 0}, "receipt": {}},
        )
        game_module.control_game(player_event)
        req: TextResponseRequest = text_provider.generate.call_args[0][0]
        assert req.response_schema is None
        assert req.response_json_schema is not None
        assert req.response_json_schema["properties"]["game_actions"]["maxItems"] == 0
    finally:
        journal.close()

