"""Unit and integration tests for Local Adventure Runner in Test Lab."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from pydantic import JsonValue
from components.story.story_handoffs import GameControlDecision, NarrationRequest
from components.story.story_session import StoryRole, StorySession
from components.canvas.game_runtime import GameAction
from components.reference_manager import Character
from testing.game_fixtures import exchange_definition
from fastapi.testclient import TestClient
import pytest
import yaml

from testlab.adventure_runner import (
    AdventureSession,
    AutoPlayer,
    AutoplayLogger,
    MockCanvasState,
    MockToolBundle,
    PlayerTurnDecision,
    PlayerControlDecision,
    format_repl_turn,
    load_adventure_config,
    main,
    run_autoplay,
)
from testlab.server import app

pytestmark = pytest.mark.usefixtures("offline_lab_text_providers")


@pytest.mark.parametrize("filename", ["game.yaml", "game.yml", "game.json"])
def test_adventure_game_moves_use_real_callback_and_reset(sample_adventure: Path, filename: str) -> None:
    game_dir = sample_adventure / "game"
    game_dir.mkdir()
    definition = exchange_definition()
    content = definition.model_dump_json() if filename == "game.json" else yaml.safe_dump(definition.model_dump(mode="json"))
    (game_dir / filename).write_text(content, encoding="utf-8")
    session = AdventureSession("synthetic-test-adventure")
    try:
        assert session.game is session.story_tool.planning_module.game_state
        assert session.game is session.story_tool.recorder_module._game_state
        assert "game_state" not in session.story_tool.response_module.__dict__
        assert session.get_state()["game"]["revision"] == 0
        move = GameAction(name="player_place", arguments={"item": "red"}, revision=0)
        result = session.take_game_action(move)
        assert result["revision"] == 1
        turn = session.story_tool.turn_journal.turns()[0]
        assert turn.game_payload["receipt"]["revision"] == 1
        assert turn.completed and turn.responder_skipped
        assert session.history[-1]["game_action"] == move.model_dump(mode="json")
        assert session.history[-1]["narration"] == ""
        session.reset()
        assert session.game.revision == 0
        assert session.game.state["player_offer"] == []
        assert session.mock_tools.canvas_state is session.mock_canvas
        assert session.history == []
        (game_dir / filename).unlink()
        session.reset()
        assert session.get_state()["game"] is None
    finally:
        session.cleanup()


def test_adventure_game_http_validation_and_autoplay(sample_adventure: Path, tmp_path: Path) -> None:
    from testlab import server

    game_dir = sample_adventure / "game"
    game_dir.mkdir()
    (game_dir / "game.json").write_text(exchange_definition().model_dump_json(), encoding="utf-8")
    session = AdventureSession("synthetic-test-adventure")
    try:
        server._adventure_runner_sessions[session.session_id] = session
        url = f"/api/adventure-runner/sessions/{session.session_id}/game/action"
        move = GameAction(name="player_place", arguments={"item": "red"}, revision=0)
        with TestClient(app) as client:
            assert client.post(url, json=move.model_dump()).status_code == 200
            assert client.post(url, json=move.model_dump()).status_code == 409
            denied = GameAction(name="narratron_place", arguments={"item": "green"}, revision=1)
            assert client.post(url, json=denied.model_dump()).status_code == 400
            illegal = GameAction(name="player_place", arguments={"item": "missing"}, revision=1)
            assert client.post(url, json=illegal.model_dump()).status_code == 400
        provider = MagicMock()
        provider.generate.return_value.parsed = PlayerTurnDecision(
            thought="Offer the other item.",
            game_action=GameAction(name="player_place", arguments={"item": "blue"}, revision=1),
        )
        player = AutoPlayer(text_provider=provider)
        with patch.object(session, "send_message") as send:
            summary = run_autoplay(session, max_turns=1, player=player, log_path=tmp_path / "moves.json")
            send.assert_not_called()
        assert summary["turns_completed"] == 1
        assert session.game.state["player_offer"] == ["red", "blue"]
        assert session.history[-1]["game_action"]["revision"] == 1
        prompt = provider.generate.call_args.args[0].prompt
        assert '"revision": 1' in prompt
        assert "Visible Game Panel" in prompt
        assert '"narratron_place"' not in prompt
    finally:
        server._adventure_runner_sessions.pop(session.session_id, None)
        session.cleanup()


def test_autoplayer_raw_game_decision_and_clears_previous_move() -> None:
    provider = MagicMock()
    player = AutoPlayer(text_provider=provider)
    provider.generate.return_value.parsed = None
    provider.generate.return_value.text = '```json\n{"game_action":{"name":"select","arguments":{"square":12},"revision":2}}\n```'
    player.decide_action({}, [], 1)
    assert player.game_action.name == "select"
    provider.generate.return_value.text = 'Thought: wait\nAction: I ask about the rules.'
    assert player.decide_action({}, [], 2)[1] == "I ask about the rules."
    assert player.game_action is None


def test_game_turn_waits_for_narratron_response(sample_adventure: Path) -> None:
    game_dir = sample_adventure / "game"
    game_dir.mkdir()
    (game_dir / "game.json").write_text(exchange_definition().model_dump_json(), encoding="utf-8")
    session = AdventureSession("synthetic-test-adventure")
    def run(shared: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            assert '"player_place"' in prompt
            session.story_tool.recorder_module.take_game_action("narratron_place", '{"item":"green"}', 1)
            return GameControlDecision().model_dump_json()
        return "No changes."

    try:
        with patch.object(StorySession, "run", run):
            result = session.take_game_action(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
        assert result["revision"] == 2
        assert session.game.state["narratron_offer"] == ["green"]
        assert session.history[-1]["planning_completed"] is True
        assert session.story_tool.wait_for_pending_events(timeout=0)
    finally:
        session.cleanup()


def test_autoplayer_copies_enabled_control_arguments() -> None:
    provider = MagicMock()
    provider.generate.return_value.parsed = PlayerControlDecision(thought="Select the piece.", game_control=0)
    player = AutoPlayer(text_provider=provider)
    state = {"game": {"revision": 7, "view": [{
        "kind": "column", "props": {}, "children": [
            {"kind": "button", "props": {"label": "Disabled", "disabled": True},
             "action": {"name": "select", "arguments": {"index": 0}}},
            {"kind": "button", "props": {"label": "c3 Red piece"},
             "action": {"name": "select", "arguments": {"index": 42}}},
        ],
    }]}}
    thought, action = player.decide_action(state, [], 1)
    assert thought == "Select the piece."
    assert player.game_action == GameAction(name="select", arguments={"index": 42}, revision=7)
    assert '"index":42' in action
    request = provider.generate.call_args.args[0]
    assert request.response_schema is PlayerControlDecision
    assert "Enabled Game Controls" in request.prompt
    assert "0: {\"label\": \"c3 Red piece\"}" in request.prompt
    provider.generate.return_value.parsed = None
    provider.generate.return_value.text = '```json\n{"thought":"Click it.","action":null,"game_control":0}\n```'
    assert player.decide_action(state, [], 2)[0] == "Click it."
    assert player.game_action == GameAction(name="select", arguments={"index": 42}, revision=7)


def test_game_narration_finishes_before_return_and_state_serializes(sample_adventure: Path) -> None:
    session = AdventureSession("synthetic-test-adventure")
    reaction = {"turn_id": 1, "user_action": "Red moves.", "narration": "Ivory replies.", "dialogue": []}
    character = Character(name="The Caretaker", gender="nonbinary")
    try:
        with (
            patch.object(session.story_tool.response_module, "_resolve_user_action", return_value=reaction) as resolve,
            patch.object(session.story_tool.engine, "_handle_scene_reaction") as handle,
        ):
            decision = GameControlDecision(narration_request=NarrationRequest(user_action="Red moves.", brief="Ivory replies."))
            with patch.object(session.story_tool.recorder_module, "_run_model", return_value=decision):
                session.story_tool._handle_player_game_action(GameAction(name="move", arguments={}, revision=0), {"revision": 1})
                assert session.story_tool.wait_for_pending_events(5)
            assert resolve.call_count == 1
            assert resolve.call_args.kwargs["game_entry"] is True
            assert resolve.call_args.kwargs["nudge"] == "Ivory replies."
            handle.assert_called_once_with(reaction)
        with patch.object(session.story_tool, "get_present_characters", return_value=[character]):
            state = json.loads(json.dumps(session.get_state()))
            assert state["characters"][0]["name"] == "The Caretaker"
    finally:
        session.cleanup()


def test_synchronous_action_uses_production_order_before_deterministic_drain(sample_adventure: Path) -> None:
    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    reaction = {"turn_id": 1, "narration": "The hearing begins.", "dialogue": [], "planning_signals": []}
    order: list[str] = []

    def respond(action: str, nudge: str = "") -> dict[str, JsonValue]:
        order.append("responder")
        return reaction

    def run(shared: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        order.append(role)
        return GameControlDecision().model_dump_json() if role == "recorder" else "No changes."

    try:
        with (
            patch.object(session.story_tool.response_module, "_run_responder_agent", side_effect=respond),
            patch.object(StorySession, "run", run),
        ):
            session._process_user_action_wrapper("  Begin the hearing.  ")
        assert order == ["responder", "recorder", "planner"]
        assert session.story_tool.turn_journal.turns()[0].completed
        assert session.story_tool._story_log[-1]["type"] == "story_response"
    finally:
        session.cleanup()


@pytest.fixture
def sample_adventure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated, synthetic adventure directory for unit testing without depending on disk assets."""
    def run(shared: StorySession, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        if role == "recorder":
            return GameControlDecision().model_dump_json()
        if role == "responder":
            return '{"narration":"The synthetic scene changes."}'
        return "No changes."

    monkeypatch.setattr(StorySession, "run", run)
    adventures_dir = tmp_path / "adventures"
    adventures_dir.mkdir(parents=True, exist_ok=True)
    adv_dir = adventures_dir / "synthetic-test-adventure"
    adv_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": "synthetic-test-adventure",
        "title": "Synthetic Test Adventure",
        "description": "A synthetic adventure package for unit tests.",
        "genre": "Test Genre",
        "tags": ["test"],
    }
    (adv_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    theater_yaml = """
live_agent:
  special_instructions: "Maintain a synthetic test persona for unit testing."
interactive_canvas:
  enabled: true
story_planning:
  adventure_mode: true
  deep_planning:
    enabled: false
  enforce_structured: true
"""
    (adv_dir / "theater.yaml").write_text(theater_yaml.strip(), encoding="utf-8")
    (adv_dir / "planning.yaml").write_text(
        "Synthetic Widget:\n  owner: recorder\n  initial: A synthetic widget for testing.\n"
        "Synthetic Radar:\n  owner: recorder\n  initial: A synthetic radar.\n",
        encoding="utf-8",
    )

    lore_dir = adv_dir / "lore"
    lore_dir.mkdir(parents=True, exist_ok=True)
    (lore_dir / "01_synthetic_lore.txt").write_text("Synthetic lore content about testing rig.", encoding="utf-8")

    monkeypatch.setattr("testlab.adventure_runner.ADVENTURES_DIR", adventures_dir)
    theaters_dir = tmp_path / "theaters"
    theaters_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("testlab.adventure_runner.THEATERS_DIR", theaters_dir)

    return adv_dir


def test_load_adventure_config(sample_adventure):
    config, adv_path, adv_id = load_adventure_config("synthetic-test-adventure")
    assert adv_id == "synthetic-test-adventure"
    assert adv_path.is_dir()
    assert config["story_planning"]["adventure_mode"] is True
    assert "Synthetic Widget" in yaml.safe_load((adv_path / "planning.yaml").read_text(encoding="utf-8"))
    assert config["interactive_canvas"]["enabled"] is True


def test_mock_tool_bundle_behavior_and_logging():
    canvas = MockCanvasState()
    bundle = MockToolBundle(
        canvas_state=canvas,
        available_references=[{"name": "test_ref", "path": "/ref.png"}],
    )

    # 1. References
    refs = bundle.list_references()
    assert len(refs) == 1
    assert refs[0]["name"] == "test_ref"

    # 2. Image
    img_res = bundle.create_image("A funky nebula cruiser", image_name="cruiser_1", effect="gleam3")
    assert "cruiser_1" in img_res
    assert canvas.current_image == "cruiser_1"
    assert canvas.current_image_effect == "gleam3"

    # 3. Music
    music_res = bundle.play_music("bassline_groove")
    assert "bassline_groove" in music_res
    assert canvas.current_music == "bassline_groove"
    assert canvas.music_status == "playing"

    bundle.pause_music()
    assert canvas.music_status == "paused"

    # 4. Chat
    bundle.send_chat_message("Synthesizing bass frequencies...")
    assert canvas.current_thought == "Synthesizing bass frequencies..."

    # 5. Interactive canvas
    a2ui_res = bundle.update_interactive_canvas("Render Groove HUD")
    assert "Groove HUD" in a2ui_res
    assert canvas.last_interactive_canvas_request == "Render Groove HUD"

    # 6. Show Image with reference handle
    show_res = bundle.show_image("cruiser_1", transition="crossfade", effect="gleam3")
    assert "cruiser_1" in show_res
    assert canvas.current_image == "cruiser_1"

    # 7. User Help
    help_res = bundle.user_help_tool("How do I mute music?")
    assert "How do I mute music?" in help_res
    assert canvas.last_user_help_question == "How do I mute music?"

    # 8. Animation
    anim_res = bundle.create_animation("Cruiser jump to warp", "warp_jump")
    assert "warp_jump" in anim_res or "anim_" in anim_res
    assert canvas.active_animation is not None

    # 9. Verify tool logs
    assert len(canvas.tool_logs) >= 8
    logged_tools = [log_entry["tool"] for log_entry in canvas.tool_logs]
    assert "create_image" in logged_tools
    assert "show_image" in logged_tools
    assert "play_music" in logged_tools
    assert "send_chat_message" in logged_tools
    assert "user_help_tool" in logged_tools
    assert "update_interactive_canvas" in logged_tools
    assert "create_animation" in logged_tools


def test_adventure_session_assembly_and_prompt(sample_adventure):
    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        assert session.adventure_id == "synthetic-test-adventure"
        assert session.agent is not None
        assert session.story_tool is not None

        # Verify prompt includes adventure's special instructions
        instruction = session.agent.instruction
        assert "synthetic test persona" in instruction.lower()
        assert "Interactive Canvas" in instruction

        # Verify tool catalog matches services/live_agent.py expectations
        tool_names = [getattr(t, "__name__", str(t)) for t in session.tools]
        assert "create_image" in tool_names
        assert "play_music" in tool_names
        assert "send_chat_message" in tool_names
        assert "user_help_tool" in tool_names
        assert "process_user_action" in tool_names or "_process_user_action_wrapper" in tool_names
        assert "update_interactive_canvas" in tool_names
        assert {"browse_images", "search_image_by_metadata"}.isdisjoint(tool_names)
        assert {"list_references", "search_references"}.issubset(tool_names)
        assert "## Preloaded References Context" in instruction

        # Initial state verification
        state = session.get_state()
        assert len(state["sticky_notes"]) >= 2
        sticky_topics = [s["topic"] for s in state["sticky_notes"]]
        assert "Synthetic Widget" in sticky_topics
        assert "Synthetic Radar" in sticky_topics
    finally:
        session.cleanup()


def test_adventure_session_turn_execution_mocked(sample_adventure):
    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        mock_reaction = {
            "narration": "The bass synthesizer reverberates across the bridge.",
            "scene_reference": "Syncopated Bridge",
            "dialogue": [{"speaker": "Captain Funk", "text": "Groove locked in!"}],
            "manifested_characters": [],
            "planning_signals": [
                "A mysterious funk distress signal may arrive.",
                "The navigation transducer is heating up.",
                "A rival vessel is nearby in swing warp.",
            ],
        }

        # Mock responder and deep planner execution so no live model calls are made.
        with (
            patch.object(
                session.story_tool.response_module,
                "_run_responder_agent",
                return_value=mock_reaction,
            ),
            patch.object(
                session.story_tool.planning_module,
                "_run_deep_planner_agent",
                return_value={"tool_updates": True},
            ),
        ):
            # Simulate ADK runner execution
            mock_events = [
                MagicMock(
                    is_final_response=lambda: True,
                    content=MagicMock(parts=[MagicMock(text="The console hums with vibrant energy. Captain Funk nods.")]),
                )
            ]

            async def fake_run_async(*args, **kwargs):
                # Call tool during turn simulation
                session._process_user_action_wrapper("I power on the transducer console.")
                session.mock_tools.play_music("groove_alpha")
                session.mock_tools.create_image("A gleaming neon synthesizer cockpit", image_name="synth_cockpit")
                for e in mock_events:
                    yield e

            with patch.object(session.runner, "run_async", side_effect=fake_run_async):
                res = session.send_message("I power on the transducer console.")

                assert "turn" in res
                turn = res["turn"]
                assert "Captain Funk" in turn["agent_response"]
                assert "narration" in turn
                assert turn["narration"] != ""
                assert "dialogue" in turn
                assert len(turn["dialogue"]) == 1
                assert turn["dialogue"][0]["speaker"] == "Captain Funk"
                assert turn["dialogue"][0]["text"] == "Groove locked in!"
                assert len(turn["tool_calls"]) == 3
                tool_names = [tc["tool"] for tc in turn["tool_calls"]]
                assert "process_user_action" in tool_names
                assert "play_music" in tool_names
                assert "create_image" in tool_names

                # Check updated state
                assert res["state"]["sticky_notes"] is not None
                assert session.mock_canvas.current_music == "groove_alpha"
                assert session.mock_canvas.music_status == "playing"
    finally:
        session.cleanup()


def test_adventure_runner_lab_api_routes(sample_adventure):
    client = TestClient(app)

    # 1. GET /adventure-runner page
    page = client.get("/adventure-runner")
    assert page.status_code == 200
    assert "Adventure Runner Lab" in page.text
    assert "toggleToolArgs" in page.text
    assert "tool-details-container" in page.text
    assert "formatJsonHtml" in page.text

    # 2. GET index has adventure-runner card
    index = client.get("/")
    assert index.status_code == 200
    assert 'href="/adventure-runner"' in index.text

    # 3. GET /api/adventure-runner/adventures
    advs_res = client.get("/api/adventure-runner/adventures")
    assert advs_res.status_code == 200
    adventures = advs_res.json()["adventures"]
    assert any(a["id"] == "synthetic-test-adventure" for a in adventures)

    # 4. POST /api/adventure-runner/sessions
    create_res = client.post(
        "/api/adventure-runner/sessions",
        json={"adventure_id": "synthetic-test-adventure", "nodes_ahead": 3},
    )
    assert create_res.status_code == 200
    session_data = create_res.json()
    session_id = session_data["id"]
    assert session_data["adventure_id"] == "synthetic-test-adventure"
    assert len(session_data["state"]["sticky_notes"]) >= 2

    # 5. GET /api/adventure-runner/sessions/{id}
    get_res = client.get(f"/api/adventure-runner/sessions/{session_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == session_id

    # 6. POST /api/adventure-runner/sessions/{id}/messages (mocking runner)
    from testlab.server import _adventure_runner_sessions
    active_session = _adventure_runner_sessions[session_id]

    mock_reaction = {
        "narration": "The synth console springs to life.",
        "scene_reference": "Cockpit",
        "dialogue": [],
        "manifested_characters": [],
        "planning_signals": ["Beat 1", "Beat 2", "Beat 3"],
    }

    async def fake_run_async(*args, **kwargs):
        active_session._process_user_action_wrapper("Look around")
        active_session.mock_tools.send_chat_message("Scanning sector")
        yield MagicMock(
            is_final_response=lambda: True,
            content=MagicMock(parts=[MagicMock(text="You observe the glittering dust of the syncopated nebula.")]),
        )

    with (
        patch.object(
            active_session.story_tool.response_module,
            "_run_responder_agent",
            return_value=mock_reaction,
        ),
        patch.object(
            active_session.story_tool.planning_module,
            "_run_deep_planner_agent",
            return_value={"tool_updates": True},
        ),
        patch.object(active_session.runner, "run_async", side_effect=fake_run_async),
    ):
            msg_res = client.post(
                f"/api/adventure-runner/sessions/{session_id}/messages",
                json={"message": "Look around"},
            )
            assert msg_res.status_code == 200
            turn_data = msg_res.json()
            assert "glittering dust" in turn_data["turn"]["agent_response"]
            assert "lore_activity" in turn_data["turn"]
            assert "lore_docs_browsed" in turn_data["turn"]

    # 7. GET /api/adventure-runner/sessions/{id}/lore
    lore_list_res = client.get(f"/api/adventure-runner/sessions/{session_id}/lore")
    assert lore_list_res.status_code == 200
    lore_docs = lore_list_res.json()["documents"]
    assert len(lore_docs) > 0
    first_doc = lore_docs[0]

    # 8. GET /api/adventure-runner/sessions/{id}/lore/{doc_path}
    lore_doc_res = client.get(f"/api/adventure-runner/sessions/{session_id}/lore/{first_doc}")
    assert lore_doc_res.status_code == 200
    assert len(lore_doc_res.json()["content"]) > 0

    # 9. POST /api/adventure-runner/sessions/{id}/reset
    reset_res = client.post(f"/api/adventure-runner/sessions/{session_id}/reset")
    assert reset_res.status_code == 200
    assert len(reset_res.json()["history"]) == 0

    # Cleanup
    active_session.cleanup()


def test_send_message_inside_running_event_loop(sample_adventure):
    import asyncio

    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        mock_reaction = {
            "narration": "The synth console springs to life.",
            "scene_reference": "Cockpit",
            "dialogue": [],
            "manifested_characters": [],
            "planning_signals": ["Beat 1", "Beat 2", "Beat 3"],
        }

        async def fake_run_async(*args, **kwargs):
            session._process_user_action_wrapper("Test inside loop")
            yield MagicMock(
                is_final_response=lambda: True,
                content=MagicMock(parts=[MagicMock(text="Response from agent.")]),
            )

        with (
            patch.object(
                session.story_tool.response_module,
                "_run_responder_agent",
                return_value=mock_reaction,
            ),
            patch.object(
                session.story_tool.planning_module,
                "_run_deep_planner_agent",
                return_value={"tool_updates": True},
            ),
            patch.object(session.runner, "run_async", side_effect=fake_run_async),
        ):
                # Call send_message directly from within an active asyncio event loop
                async def run_in_active_loop():
                    return session.send_message("Test inside loop")

                res = asyncio.run(run_in_active_loop())
                assert "error" not in res
                assert res["turn"]["agent_response"] == "Response from agent."
    finally:
        session.cleanup()


def test_lore_browsing_tracked_in_turn(sample_adventure):
    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        # Simulate StoryTool reading lore during a turn
        session.story_tool.reset_lore_call_counts()
        session.story_tool.read_lore("01_synthetic_lore.txt")
        session.story_tool.search_lore("testing rig")

        browsed = session.story_tool.get_lore_docs_browsed_this_turn()
        assert "01_synthetic_lore.txt" in browsed
        activity = session.story_tool.get_lore_activity_this_turn()
        assert any(a["type"] == "read_file" for a in activity)
        assert any(a["type"] == "search" for a in activity)
    finally:
        session.cleanup()


def test_format_repl_turn():
    # 1. Turn with both narration and dialogue
    turn_full = {
        "agent_response": "The obsidian halls echo with heavy footsteps.",
        "narration": "The obsidian halls echo with heavy footsteps.",
        "dialogue": [
            {"speaker": "Overlord Malakor", "text": "Who disturbs my throne?", "kind": "speech"},
            {"speaker": "Vizier Vespera", "text": "He seems anxious.", "kind": "thought"},
        ],
        "tool_calls": [
            {"tool": "process_user_action", "result": {}},
            {"tool": "show_image", "result": "Displaying image 'throne_room'"},
        ],
    }
    output = format_repl_turn(turn_full)
    assert "Narratron > The obsidian halls echo with heavy footsteps." in output
    assert "[Dialogue]:" in output
    assert '* Overlord Malakor: "Who disturbs my throne?"' in output
    assert "* Vizier Vespera (thought): (He seems anxious.)" in output
    assert "[Peripherals Staged]:" in output
    assert "* show_image: Displaying image 'throne_room'" in output
    # process_user_action should not be printed under peripherals
    assert "* process_user_action" not in output

    # 2. Turn with narration only (no dialogue, no peripherals)
    turn_narration_only = {
        "agent_response": "You silently wait in the shadows.",
        "narration": "You silently wait in the shadows.",
        "dialogue": [],
        "tool_calls": [],
    }
    output_simple = format_repl_turn(turn_narration_only)
    assert output_simple == "Narratron > You silently wait in the shadows."
    assert "[Dialogue]" not in output_simple
    assert "[Peripherals Staged]" not in output_simple


def test_player_turn_decision_and_cleaning():
    decision = PlayerTurnDecision(
        thought="Test if the doors can be kicked open",
        action="I kick the ornate mahogany door with full force.",
    )
    assert decision.thought == "Test if the doors can be kicked open"
    assert "mahogany door" in decision.action

    # Cleaning prefixes and quotes
    assert AutoPlayer._clean_action('Player: "I inspect the shelf."') == "I inspect the shelf."
    assert AutoPlayer._clean_action("Action > I wave my hands.") == "I wave my hands."
    assert AutoPlayer._clean_action("'I whisper to Morvath.'") == "I whisper to Morvath."

    # Fallback JSON parsing
    json_text = '```json\n{"thought": "Try comedic greeting", "action": "I bow dramatically and honk a horn."}\n```'
    th, act = AutoPlayer._parse_fallback(json_text)
    assert th == "Try comedic greeting"
    assert act == "I bow dramatically and honk a horn."

    # Fallback line-based parsing
    line_text = "Thought: Assessing the room layout\nAction: I look around the room for any hidden levers."
    th2, act2 = AutoPlayer._parse_fallback(line_text)
    assert th2 == "Assessing the room layout"
    assert act2 == "I look around the room for any hidden levers."


def test_auto_player_prompt_and_decide_action():
    mock_provider = MagicMock()
    player = AutoPlayer(
        adventure_title="Test Odyssey",
        adventure_description="A test space adventure.",
        instructions="Be zany and try to break the game",
        text_provider=mock_provider,
    )

    # 1. Check prompt assembly on Turn 1 (no history, stickies hidden by default)
    p1 = player._build_prompt(
        session_state={"sticky_notes": [{"topic": "Power Unit", "info": "Active"}]},
        history=[],
        turn_index=1,
    )
    assert "Turn 1" in p1
    assert "Power Unit" not in p1
    assert "The adventure is just beginning" in p1

    # Check with include_stickies=True
    player_with_stickies = AutoPlayer(
        adventure_title="Test Odyssey",
        adventure_description="A test space adventure.",
        instructions="Be zany and try to break the game",
        text_provider=mock_provider,
        include_stickies=True,
    )
    p1_with_stickies = player_with_stickies._build_prompt(
        session_state={"sticky_notes": [{"topic": "Power Unit", "info": "Active"}]},
        history=[],
        turn_index=1,
    )
    assert "Power Unit" in p1_with_stickies

    # 2. Check prompt assembly on Turn 2 (with history)
    mock_history = [
        {
            "turn_index": 1,
            "user_message": "I press the red switch.",
            "narration": "Alarms blare in three octaves.",
            "dialogue": [{"speaker": "Captain Funk", "text": "What did you touch?!"}],
        }
    ]
    p2 = player._build_prompt(
        session_state={"sticky_notes": []},
        history=mock_history,
        turn_index=2,
    )
    assert "Turn 2" in p2
    assert "I press the red switch." in p2
    assert "Captain Funk" in p2

    # 3. Check decide_action when structured generation succeeds
    mock_res = MagicMock()
    mock_res.parsed = PlayerTurnDecision(
        thought="I want to see if the alarm turns off if I press it again.",
        action="I press the red switch a second time with enthusiasm.",
    )
    mock_provider.generate.return_value = mock_res

    thought, action = player.decide_action(
        session_state={},
        history=mock_history,
        turn_index=2,
    )
    assert "alarm turns off" in thought
    assert "second time" in action

    # 4. Check decide_action when structured generation fails and falls back
    mock_provider.generate.side_effect = [
        Exception("Schema generation not supported"),
        MagicMock(text="Thought: Trying another button\nAction: I push the blue button instead."),
    ]
    thought_fb, action_fb = player.decide_action(
        session_state={},
        history=[],
        turn_index=1,
    )
    assert thought_fb == "Trying another button"
    assert action_fb == "I push the blue button instead."


def test_autoplay_logger_incremental_markdown_and_summary(tmp_path):
    log_file = tmp_path / "test_autoplay_log.md"
    logger = AutoplayLogger(
        adventure_id="test-adventure",
        adventure_title="Test Adventure",
        session_id="test_session_123",
        instructions="Be zany and try to break the game",
        agent_model="gemini-3.8-flash",
        planner_model="gemini-3.8-flash",
        autoplay_model="gemini-3.8-flash",
        max_turns=2,
        log_path=log_file,
    )

    # 1. Start log
    initial_state = {
        "sticky_notes": [{"topic": "Secret Map", "info": "Hidden behind the painting."}],
    }
    logger.start_log(initial_state)
    assert log_file.is_file()
    content_start = log_file.read_text(encoding="utf-8")
    assert "# Autoplay Session Log: Test Adventure" in content_start
    assert "Be zany and try to break the game" in content_start
    assert "Secret Map" in content_start

    # 2. Log Turn 1
    turn_1 = {
        "user_message": "I tap the painting three times.",
        "narration": "A hollow click resonates from behind the frame.",
        "dialogue": [{"speaker": "Butler Giles", "text": "Please refrain from touching the heirlooms.", "kind": "speech"}],
        "tool_calls": [{"tool": "play_music", "result": "Playing eerie_notes"}],
    }
    state_after_1 = {
        "sticky_notes": [{"topic": "Secret Safe", "info": "The secret safe is revealed."}],
    }
    logger.log_turn(1, "Testing painting mechanism", "I tap the painting three times.", turn_1, state_after_1)
    content_turn1 = log_file.read_text(encoding="utf-8")
    assert "### Turn 1" in content_turn1
    assert "Testing painting mechanism" in content_turn1
    assert "A hollow click resonates" in content_turn1
    assert "Butler Giles" in content_turn1
    assert "play_music" in content_turn1
    assert "The secret safe is revealed." in content_turn1

    # 3. Finalize
    final_state = {
        "sticky_notes": [{"topic": "Secret Safe", "info": "The secret safe is revealed."}],
        "mock_canvas": {"current_music": "eerie_notes"},
    }
    logger.finalize(final_state, interrupted=False)
    content_final = log_file.read_text(encoding="utf-8")
    assert "## Autoplay Session Summary" in content_final
    assert "Completed Turns**: 1 / 2" in content_final
    assert "Completed successfully" in content_final


def test_autoplay_logger_json(tmp_path):
    json_log = tmp_path / "test_log.json"
    logger = AutoplayLogger(
        adventure_id="test-adv",
        adventure_title="Test Adv",
        session_id="session_json_1",
        instructions="Sneak around",
        agent_model="model_a",
        planner_model="model_p",
        autoplay_model="model_ap",
        max_turns=1,
        log_path=json_log,
    )

    logger.start_log({})
    logger.log_turn(1, "Think sneak", "I creep into the kitchen", {"narration": "Floor creaks"}, {})
    logger.finalize({}, interrupted=False)

    assert json_log.is_file()
    data = json.loads(json_log.read_text(encoding="utf-8"))
    assert data["adventure_id"] == "test-adv"
    assert data["instructions"] == "Sneak around"
    assert len(data["turns"]) == 1
    assert data["turns"][0]["action"] == "I creep into the kitchen"


def test_run_autoplay_execution(sample_adventure, tmp_path):
    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        log_file = tmp_path / "autoplay_run.md"

        mock_turn = {
            "turn_index": 1,
            "user_message": "I investigate the synthetic widget.",
            "agent_response": "The widget spins with synthetic joy.",
            "narration": "The widget spins with synthetic joy.",
            "dialogue": [],
            "tool_calls": [],
        }

        # Mock player decision
        mock_player = MagicMock(spec=AutoPlayer)
        mock_player.instructions = "Be zany and try to break the game"
        mock_player.game_action = None
        mock_player.decide_action.return_value = (
            "Trying to make the widget oscillate",
            "I investigate the synthetic widget.",
        )

        with patch.object(session, "send_message", return_value={"turn": mock_turn, "state": session.get_state()}):
            summary = run_autoplay(
                session=session,
                instructions="Be zany and try to break the game",
                max_turns=2,
                log_path=log_file,
                player=mock_player,
            )

            assert summary["turns_completed"] == 2
            assert summary["max_turns"] == 2
            assert summary["interrupted"] is False
            assert log_file.is_file()

            content = log_file.read_text(encoding="utf-8")
            assert "Autoplay Session Log: Synthetic Test Adventure" in content
            assert "Adventure ID**: `synthetic-test-adventure`" in content
            assert "I investigate the synthetic widget." in content
            assert "The widget spins with synthetic joy." in content
            assert "Completed Turns**: 2 / 2" in content
    finally:
        session.cleanup()


def test_main_cli_autoplay_flags(monkeypatch):
    import sys

    test_args = [
        "adventure_runner.py",
        "--adventure",
        "synthetic-test-adventure",
        "--autoplay_instructions",
        "Be zany and try to break the game",
        "-n",
        "3",
        "--autoplay-delay",
        "0.0",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    with patch("testlab.adventure_runner.AdventureSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.get_state.return_value = {"sticky_notes": []}
        mock_session_cls.return_value = mock_session

        with patch("testlab.adventure_runner.run_autoplay") as mock_run_autoplay:
            mock_run_autoplay.return_value = {"turns_completed": 3}
            ret = main()

            assert ret == 0
            mock_run_autoplay.assert_called_once()
            call_kwargs = mock_run_autoplay.call_args.kwargs
            assert call_kwargs["instructions"] == "Be zany and try to break the game"
            assert call_kwargs["max_turns"] == 3
            assert call_kwargs["include_stickies"] is False
            mock_session.cleanup.assert_called_once()


def test_adventure_session_tool_profile_non_adventure_mode(sample_adventure, monkeypatch):
    # Rewrite theater.yaml with adventure_mode=False
    theater_yaml = """
story_planning:
  adventure_mode: false
live_agent:
  special_instructions: "Narrate an ongoing chronicle."
"""
    (sample_adventure / "theater.yaml").write_text(theater_yaml.strip(), encoding="utf-8")

    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        tool_names = [getattr(t, "__name__", str(t)) for t in session.tools]
        assert "update_sticky_note" in tool_names
        assert "create_or_update_character" in tool_names
        assert "create_or_update_scene" in tool_names
        assert "lookup_character" in tool_names
        assert "clear_characters" in tool_names
        assert "process_user_action" not in tool_names
        assert "_process_user_action_wrapper" not in tool_names
        assert "user_help_tool" in tool_names
        assert {"browse_images", "search_image_by_metadata"}.isdisjoint(tool_names)
        assert {"list_references", "search_references"}.issubset(tool_names)
    finally:
        session.cleanup()


def test_adventure_session_tool_profile_disabled_peripherals(sample_adventure, monkeypatch):
    theater_yaml = """
story_planning:
  adventure_mode: true
image_generation:
  enabled: false
user_help:
  enabled: false
music:
  use_generated_music: true
animation:
  enabled: true
"""
    (sample_adventure / "theater.yaml").write_text(theater_yaml.strip(), encoding="utf-8")

    session = AdventureSession(adventure_id_or_path="synthetic-test-adventure")
    try:
        tool_names = [getattr(t, "__name__", str(t)) for t in session.tools]
        assert "create_image" not in tool_names
        assert "user_help_tool" not in tool_names
        assert "create_music" in tool_names
        assert "create_animation" in tool_names
        assert "play_animation" in tool_names
        assert "browse_animations" in tool_names
        assert "show_image" in tool_names
    finally:
        session.cleanup()

