from fastapi.testclient import TestClient

from testlab.server import app


def test_story_planner_lab_routes_create_isolated_session():
    client = TestClient(app)

    index = client.get("/")
    assert index.status_code == 200
    assert "Narratron Test Lab" in index.text
    assert 'href="/story-planner"' in index.text

    page = client.get("/story-planner")
    assert page.status_code == 200
    assert "Story Planner Lab" in page.text
    assert "Callback queue" in page.text

    created = client.post(
        "/api/story-planner/sessions",
        json={"planner_model": "gemini-3.7-flash", "nodes_ahead": 2},
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["events"] == []
    assert payload["state"]["characters"] == []
    assert payload["state"]["plot_beats"] == []

    fetched = client.get(f"/api/story-planner/sessions/{payload['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == payload["id"]


def test_story_planner_lab_submits_action_and_records_event():
    import time
    from unittest.mock import patch

    client = TestClient(app)
    created = client.post(
        "/api/story-planner/sessions",
        json={"planner_model": "gemini-3.7-flash", "nodes_ahead": 2},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    mock_reaction = {
        "narration": "The shadows part as you step forward.",
        "dialogue": [],
        "manifested_characters": [],
        "plot_beats": [
            {"plot_beat": "A door creaks open ahead."},
            {"plot_beat": "Footsteps echo in the distance."},
        ],
    }

    with patch("tools.story_planning_tool.StoryPlanningTools._run_planner_agent", return_value=mock_reaction):
        submitted = client.post(
            f"/api/story-planner/sessions/{session_id}/actions",
            json={"action": "I step into the hallway."},
        )
        assert submitted.status_code == 200
        assert submitted.json()["acknowledgement"]["status"] == "processing"

        # Wait for background thread callback
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            res = client.get(f"/api/story-planner/sessions/{session_id}")
            assert res.status_code == 200
            data = res.json()
            if data["events"]:
                break
            time.sleep(0.05)

        assert len(data["events"]) == 1
        assert data["events"][0]["result"]["narration"] == mock_reaction["narration"]
        assert len(data["state"]["plot_beats"]) == 2

