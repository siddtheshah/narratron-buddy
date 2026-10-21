"""Exercise the production turn scheduler and its durable failure boundaries."""

from collections.abc import Iterator
from pathlib import Path
from threading import Event
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


def test_failed_receipt_write_recovers_from_game_commit_without_reexecuting(
    harness: TurnHarness, tmp_path: Path,
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
            tool._handle_player_game_action(player, game.pending_story_actions()[0][1])
            assert not tool.wait_for_pending_events(5)
            assert game.revision == 2
        tool.retry_pending_turns()
        assert tool.wait_for_pending_events(5)
    assert game.revision == 2
    assert len(tool.recording_journal.narratron_receipts(tool.turn_journal.turns()[0].turn_id + ":recording")) == 1


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
