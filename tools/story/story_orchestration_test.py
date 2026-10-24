"""Exercise the production turn scheduler and its durable failure boundaries."""

from collections.abc import Iterator
import json
from pathlib import Path
from threading import Event
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import JsonValue

from components.canvas.game_runtime import GameAction
from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.notepad import NoteSnapshot
from components.reference_manager import ReferenceManager
from components.theater_manager import Theater, TheaterManager
from providers import TextResponseProvider
from tools.story.story_handoffs import GameControlDecision, NarrationRequest
from tools.story.story_recording_journal import RecordingEvent
from tools.story.story_response_module import MAX_GAME_ENTRY_RESPONSE_ATTEMPTS
from tools.story.story_session import StoryRole, StorySession
from tools.story.story_tool import StoryTool
from testing.game_fixtures import exchange_definition, make_game


class TurnHarness:
    def __init__(self, root: Path) -> None:
        self.theater = MagicMock(spec=Theater, wraps=TheaterManager(root).theater("turns"))
        self.theater.theater_id = "turns"
        self.theater.config.return_value = {"story_planning": {"adventure_mode": True, "cooldown_duration": 0}}
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

    def new_tool(self) -> StoryTool:
        canvas = MagicMock(spec=CanvasStateManager)
        canvas.notepad = None
        canvas.story = StoryState()
        canvas.game = None
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
    tool._on_scene_reaction = delivered
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
    tool._on_scene_reaction = delivered

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
    assert tool._foreground_work[turn.turn_id].result() == {"status": "pending", "story_turn_id": turn.turn_id}
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
    assert harness.calls == ["responder", "recorder"]
    assert tool.notepad.snapshot().notes["Inventory"] == "Key"
    harness.fail_recording = False
    tool.retry_pending_turns()
    assert tool.wait_for_pending_events(5)
    assert harness.calls.count("responder") == 2
    assert harness.calls.count("recorder") == 3
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
    with patch.object(StoryTool, "_schedule_cooldown_timer"):
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


def test_planner_can_continue_while_next_action_records_and_rechecks_stale_guidance(harness: TurnHarness) -> None:
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
        tool.resolve_action("Turn the key")
        assert started.wait(5)
        harness.decision = GameControlDecision(factual_updates={"Inventory": "Second key"})
        second = tool.resolve_action("Find another key")
        assert "error" not in second
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
    game.before_player_action = tool._wait_for_foreground
    game.on_player_action = tool._handle_player_game_action

    harness.fail_recording = True
    assert "error" in tool.resolve_action("Turn the key")
    assert not tool.turn_journal.turns()[0].foreground_ready

    # Transient recording failure clears; next player game action recovers the
    # prior turn in before_player_action instead of remaining permanently wedged.
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

    with (
        patch.object(tool.recorder_module, "record", side_effect=record_once_failing),
        patch.object(StoryTool, "_schedule_cooldown_timer"),
    ):
        first = tool.process_user_action("Turn the key")
        assert type(first) is dict
        assert started.wait(5)
        second = tool._schedule_turn(
            tool.turn_journal.accept(
                tool.turn_journal.turns()[0].model_copy(
                    update={"turn_id": "turn-2", "position": 0, "action": "Open the door", "response": None}
                )
            ),
            retry_earlier=True,
        )
        release.set()
        result = second.result(timeout=5)
        assert "error" not in result
        assert tool.wait_for_pending_events(5)
    assert all(turn.completed for turn in tool.turn_journal.turns())


def test_process_user_action_rejects_internal_game_entry_narration_prompt(harness: TurnHarness) -> None:
    tool = harness.new_tool()
    result = tool.process_user_action("Narrate the recorder-established outcome.")
    assert type(result) is dict
    assert "error" in result
    assert tool.turn_journal.turns() == []
