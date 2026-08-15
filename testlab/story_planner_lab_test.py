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
