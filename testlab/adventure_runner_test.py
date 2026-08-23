"""Unit and integration tests for Local Adventure Runner in Test Lab."""

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from testlab.adventure_runner import (
    AdventureSession,
    MockCanvasState,
    MockToolBundle,
    list_available_adventures,
    load_adventure_config,
)
from testlab.server import app


def test_list_available_adventures_finds_space_funk():
    adventures = list_available_adventures()
    assert len(adventures) >= 1
    ids = [a["id"] for a in adventures]
    assert "space-funk-odyssey" in ids

    sfo = next(a for a in adventures if a["id"] == "space-funk-odyssey")
    assert sfo["title"] == "Space Funk Odyssey"
    assert sfo["lore_count"] > 0
    assert sfo["has_theater_yaml"] is True


def test_load_adventure_config():
    config, adv_path, adv_id = load_adventure_config("space-funk-odyssey")
    assert adv_id == "space-funk-odyssey"
    assert adv_path.is_dir()
    assert config["story_planning"]["adventure_mode"] is True
    assert "Player Character & Groove" in config["story_planning"]["required_stickies"]
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

    pause_res = bundle.pause_music()
    assert canvas.music_status == "paused"

    # 4. Chat
    chat_res = bundle.send_chat_message("Synthesizing bass frequencies...")
    assert canvas.current_thought == "Synthesizing bass frequencies..."

    # 5. Interactive canvas
    a2ui_res = bundle.update_interactive_canvas("Render Groove HUD")
    assert "Groove HUD" in a2ui_res
    assert canvas.last_interactive_canvas_request == "Render Groove HUD"

    # 6. Verify tool logs
    assert len(canvas.tool_logs) >= 5
    logged_tools = [l["tool"] for l in canvas.tool_logs]
    assert "create_image" in logged_tools
    assert "play_music" in logged_tools
    assert "send_chat_message" in logged_tools
    assert "update_interactive_canvas" in logged_tools


def test_adventure_session_assembly_and_prompt():
    session = AdventureSession(adventure_id_or_path="space-funk-odyssey")
    try:
        assert session.adventure_id == "space-funk-odyssey"
        assert session.agent is not None
        assert session.story_planning_tools is not None

        # Verify prompt includes adventure's special instructions
        instruction = session.agent.instruction
        assert "Space Funk Odyssey" in instruction
        assert "Interactive Canvas" in instruction

        # Verify tool catalog matches services/agent.py expectations
        tool_names = [getattr(t, "__name__", str(t)) for t in session.tools]
        assert "create_image" in tool_names
        assert "play_music" in tool_names
        assert "send_chat_message" in tool_names
        assert "_process_user_action_wrapper" in tool_names
        assert "update_interactive_canvas" in tool_names

        # Initial state verification
        state = session.get_state()
        assert len(state["sticky_notes"]) >= 4
        sticky_topics = [s["topic"] for s in state["sticky_notes"]]
        assert "Player Character & Groove" in sticky_topics
        assert "Combat Stats & Synergy" in sticky_topics
    finally:
        session.cleanup()


def test_adventure_session_turn_execution_mocked():
    session = AdventureSession(adventure_id_or_path="space-funk-odyssey")
    try:
        mock_reaction = {
            "narration": "The bass synthesizer reverberates across the bridge.",
            "scene_label": "Syncopated Bridge",
            "dialogue": [{"speaker": "Captain Funk", "text": "Groove locked in!"}],
            "manifested_characters": [],
            "plot_beats": [
                {"plot_beat": "A mysterious funk distress signal arrives."},
                {"plot_beat": "The navigation transducer heats up."},
                {"plot_beat": "A rival vessel drops out of swing warp."},
            ],
        }

        # Mock story planner agent execution so _resolve_user_action processes and commits the reaction
        with patch.object(session.story_planning_tools, "_run_planner_agent", return_value=mock_reaction) as mock_run:
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
                session.mock_tools.create_image("A gleaming neon synthesizer cockpit")
                for e in mock_events:
                    yield e

            with patch.object(session.runner, "run_async", side_effect=fake_run_async):
                res = session.send_message("I power on the transducer console.")

                assert "turn" in res
                turn = res["turn"]
                assert "Captain Funk" in turn["agent_response"]
                assert len(turn["tool_calls"]) == 3
                tool_names = [tc["tool"] for tc in turn["tool_calls"]]
                assert "process_user_action" in tool_names
                assert "play_music" in tool_names
                assert "create_image" in tool_names

                # Check updated state
                state = res["state"]
                assert len(state["plot_beats"]) == 3
                assert session.mock_canvas.current_music == "groove_alpha"
                assert session.mock_canvas.music_status == "playing"
    finally:
        session.cleanup()


def test_adventure_runner_lab_api_routes():
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
    assert any(a["id"] == "space-funk-odyssey" for a in adventures)

    # 4. POST /api/adventure-runner/sessions
    create_res = client.post(
        "/api/adventure-runner/sessions",
        json={"adventure_id": "space-funk-odyssey", "nodes_ahead": 3},
    )
    assert create_res.status_code == 200
    session_data = create_res.json()
    session_id = session_data["id"]
    assert session_data["adventure_id"] == "space-funk-odyssey"
    assert len(session_data["state"]["sticky_notes"]) >= 4

    # 5. GET /api/adventure-runner/sessions/{id}
    get_res = client.get(f"/api/adventure-runner/sessions/{session_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == session_id

    # 6. POST /api/adventure-runner/sessions/{id}/messages (mocking runner)
    from testlab.server import _adventure_runner_sessions
    active_session = _adventure_runner_sessions[session_id]

    mock_reaction = {
        "narration": "The synth console springs to life.",
        "scene_label": "Cockpit",
        "dialogue": [],
        "manifested_characters": [],
        "plot_beats": [{"plot_beat": "Beat 1"}, {"plot_beat": "Beat 2"}, {"plot_beat": "Beat 3"}],
    }

    async def fake_run_async(*args, **kwargs):
        active_session._process_user_action_wrapper("Look around")
        active_session.mock_tools.send_chat_message("Scanning sector")
        yield MagicMock(
            is_final_response=lambda: True,
            content=MagicMock(parts=[MagicMock(text="You observe the glittering dust of the syncopated nebula.")]),
        )

    with patch.object(active_session.story_planning_tools, "_run_planner_agent", return_value=mock_reaction):
        with patch.object(active_session.runner, "run_async", side_effect=fake_run_async):
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


def test_send_message_inside_running_event_loop():
    import asyncio

    session = AdventureSession(adventure_id_or_path="space-funk-odyssey")
    try:
        mock_reaction = {
            "narration": "The synth console springs to life.",
            "scene_label": "Cockpit",
            "dialogue": [],
            "manifested_characters": [],
            "plot_beats": [{"plot_beat": "Beat 1"}, {"plot_beat": "Beat 2"}, {"plot_beat": "Beat 3"}],
        }

        async def fake_run_async(*args, **kwargs):
            session._process_user_action_wrapper("Test inside loop")
            yield MagicMock(
                is_final_response=lambda: True,
                content=MagicMock(parts=[MagicMock(text="Response from agent.")]),
            )

        with patch.object(session.story_planning_tools, "_run_planner_agent", return_value=mock_reaction):
            with patch.object(session.runner, "run_async", side_effect=fake_run_async):
                # Call send_message directly from within an active asyncio event loop
                async def run_in_active_loop():
                    return session.send_message("Test inside loop")

                res = asyncio.run(run_in_active_loop())
                assert "error" not in res
                assert res["turn"]["agent_response"] == "Response from agent."
    finally:
        session.cleanup()


def test_lore_browsing_tracked_in_turn():
    session = AdventureSession(adventure_id_or_path="space-funk-odyssey")
    try:
        # Simulate StoryPlanningTools reading lore during a turn
        session.story_planning_tools.reset_lore_call_counts()
        session.story_planning_tools.read_lore("companions/jax_thumper_vance.txt")
        session.story_planning_tools.search_lore("groove rig")

        browsed = session.story_planning_tools.get_lore_docs_browsed_this_turn()
        assert "companions/jax_thumper_vance.txt" in browsed
        activity = session.story_planning_tools.get_lore_activity_this_turn()
        assert any(a["type"] == "read_file" for a in activity)
        assert any(a["type"] == "search" for a in activity)
    finally:
        session.cleanup()


