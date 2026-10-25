"""Exercise the production turn scheduler and its durable failure boundaries."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import JsonValue

from components.canvas.game_runtime import GameAction
from components.canvas.game_state import GameState
from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.notepad import NoteSnapshot
from components.reference_manager import ReferenceManager
from components.theater_manager import Theater, TheaterManager
from providers import TextResponseProvider
from components.story.story_handoffs import GameControlDecision, NarrationRequest
from components.story.story_recording_journal import RecordingEvent
from components.story.story_response_module import MAX_GAME_ENTRY_RESPONSE_ATTEMPTS
from components.story.story_session import StoryRole, StorySession
from components.story.story_turn_journal import StoryTurn
from tools.story.story_tool import StoryTool
from testing.game_fixtures import exchange_definition, make_game


class TurnHarness:
    def __init__(self, root: Path) -> None:
        self.theater = MagicMock(spec=Theater, wraps=TheaterManager(root).theater("turns"))
        self.theater.theater_id = "turns"
        self.theater.config.return_value = {"story_planning": {"adventure_mode": True, "input_cycle_length": 0}}
        self.theater.read_planning_schema.return_value = {
            "Inventory": {"owner": "recorder", "initial": "Key"},
            "Ambush": {"owner": "planner", "initial": "Wait"},
        }
        self.theater.lore_documents.return_value = []
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        self.decision = GameControlDecision(factual_updates={"Inventory": "Broken key"})
        self.fail_recording = False
        self.fail_planning = False
        self.fail_response = False
        self.tools: list[StoryTool] = []

    def new_tool(self, game: GameState | None = None, *, story: StoryState | None = None) -> StoryTool:
        canvas = MagicMock(spec=CanvasStateManager)
        canvas.notepad = None
        canvas.story = StoryState() if story is None else story
        canvas.game = game
        references = MagicMock(spec=ReferenceManager)
        references.export_characters.return_value = []
        references.get_present_characters.return_value = []
        references.export_player_character.return_value = None
        references.get_player_character.return_value = None
        tool = StoryTool(self.theater, canvas, MagicMock(spec=TextResponseProvider), references)
        self.tools.append(tool)
        return tool

    def model_run(self, role: StoryRole, prompt: str) -> str:
        self.calls.append(role)
        self.prompts.append((role, prompt))
        if role == "recorder":
            if self.fail_recording:
                raise RuntimeError("recording unavailable")
            return self.decision.model_dump_json()
        if role == "planner":
            if self.fail_planning:
                raise RuntimeError("planning unavailable")
            return "No planning changes."
        if self.fail_response:
            raise RuntimeError("narration unavailable")
        return '{"narration": "The key breaks.", "proposed_factual_changes": []}'

    def close(self) -> None:
        for tool in self.tools:
            tool.close()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[TurnHarness]:
    harness = TurnHarness(tmp_path)

    def run(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        return harness.model_run(role, prompt)

    with patch.object(StorySession, "run", run):
        yield harness
        harness.close()


def test_communicated_turn_records_then_plans_without_request(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    result = tool.resolve_action("Turn the key", drain=True)
    assert result["narration"] == "The key breaks."
    assert harness.calls == ["responder", "recorder", "planner"]
    assert tool.notepad.snapshot().notes["Inventory"] == "Broken key"
    assert tool.turn_journal.turns()[0].completed
    assert tool.turn_journal.turns()[0].entry_role == "responder"
    assert tool.recording_journal.pending() == []


@pytest.mark.parametrize("with_contract", [False, True])
def test_unstructured_adventure_stages_freeform_notes_and_recovers_after_restart(
    harness: TurnHarness, with_contract: bool,
) -> None:
    harness.theater.config.return_value["story_planning"]["enforce_structured"] = False
    if not with_contract:
        harness.theater.read_planning_schema.return_value = None
    harness.decision = GameControlDecision()
    tool = harness.new_tool()
    assert "Freeform sticky creation is enabled" in tool.recorder_module._build_recorder_instruction()
    planner_instruction = tool.planning_module._build_deep_planner_instruction()
    assert "Freeform sticky creation is enabled" in planner_instruction
    assert "only durable planning vocabulary" not in planner_instruction

    def run(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            assert tool.recorder_module.staged_notes.update_sticky_note("Discovery", "Found a key").endswith("staged.")
            assert "Discovery" not in tool.notepad.snapshot().notes
        elif role == "planner":
            assert "Found a key" in prompt
            assert tool.planning_module.staged_notes.update_sticky_note("Possibility", "A guard may investigate").endswith("staged.")
            assert "Possibility" not in tool.notepad.snapshot().notes
        return harness.model_run(role, prompt)

    with patch.object(StorySession, "run", run):
        assert "error" not in tool.resolve_action("Inspect the courtyard", drain=True)
    assert harness.calls == ["responder", "recorder", "planner"]
    assert tool.notepad.snapshot().notes["Discovery"] == "Found a key"
    assert tool.notepad.snapshot().notes["Possibility"] == "A guard may investigate"
    snapshot = tool.notepad.snapshot()
    tool.close()
    harness.tools.remove(tool)
    restarted = harness.new_tool()
    assert restarted.wait_for_pending_events(5)
    assert restarted.notepad.snapshot() == snapshot


def test_response_recording_can_prepare_game_offer_awaiting_player_consent(
    harness: TurnHarness, tmp_path: Path,
) -> None:
    tool = harness.new_tool()
    game = make_game(tmp_path / "story-offer", exchange_definition())
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            assert "# Recorder Work (record)" in prompt
            for name, arguments in (("narratron_place", '{"item":"green"}'), ("narratron_finalize", "{}")):
                receipt = tool.recorder_module.take_game_action(name, arguments, game.revision)
                assert not receipt.startswith("Error:"), receipt
        return harness.model_run(role, prompt)

    with patch.object(StorySession, "run", model):
        result = tool.resolve_action("Show me your offer", drain=True)
        assert "error" not in result
        assert harness.calls == ["responder", "recorder", "planner"]
        assert game.revision == 2
        assert game.state["narratron_offer"] == ["green"]
        assert game.state["narratron_done"] is True
        assert game.state["player_done"] is False
        assert game.state["trades"] == 0
        assert game.state["player_inventory"] == ["red", "blue"]
        turn = tool.turn_journal.turns()[0]
        assert turn.completed
        event = tool._recording_event(turn)
        receipts = tool.recording_journal.narratron_receipts(event.event_id)
        assert len(receipts) == 2
        assert all(tool.recording_journal.recorded_revision(receipt.event_id) is not None for receipt in receipts)
        assert tool.recording_journal.is_game_control_completed(event.event_id)
        assert tool.recording_journal.pending() == []
        tool.recorder_module.record(event)
        assert game.revision == 2
        assert harness.calls == ["responder", "recorder", "planner"]
        assert "Error:" in tool.recorder_module.take_game_action("narratron_finalize", "{}", game.revision)


@pytest.mark.parametrize("attempt", ["prerequisite", "arguments", "actor"])
def test_response_recording_preserves_game_validation(
    harness: TurnHarness, tmp_path: Path, attempt: str,
) -> None:
    tool = harness.new_tool()
    definition = exchange_definition()
    definition.transitions["narratron_finalize"].guard = "state.player_done && state.player_offer.size() > 0"
    game = make_game(tmp_path / "story-prerequisite", definition)
    game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game
    before = game.serialize()
    errors: list[str] = []
    actions = {
        "prerequisite": ("narratron_finalize", "{}"),
        "arguments": ("narratron_place", '{"item":42}'),
        "actor": ("player_finalize", "{}"),
    }

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            name, arguments = actions[attempt]
            errors.append(tool.recorder_module.take_game_action(name, arguments, game.revision))
        return harness.model_run(role, prompt)

    with patch.object(StorySession, "run", model):
        result = tool.resolve_action("Accept my offer", drain=True)
    assert "error" not in result
    assert len(errors) == 1 and errors[0].startswith("Error:")
    assert "active story-response" not in errors[0]
    assert game.serialize() == before
    assert tool.recording_journal.pending() == []


@pytest.mark.parametrize("boundary", ["receipt_projection", "completion"])
def test_response_recording_retries_receipt_completion_without_repeating_models(
    harness: TurnHarness, tmp_path: Path, boundary: str,
) -> None:
    tool = harness.new_tool()
    game = make_game(tmp_path / "story-recovery", exchange_definition())
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game
    commit = tool.recording_journal.commit

    def fail_receipt_commit(event_id: str, snapshot: NoteSnapshot) -> None:
        if ":narratron:" in event_id:
            raise OSError("receipt projection unavailable")
        commit(event_id, snapshot)

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            receipt = tool.recorder_module.take_game_action("narratron_place", '{"item":"green"}', game.revision)
            assert not receipt.startswith("Error:"), receipt
        return harness.model_run(role, prompt)

    failure = (
        patch.object(tool.recording_journal, "commit", side_effect=fail_receipt_commit)
        if boundary == "receipt_projection"
        else patch.object(tool.recording_journal, "complete_game_control", side_effect=OSError("completion unavailable"))
    )
    with patch.object(StorySession, "run", model):
        with failure:
            assert "error" in tool.resolve_action("Show me your offer")
        assert game.revision == 1
        assert harness.calls == ["responder", "recorder"]
        tool.retry_pending_turns()
        assert tool.wait_for_pending_events(5)
    assert game.revision == 1
    assert harness.calls == ["responder", "recorder", "planner"]
    turn = tool.turn_journal.turns()[0]
    assert turn.completed
    assert tool.recording_journal.is_game_control_completed(tool._recording_event(turn).event_id)
    assert tool.recording_journal.pending() == []


def test_recording_narratron_receipt_cannot_submit_more_actions(
    harness: TurnHarness, tmp_path: Path,
) -> None:
    tool = harness.new_tool()
    game = make_game(tmp_path / "receipt-only", exchange_definition())
    tool.recorder_module._game_state = game
    errors: list[str] = []

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            errors.append(tool.recorder_module.take_game_action("narratron_place", '{"item":"green"}', game.revision))
        return harness.model_run(role, prompt)

    event = RecordingEvent(
        event_id="receipt-only", action_id="story-turn", source_event_ids=("story-response",),
        kind="committed_game_action", source_revision=0, payload={"actor": "narratron"},
    )
    with patch.object(StorySession, "run", model):
        tool.recorder_module.record(event)
    assert len(errors) == 1 and "active story-response or game-entry recording stage" in errors[0]
    assert game.revision == 0
    assert harness.calls == ["recorder"]
    assert tool.recording_journal.pending() == []


def test_game_turn_skip_is_explicit_and_planning_is_mandatory(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    action = GameAction(name="move", arguments={}, revision=0)
    receipt = {"revision": 1, "state": {"secret": "raw game secret"}}
    tool._handle_player_game_action(action, receipt)
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "planner"]
    turn = tool.turn_journal.turns()[0]
    assert turn.entry_role == "recorder"
    assert turn.responder_skipped
    assert turn.narration_reason
    assert turn.completed
    tool._handle_player_game_action(action, receipt)
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "planner"]


def test_significant_game_turn_narrates_brief_without_recording_again(harness: TurnHarness) -> None:
    harness.decision = GameControlDecision(
        narration_request=NarrationRequest(user_action="A trade completed", brief="The merchant hands over the key."),
        reason="A consequential trade.",
    )
    tool = harness.new_tool()
    tool._handle_player_game_action(GameAction(name="trade", arguments={}, revision=0), {
        "revision": 1, "state": {"raw_game_secret": 42}, "instructions": "private mechanics",
    })
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "responder", "planner"]
    responder_prompt = next(prompt for role, prompt in harness.prompts if role == "responder")
    assert "The merchant hands over the key" in responder_prompt
    assert "raw_game_secret" not in responder_prompt
    assert "private mechanics" not in responder_prompt


def test_game_entry_rejection_is_returned_to_responder_and_only_correction_is_published(
    harness: TurnHarness, caplog: pytest.LogCaptureFixture,
) -> None:
    harness.decision = GameControlDecision(
        narration_request=NarrationRequest(user_action="A trade completed", brief="The merchant hands over the key."),
    )
    tool = harness.new_tool()
    delivered = MagicMock()
    tool.on_scene_reaction = delivered
    stages: list[str] = []

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        response = harness.model_run(role, prompt)
        if role == "responder":
            stages.append(stage_id)
            assert "The merchant hands over the key." in prompt
            assert "raw_game_secret" not in prompt
            if len(stages) == 1:
                return json.dumps({
                    "narration": "The merchant also gives you a crown.",
                    "proposed_factual_changes": [{
                        "topic": "Inventory", "info": "Key and crown", "evidence": "A new gift.",
                    }],
                })
            assert "# Application Validation Feedback" in prompt
            assert "Game-entry narration cannot establish new factual or mechanical changes" in prompt
            assert "Key and crown" in prompt
            return '{"narration": "The merchant hands over the key.", "proposed_factual_changes": []}'
        return response

    with patch.object(StorySession, "run", model), patch.object(tool, "record_tool_failure") as failure:
        tool._handle_player_game_action(GameAction(name="trade", arguments={}, revision=0), {
            "revision": 1, "state": {"raw_game_secret": 42},
        })
        assert tool.wait_for_pending_events(5)
        failure.assert_not_called()
    assert harness.calls == ["recorder", "responder", "responder", "planner"]
    assert stages == [tool.turn_journal.turns()[0].turn_id] * 2
    delivered.assert_called_once()
    assert delivered.call_args.args[0]["narration"] == "The merchant hands over the key."
    assert delivered.call_args.args[0]["proposed_factual_changes"] == []
    assert "Rejected game-entry narration" in caplog.text
    assert tool.response_module._responder_feedback == ""
    assert not tool.response_module._game_entry


@pytest.mark.parametrize("restart", [False, True])
def test_game_entry_repeated_rejections_stay_pending_without_live_agent_error(
    harness: TurnHarness, caplog: pytest.LogCaptureFixture, restart: bool,
) -> None:
    harness.decision = GameControlDecision(
        narration_request=NarrationRequest(user_action="A trade completed", brief="The merchant hands over the key."),
    )
    tool = harness.new_tool()
    delivered = MagicMock()
    tool.on_scene_reaction = delivered

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        response = harness.model_run(role, prompt)
        if role == "responder":
            return json.dumps({
                "narration": "The merchant also gives you a crown.",
                "proposed_factual_changes": [{
                    "topic": "Inventory", "info": "Key and crown", "evidence": "A new gift.",
                }],
            })
        return response

    with patch.object(StorySession, "run", model), patch.object(tool, "record_tool_failure") as failure:
        tool._handle_player_game_action(GameAction(name="trade", arguments={}, revision=0), {"revision": 1})
        assert not tool.wait_for_pending_events(5)
        failure.assert_not_called()
    delivered.assert_not_called()
    assert harness.calls == ["recorder"] + ["responder"] * MAX_GAME_ENTRY_RESPONSE_ATTEMPTS
    turn = tool.turn_journal.turns()[0]
    assert not tool.is_action_in_flight
    assert turn.recorder_completed
    assert turn.response is None
    assert not turn.foreground_ready
    assert not turn.planner_completed
    assert "awaits corrected game-entry narration" in caplog.text
    assert tool.response_module._responder_feedback == ""
    assert not tool.response_module._game_entry

    if restart:
        tool.close()
        harness.tools.remove(tool)
        tool = harness.new_tool()
    else:
        tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder"] + ["responder"] * (MAX_GAME_ENTRY_RESPONSE_ATTEMPTS + 1) + ["planner"]
    assert tool.turn_journal.turns()[0].completed


def test_failed_recording_blocks_dependent_action_and_retries_saved_response(harness: TurnHarness) -> None:
    harness.fail_recording = True
    tool = harness.new_tool()
    assert "error" in tool.resolve_action("Turn the key")
    assert "error" in tool.resolve_action("Use the broken key")
    assert harness.calls == ["responder", "recorder", "recorder"]
    assert tool.notepad.snapshot().notes["Inventory"] == "Key"
    harness.fail_recording = False
    tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    assert harness.calls.count("responder") == 2
    assert harness.calls.count("recorder") == 4
    assert harness.calls.count("planner") == 2


def test_planner_failure_does_not_undo_recording_or_repeat_foreground(harness: TurnHarness) -> None:
    harness.fail_planning = True
    tool = harness.new_tool()
    tool.resolve_action("Turn the key")
    assert not tool.wait_for_pending_events(5)
    assert tool.turn_journal.turns()[0].foreground_ready
    assert tool.notepad.snapshot().notes["Inventory"] == "Broken key"
    harness.fail_planning = False
    tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    assert harness.calls.count("responder") == 1
    assert harness.calls.count("recorder") == 1


def test_restart_resumes_skipped_game_planning_without_narration(harness: TurnHarness) -> None:
    harness.fail_planning = True
    first = harness.new_tool()
    first._handle_player_game_action(GameAction(name="move", arguments={}, revision=0), {"revision": 1})
    assert not first.wait_for_pending_events(5)
    first.close()
    harness.tools.remove(first)
    harness.calls.clear()
    harness.decision = GameControlDecision(narration_request=NarrationRequest(user_action="Changed decision", brief="Do not use."))
    harness.fail_planning = False
    restarted = harness.new_tool()
    assert restarted.wait_for_pending_events(5)
    assert harness.calls == ["planner"]
    assert restarted.turn_journal.turns()[0].responder_skipped


def test_public_tool_uses_production_scheduler(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    result = tool.process_user_action("Turn the key")
    assert type(result) is dict
    assert result["status"] == "processing"
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["responder", "recorder", "planner"]


def test_story_turn_publishes_processing_activity_until_foreground_work_completes(
    harness: TurnHarness,
) -> None:
    tool = harness.new_tool()
    started, release = Event(), Event()
    resolve = tool.response_module._resolve_user_action

    def block_resolver(
        action: str,
        nudge: str = "",
        *,
        publish: bool = True,
        game_entry: bool = False,
        stage_id: str = "",
    ) -> dict[str, JsonValue]:
        started.set()
        assert release.wait(5)
        return resolve(
            action,
            nudge=nudge,
            publish=publish,
            game_entry=game_entry,
            stage_id=stage_id,
        )

    tool.response_module._resolve_user_action = block_resolver
    result = tool.process_user_action("Turn the key")

    assert type(result) is dict
    assert started.wait(5)
    tool.canvas_manager.tool_response.set_activity.assert_called_once_with(
        "user_action", active=True,
    )

    release.set()
    assert tool.wait_for_pending_events(5)
    assert tool.canvas_manager.tool_response.set_activity.call_args_list == [
        (("user_action",), {"active": True}),
        (("user_action",), {"active": False}),
    ]


def test_callbacks_and_sticky_publication_cannot_open_turns(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    tool.resolve_action("Turn the key", drain=True)
    calls = list(harness.calls)
    tool._handle_scene_reaction({"narration": "Already completed."})
    source = tool.notepad.snapshot()
    tool.notepad.publish_role_updates("planner", {"Ambush": "If discovered, investigate."}, source=source)
    assert harness.calls == calls
    assert len(tool.turn_journal.turns()) == 1


def test_next_action_waits_for_recording_commit(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    started, release = Event(), Event()
    reconcile = tool.recorder_module._reconcile

    def record(event: RecordingEvent, source: NoteSnapshot) -> dict[str, str]:
        started.set()
        assert release.wait(5)
        return reconcile(event, source)

    tool.recorder_module._reconcile = record
    first = tool.process_user_action("Turn the key")
    assert type(first) is dict
    assert started.wait(5)
    tool.process_user_action("Use the broken key")
    assert harness.calls == ["responder"]
    release.set()
    assert tool.wait_for_pending_events(5)


def test_models_have_only_their_authorized_mutation_tools(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    responder = tool.response_module._responder_agent
    planner = tool.planning_module._deep_planner_agent
    recorder = tool.recorder_module.agent
    assert recorder is not None
    response_tools = {function.__name__ for function in responder.tools}
    planner_tools = {function.__name__ for function in planner.tools}
    assert not response_tools & {"update_sticky_note", "take_game_action", "trigger_story_planning", "create_or_update_character", "create_or_update_scene", "update_player_character"}
    assert not planner_tools & {"take_game_action", "trigger_story_response", "create_or_update_character", "create_or_update_scene", "update_player_character"}
    assert tool.story_session.runner.app_name == "narratron_story"
    assert len({agent.output_key for agent in (responder, planner, recorder)}) == 3
    assert all(agent.include_contents == "none" for agent in (responder, planner, recorder))
    assert "cannot write" in tool.planning_module.staged_notes.update_sticky_note("Inventory", "Stolen")
    assert "cannot write" in tool.recorder_module.staged_notes.update_sticky_note("Ambush", "Happened")


def test_restart_resumes_pending_narration_without_another_recorder(harness: TurnHarness) -> None:
    harness.decision = GameControlDecision(
        narration_request=NarrationRequest(user_action="A trade completed", brief="The merchant hands over the key."),
        reason="A consequential trade.",
    )
    harness.fail_response = True
    first = harness.new_tool()
    first._handle_player_game_action(GameAction(name="move", arguments={}, revision=0), {"revision": 1})
    assert not first.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "responder"]
    first.close()
    harness.tools.remove(first)
    harness.calls.clear()
    harness.fail_response = False
    restarted = harness.new_tool()
    assert restarted.wait_for_pending_events(5)
    assert harness.calls == ["responder", "planner"]
    assert restarted.turn_journal.turns()[0].completed


def test_restart_preserves_latest_planner_checkpoint(harness: TurnHarness) -> None:
    first = harness.new_tool()
    with patch.object(first.planning_module, "plan_recorded_turn", return_value={"Ambush": "Investigate only if discovered."}):
        first.resolve_action("Turn the key", drain=True)
    revision = first.notepad.revision
    first.close()
    harness.tools.remove(first)
    restarted = harness.new_tool()
    assert restarted.notepad.snapshot().notes == {
        "Inventory": "Broken key", "Ambush": "Investigate only if discovered.",
    }
    assert restarted.notepad.revision >= revision


@pytest.mark.parametrize("ingress", ["game_action", "communicated_action"])
def test_failed_receipt_write_recovers_from_game_commit_without_reexecuting(
    harness: TurnHarness, tmp_path: Path, ingress: Literal["game_action", "communicated_action"],
) -> None:
    tool = harness.new_tool()
    game = make_game(tmp_path / "game-case", exchange_definition())
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game
    player = GameAction(name="player_place", arguments={"item": "red"}, revision=0)
    game.act(player)
    attempted = False

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        nonlocal attempted
        if role == "recorder" and not attempted:
            attempted = True
            tool.recorder_module.take_game_action("narratron_place", '{"item":"green"}', 1)
        elif role == "recorder":
            assert '"narratron_place"' in prompt
            assert "Already committed Narratron receipts" in prompt
        return harness.model_run(role, prompt)

    with patch.object(StorySession, "run", model):
        with patch.object(tool.recording_journal, "record_narratron_receipt", side_effect=OSError("journal offline")):
            if ingress == "game_action":
                tool._handle_player_game_action(player, game.pending_story_actions()[0][1])
            else:
                assert "error" in tool.resolve_action("Show me the green item")
            assert not tool.wait_for_pending_events(5)
            assert game.revision == 2
        tool.retry_pending_turns()
        assert tool.wait_for_pending_events(5)
    assert game.revision == 2
    assert len(tool.recording_journal.narratron_receipts(tool.turn_journal.turns()[0].turn_id + ":recording")) == 1
    assert tool.recording_journal.pending() == []
    assert tool.turn_journal.turns()[0].completed


def test_planning_and_next_action_share_one_engine_flight(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    started, release = Event(), Event()
    sources: list[int] = []

    def plan(handoff: dict[str, JsonValue], source: NoteSnapshot) -> dict[str, str]:
        sources.append(source.revision)
        if len(sources) == 1:
            started.set()
            assert release.wait(5)
        return {"Ambush": f"Investigate after revision {source.revision}."}

    with patch.object(tool.planning_module, "plan_recorded_turn", side_effect=plan):
        tool.process_user_action("Turn the key")
        try:
            assert started.wait(5)
            harness.decision = GameControlDecision(factual_updates={"Inventory": "Second key"})
            tool.process_user_action("Find another key")
            assert harness.calls == ["responder", "recorder"]
        finally:
            release.set()
        assert tool.wait_for_pending_events(5)
    assert sources[1] > sources[0]
    assert len(tool.turn_journal.turns()) == 2
    assert all(turn.completed for turn in tool.turn_journal.turns())


def test_failed_turn_recovers_on_subsequent_player_game_action(
    harness: TurnHarness, tmp_path: Path,
) -> None:
    tool = harness.new_tool()
    game = make_game(tmp_path / "game-recovery", exchange_definition())
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game
    game.on_player_action = tool._handle_player_game_action

    harness.fail_recording = True
    assert "error" in tool.resolve_action("Turn the key")
    assert not tool.turn_journal.turns()[0].foreground_ready

    # The next cycle recovers the failed stage after the game commits.
    harness.fail_recording = False
    game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
    assert tool.wait_for_pending_events(5)
    assert len(tool.turn_journal.turns()) == 2
    assert all(turn.completed for turn in tool.turn_journal.turns())


def test_in_flight_turn_failure_recovers_when_queued_action_runs(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    started, release = Event(), Event()
    attempts = 0
    original_record = tool.recorder_module.record

    def record_once_failing(event: RecordingEvent) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            assert release.wait(5)
            raise RuntimeError("transient recorder timeout")
        return original_record(event)

    with patch.object(tool.recorder_module, "record", side_effect=record_once_failing):
        first = tool.process_user_action("Turn the key")
        assert type(first) is dict
        assert started.wait(5)
        second = tool.process_user_action("Open the door")
        assert second["status"] == "processing"
        release.set()
        assert tool.wait_for_pending_events(5)
    assert all(turn.completed for turn in tool.turn_journal.turns())


def test_process_user_action_rejects_internal_game_entry_narration_prompt(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    result = tool.process_user_action("Narrate the recorder-established outcome.")
    assert type(result) is dict
    assert "error" in result
    assert tool.turn_journal.turns() == []


def test_cycle_combines_game_receipts_and_all_spoken_actions(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    tool.resolve_action("Look around", drain=True)
    harness.calls.clear()
    harness.prompts.clear()
    tool._story_log.clear()
    harness.decision = GameControlDecision(
        factual_updates={"Inventory": "Broken key"},
        narration_request=NarrationRequest(user_action="Trade", brief="The merchant trades the key."),
    )
    delivered = MagicMock()
    tool.on_scene_reaction = delivered
    tool.process_user_action("Ask about the merchant")
    tool._handle_player_game_action(GameAction(name="trade", revision=0), {"revision": 1})
    tool.process_user_action("Examine the key")
    assert harness.calls == []
    assert tool._story_log == []

    def model(session: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        result = harness.model_run(role, prompt)
        if role == "recorder" and "# Recorder Work (record)" in prompt:
            return harness.decision.model_copy(update={"narration_request": None}).model_dump_json()
        return result

    with patch.object(StorySession, "run", model):
        assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "responder", "recorder", "planner"]
    prompt = next(prompt for role, prompt in harness.prompts if role == "responder")
    assert "Ask about the merchant" in prompt
    assert "Examine the key" in prompt
    assert "The merchant trades the key." in prompt
    assert "Do not resolve those game actions again" in prompt
    delivered.assert_called_once()
    turns = tool.turn_journal.turns()[1:]
    assert len(turns) == 3
    assert len({turn.cycle_id for turn in turns}) == 1
    assert sum(turn.response is not None for turn in turns) == 1
    assert all(turn.completed for turn in turns)


def test_game_commit_during_speech_processing_queues_without_waiting(
    harness: TurnHarness, tmp_path: Path,
) -> None:
    game = make_game(tmp_path / "concurrent-game", exchange_definition())
    tool = harness.new_tool(game)
    started, release = Event(), Event()
    original = tool.response_module._resolve_user_action

    def resolve(
        action: str, nudge: str = "", *, publish: bool = True,
        game_entry: bool = False, stage_id: str = "",
    ) -> dict[str, JsonValue]:
        if action == "Turn the key":
            started.set()
            assert release.wait(5)
        return original(action, nudge, publish=publish, game_entry=game_entry, stage_id=stage_id)

    with patch.object(tool.response_module, "_resolve_user_action", side_effect=resolve):
        tool.process_user_action("Turn the key")
        try:
            assert started.wait(5)
            with ThreadPoolExecutor(max_workers=1) as pool:
                commit = pool.submit(game.act, GameAction(name="player_place", arguments={"item": "red"}, revision=0))
                try:
                    assert commit.result(timeout=2)["revision"] == 1
                    tool.process_user_action("Inspect the red item")
                    assert harness.calls == []
                finally:
                    release.set()
        finally:
            release.set()
        assert tool.wait_for_pending_events(5)
    assert game.revision == 1
    assert all(turn.completed for turn in tool.turn_journal.turns())
    assert tool.recording_journal.pending() == []


@pytest.mark.parametrize("stage", ["response", "recording", "planning"])
@pytest.mark.parametrize("restart_cycle_length", [0, 60])
def test_combined_cycle_recovers_frozen_membership_after_restart(
    harness: TurnHarness, stage: str, restart_cycle_length: float,
) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    tool.resolve_action("Look around", drain=True)
    harness.calls.clear()
    tool.process_user_action("Turn the key")
    tool.process_user_action("Examine the broken key")
    harness.fail_response = stage == "response"
    harness.fail_recording = stage == "recording"
    harness.fail_planning = stage == "planning"
    assert not tool.wait_for_pending_events(5)
    original_ids = [turn.turn_id for turn in tool.turn_journal.turns()]
    tool.close()
    harness.tools.remove(tool)
    harness.fail_response = harness.fail_recording = harness.fail_planning = False
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = restart_cycle_length
    recovered = harness.new_tool()
    assert recovered.wait_for_pending_events(5)
    turns = recovered.turn_journal.turns()
    assert [turn.turn_id for turn in turns] == original_ids
    assert len({turn.cycle_id for turn in turns[1:]}) == 1
    assert all(turn.completed for turn in turns)
    assert harness.calls.count("responder") == (2 if stage == "response" else 1)
    assert harness.calls.count("recorder") == (2 if stage == "recording" else 1)
    assert harness.calls.count("planner") == (2 if stage == "planning" else 1)
    assert len([entry for entry in recovered._story_log if entry["type"] == "story_response"]) == 2


def test_failing_delivery_callback_cannot_hold_cycle_gate(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    tool.on_scene_reaction = MagicMock(side_effect=RuntimeError("live agent disconnected"))
    tool.process_user_action("Turn the key")
    assert tool.wait_for_pending_events(5)
    assert not tool.is_action_in_flight
    tool.process_user_action("Examine the broken key")
    assert tool.wait_for_pending_events(5)
    assert harness.calls.count("responder") == 2
    assert all(turn.delivered for turn in tool.turn_journal.turns())


def test_system_opening_uses_canvas_cycle(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    started, release = Event(), Event()
    original = tool.response_module._resolve_user_action

    def resolve(action: str, **kwargs: JsonValue) -> dict[str, JsonValue]:
        started.set()
        assert release.wait(5)
        return original(action, **kwargs)

    with patch.object(tool.response_module, "_resolve_user_action", side_effect=resolve):
        try:
            result = tool.process_system_action("Begin the adventure", "Opening")
            assert result["status"] == "processing"
            assert started.wait(5)
            assert tool.canvas_manager.story.input_cycle.is_active
        finally:
            release.set()
        assert tool.wait_for_pending_events(5)
    assert harness.calls == ["responder", "recorder", "planner"]


def test_component_action_api_cannot_bypass_canvas_cycle(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    started, release = Event(), Event()
    original = tool.response_module._resolve_user_action

    def resolve(action: str, **kwargs: JsonValue) -> dict[str, JsonValue]:
        started.set()
        assert release.wait(5)
        return original(action, **kwargs)

    with patch.object(tool.response_module, "_resolve_user_action", side_effect=resolve):
        try:
            assert tool.engine.process_user_action("Turn the key")["status"] == "processing"
            assert started.wait(5)
            assert tool.canvas_manager.story.input_cycle.is_active
        finally:
            release.set()
        assert tool.wait_for_pending_events(5)
    assert harness.calls == ["responder", "recorder", "planner"]


def test_game_only_cycle_records_each_receipt_and_narrates_once(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    tool.resolve_action("Look around", drain=True)
    harness.calls.clear()
    tool._story_log.clear()
    harness.decision = GameControlDecision(narration_request=NarrationRequest(user_action="Move", brief="A piece moves."))
    tool._handle_player_game_action(GameAction(name="move", revision=0), {"revision": 1})
    tool._handle_player_game_action(GameAction(name="move", revision=1), {"revision": 2})
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["recorder", "recorder", "responder", "planner"]
    assert all(turn.completed for turn in tool.turn_journal.turns())
    assert len([entry for entry in tool._story_log if entry["type"] == "story_response"]) == 1


def test_out_of_order_callbacks_drain_durable_game_receipts_in_revision_order(
    harness: TurnHarness, tmp_path: Path,
) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    game = make_game(tmp_path / "outbox")
    game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
    game.act(GameAction(name="add", arguments={"amount": 1}, revision=1))
    pending = game.pending_story_actions()
    tool = harness.new_tool()
    tool.canvas_manager.game = game
    tool.recorder_module._game_state = game
    tool.planning_module.game_state = game
    tool._handle_player_game_action(*pending[1])
    tool._handle_player_game_action(*pending[0])
    assert [turn.game_payload["action"]["revision"] for turn in tool.turn_journal.turns()] == [0, 1]
    assert game.pending_story_actions() == []
    assert tool.wait_for_pending_events(5)
    assert game.revision == 2


def test_accepting_same_action_concurrently_creates_one_durable_input(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    tool.resolve_action("Look around", drain=True)
    harness.calls.clear()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(tool.process_user_action, ["Turn the key"] * 4))
    assert len({result["story_turn_id"] for result in results}) == 1
    assert tool.wait_for_pending_events(5)
    assert harness.calls == ["responder", "recorder", "planner"]


def test_legacy_partially_recorded_turns_keep_separate_saved_responses(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"]["input_cycle_length"] = 60
    tool = harness.new_tool()
    for index in range(2):
        tool.turn_journal.accept(StoryTurn(
            turn_id=f"legacy-{index}", ingress="communicated_action", action=f"Action {index}",
            response={"narration": f"Saved response {index}.", "notepad_revision_used": 0},
        ))
    tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    turns = tool.turn_journal.turns()
    assert len({turn.cycle_id for turn in turns}) == 2
    assert harness.calls == ["recorder", "planner", "recorder", "planner"]
    assert all(turn.completed and turn.delivered for turn in turns)


def test_rejected_durable_input_does_not_consume_orator_gate(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    tool.response_module.require_user_input = True
    tool.record_user_input()
    with patch.object(tool.turn_journal, "accept", side_effect=OSError("journal offline")):
        with pytest.raises(OSError, match="journal offline"):
            tool.process_user_action("Turn the key")
    assert tool.response_module.is_user_input_detected
    assert not tool.is_action_in_flight
    assert tool.process_user_action("Turn the key")["status"] == "processing"
    assert tool.wait_for_pending_events(5)


def test_story_log_write_failure_does_not_repeat_delivery(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    delivered = MagicMock()
    tool.on_scene_reaction = delivered
    with patch.object(tool.theater, "append_output_file", side_effect=OSError("log offline")):
        tool.process_user_action("Turn the key")
        assert tool.wait_for_pending_events(5)
    tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    delivered.assert_called_once()
    assert harness.calls == ["responder", "recorder", "planner"]


def test_input_processing_continues_while_latest_output_waits_for_canvas(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"].update({
        "input_cycle_length": 0, "output_cycle_length": 60,
    })
    with patch("components.story.output_display_cycle.Timer"):
        tool = harness.new_tool()
        story = tool.canvas_manager.story

        def resolve(action: str, **kwargs: JsonValue) -> dict[str, JsonValue]:
            return {"narration": action, "dialogue": [{"speaker": "Mara", "text": action}]}

        with patch.object(tool.response_module, "_resolve_user_action", side_effect=resolve):
            for action in ("first", "second", "third"):
                assert tool.resolve_action(action)["narration"] == action
        assert story.narration == "first"
        assert story.scene_dialogue[0]["text"] == "first"
        assert story.input_cycle.wait(5)
        turns = tool.turn_journal.turns()
        assert all(turn.completed and turn.delivered for turn in turns)
        assert story.output_cycle.payload()["displayed_sequence"] == turns[0].position
        assert story.output_cycle.payload()["accepted_sequence"] == turns[2].position
        assert tool.wait_for_pending_events(5)
        assert story.narration == "third"
        assert story.scene_dialogue[0]["text"] == "third"
        assert story.output_cycle.payload()["displayed_sequence"] == turns[2].position


def test_pending_output_survives_restart_after_turn_is_acknowledged(harness: TurnHarness) -> None:
    harness.theater.config.return_value["story_planning"].update({
        "input_cycle_length": 0, "output_cycle_length": 60,
    })
    with patch("components.story.output_display_cycle.Timer"):
        tool = harness.new_tool()
        with patch.object(tool.response_module, "_resolve_user_action", side_effect=[
            {"narration": "first"}, {"narration": "second"}, {"narration": "third"},
        ]):
            for action in ("first", "second", "third"):
                tool.resolve_action(action)
        assert tool.cycle.wait(5)
        assert all(turn.delivered for turn in tool.turn_journal.turns())
        saved = tool.canvas_manager.story.serialize()
        tool.close()
        restored = StoryState()
        restored.load(saved)
        restarted = harness.new_tool(story=restored)
        assert restored.narration == "first"
        assert restarted.wait_for_pending_events(5)
        assert restored.narration == "third"
        assert restored.output_cycle.payload()["displayed_sequence"] == 3
        assert harness.calls == ["recorder", "planner"] * 3
