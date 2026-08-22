import logging
from pathlib import Path
from unittest.mock import MagicMock

from jsonschema import Draft202012Validator

from providers import TextResponseResult
from tools.interactive_canvas_tool import (
    A2UISurfaceDraft,
    CANVAS_CATALOG,
    CANVAS_CATALOG_ID,
    CANVAS_COMPONENT_SCHEMA,
    CANVAS_DRAFT_SCHEMA,
    InteractiveCanvasTools,
    SUPPORTED_COMPONENTS,
)


def valid_draft(persistent=False, target_surface_id=""):
    return A2UISurfaceDraft(
        target_surface_id=target_surface_id,
        left_pct=72,
        top_pct=42,
        width_pct=25,
        persistent=persistent,
        components=[
            {"id": "root", "component": "Card", "child": "body"},
            {"id": "body", "component": "Column", "children": ["title", "grab"]},
            {"id": "title", "component": "Text", "text": "A sword glints in the stone.", "variant": "heading"},
            {"id": "grab_label", "component": "Text", "text": "Grab the sword"},
            {
                "id": "grab",
                "component": "Button",
                "child": "grab_label",
                "action": {"event": {"name": "grabSword", "context": {"userAction": "I grab the sword."}}},
            },
        ],
    )


def make_tools(response_factory, config=None, adventure_mode=False):
    canvas = MagicMock()
    canvas.get_latest_state.return_value = {
        "prompt": "A sword in a moonlit stone",
        "narration": "The blade waits.",
        "scene_dialogue": [],
        "sticky_notes": [],
        "interactive_surfaces": [],
    }
    canvas._resolve_active_image.return_value = (None, None, 0, "")
    service = MagicMock()
    service.get.return_value = canvas
    provider = MagicMock()
    # Keep this test double on the provider-neutral path. The concrete Gemini
    # provider exposes `client` for the optional multimodal fast path.
    provider.client = None
    provider.generate.side_effect = lambda request: TextResponseResult(
        text="",
        provider="test",
        model="test-model",
        parsed=response_factory(request.prompt),
    )
    return InteractiveCanvasTools(
        config or {"max_surfaces": 3},
        theater_id="stage",
        canvas_state_service=service,
        text_response_provider=provider,
        adventure_mode=adventure_mode,
    ), canvas


def test_canvas_catalog_is_the_generation_schema_source_of_truth():
    Draft202012Validator.check_schema(CANVAS_CATALOG)
    Draft202012Validator.check_schema(CANVAS_DRAFT_SCHEMA)

    assert CANVAS_CATALOG["catalogId"] == CANVAS_CATALOG_ID
    assert set(CANVAS_CATALOG["components"]) == set(SUPPORTED_COMPONENTS)
    branches = CANVAS_COMPONENT_SCHEMA["anyOf"]
    assert {branch["title"] for branch in branches} == set(SUPPORTED_COMPONENTS)
    assert all("id" in branch["properties"] for branch in branches)
    assert all("component" in branch["properties"] for branch in branches)
    assert all("id" in branch["required"] for branch in branches)
    assert all(
        branch["properties"]["component"] == {"enum": [branch["title"]], "type": "string"}
        for branch in branches
    )
    generation_component = CANVAS_DRAFT_SCHEMA["properties"]["surfaces"]["items"]["properties"]["components"]["items"]
    assert generation_component["type"] == "object"
    assert "anyOf" not in generation_component
    assert generation_component["properties"]["component"] == {
        "type": "string", "enum": sorted(SUPPORTED_COMPONENTS),
    }
    assert {"id", "component", "child", "children", "text", "value", "action"} <= set(
        generation_component["properties"]
    )
    assert "action" in generation_component["required"]


def test_canvas_generation_uses_text_provider_with_catalog_response_schema():
    tools, _ = make_tools(lambda *_: valid_draft())

    result = tools.update_interactive_canvas("Put a sword interaction beside the blade")

    assert result["status"] == "displayed"
    request = tools.text_response_provider.generate.call_args.args[0]
    assert request.model == tools.model
    assert request.response_schema == CANVAS_DRAFT_SCHEMA
    assert request.response_json_schema is None
    assert "one card per character health" in request.prompt
    assert "left_pct` and `top_pct` are the CENTER" in request.prompt
    surface_schema = CANVAS_DRAFT_SCHEMA["properties"]["surfaces"]["items"]
    assert "CENTER" in surface_schema["properties"]["left_pct"]["description"]


def test_canvas_generation_attaches_the_active_canvas_image():
    tools, canvas = make_tools(lambda *_: valid_draft())
    image_path = Path(__file__).resolve().parent.parent / "testlab" / "images" / "trace-knight-sword.png"
    canvas._resolve_active_image.return_value = (None, str(image_path), 0, "Two knights duel.")

    result = tools.update_interactive_canvas("Put health bars beneath the fighters")

    assert result["status"] == "displayed"
    request = tools.text_response_provider.generate.call_args.args[0]
    assert len(request.attachments) == 1
    assert request.attachments[0].data == image_path.read_bytes()
    assert request.attachments[0].mime_type == "image/png"
    assert "An image is attached to this request" in request.prompt


def test_update_interactive_canvas_without_id_creates_a2ui_surface():
    tools, canvas = make_tools(lambda *_: valid_draft())
    result = tools.update_interactive_canvas("Put a sword interaction beside the blade")

    assert result["status"] == "displayed"
    surface = canvas.upsert_interactive_surface.call_args.args[0]
    create = surface["messages"][0]["createSurface"]
    assert surface["placement"] == {"left_pct": 72.0, "top_pct": 42.0, "width_pct": 25.0}
    assert create["surfaceId"] == result["surface_id"]
    assert create["catalogId"] == CANVAS_CATALOG_ID
    assert create["components"][-1]["action"]["event"]["context"]["userAction"] == "I grab the sword."
    canvas.upsert_interactive_surface.assert_called_once()


def test_update_interactive_canvas_creation_preserves_ui_agent_persistence_choice():
    tools, canvas = make_tools(lambda *_: valid_draft(persistent=True))
    result = tools.update_interactive_canvas("Create a persistent health tracker")

    assert result["persistent"] is True
    assert canvas.upsert_interactive_surface.call_args.args[0]["persistent"] is True


def test_update_interactive_canvas_creates_multiple_independent_surfaces_in_one_response():
    batch = {
        "surfaces": [
            {
                "left_pct": 25, "top_pct": 70, "width_pct": 22,
                "components": [
                    {"id": "root", "component": "Card", "child": "knight_1"},
                    {"id": "knight_1", "component": "Progress", "label": "Knight 1", "value": 100},
                ],
            },
            {
                "left_pct": 75, "top_pct": 70, "width_pct": 22,
                "components": [
                    {"id": "root", "component": "Card", "child": "knight_2"},
                    {"id": "knight_2", "component": "Progress", "label": "Knight 2", "value": 100},
                ],
            },
        ],
    }
    tools, canvas = make_tools(lambda *_: batch)

    result = tools.update_interactive_canvas("Create separate health bars for both knights")

    assert result["status"] == "displayed"
    assert result["surface_count"] == 2
    assert len(result["surface_ids"]) == 2
    assert canvas.upsert_interactive_surface.call_count == 2
    placements = [call.args[0]["placement"]["left_pct"] for call in canvas.upsert_interactive_surface.call_args_list]
    assert placements == [25, 75]


def test_generation_action_placeholder_is_removed_from_non_button_components():
    components = InteractiveCanvasTools._validate_components([
        {
            "id": "root", "component": "Progress", "value": 100,
            "action": {"event": {"name": "unused", "context": {"userAction": "unused"}}},
        },
    ])

    assert "action" not in components[0]


def test_update_interactive_canvas_accepts_display_only_progress_grid():
    draft = A2UISurfaceDraft(
        persistent=True,
        components=[
            {"id": "root", "component": "Card", "child": "stats"},
            {"id": "stats", "component": "Grid", "columns": 2, "children": ["health", "mana"]},
            {
                "id": "health", "component": "Progress", "label": "Health",
                "value": {"path": "/health"}, "max": 10, "variant": "health",
            },
            {
                "id": "mana", "component": "Progress", "label": "Mana",
                "value": 6, "max": 10, "variant": "mana",
            },
        ],
        data_model={"health": 8},
    )
    tools, canvas = make_tools(lambda *_: draft)

    result = tools.update_interactive_canvas("Show a persistent health and mana grid")

    assert result["status"] == "displayed"
    components = canvas.upsert_interactive_surface.call_args.args[0]["messages"][0]["createSurface"]["components"]
    assert [component["component"] for component in components] == ["Card", "Grid", "Progress", "Progress"]


def test_update_interactive_canvas_rejects_invalid_grid_columns():
    draft = A2UISurfaceDraft(components=[
        {"id": "root", "component": "Grid", "columns": 12, "children": ["item"]},
        {"id": "item", "component": "Text", "text": "Item"},
    ])
    tools, canvas = make_tools(lambda *_: draft)

    result = tools.update_interactive_canvas("Make an enormous grid")

    assert "error" in result
    canvas.upsert_interactive_surface.assert_not_called()


def test_update_interactive_canvas_normalizes_common_generated_identifier_mistakes():
    draft = A2UISurfaceDraft(components=[
        {"id": "root", "component": "Column", "children": ["health-bar", "take-action"]},
        {"id": "health-bar", "component": "Progress", "label": "Health", "value": 8, "max": 10},
        {"id": "take-label", "component": "Text", "text": "Take action"},
        {
            "id": "take-action", "component": "Button", "child": "take-label",
            "action": {"event": {"name": "take-action", "context": {"userAction": "Take action"}}},
        },
    ])
    tools, canvas = make_tools(lambda *_: draft)

    result = tools.update_interactive_canvas("Show health and an action")

    assert result["status"] == "displayed"
    components = canvas.upsert_interactive_surface.call_args.args[0]["messages"][0]["createSurface"]["components"]
    by_id = {component["id"]: component for component in components}
    assert set(by_id) == {"root", "health_bar", "take_label", "take_action"}
    assert by_id["root"]["children"] == ["health_bar", "take_action"]
    assert by_id["take_action"]["child"] == "take_label"
    assert by_id["take_action"]["action"]["event"]["name"] == "take_action"


def test_update_interactive_canvas_reports_missing_component_id_precisely():
    draft = A2UISurfaceDraft(components=[
        {"id": "root", "component": "Column", "children": []},
        {"component": "Text", "text": "Missing ID"},
    ])
    tools, canvas = make_tools(lambda *_: draft)

    result = tools.update_interactive_canvas("Make invalid UI")

    assert "index 1" in result["error"]
    assert "missing" in result["error"].lower()
    canvas.upsert_interactive_surface.assert_not_called()


def test_update_interactive_canvas_retries_one_invalid_catalog_draft():
    invalid = A2UISurfaceDraft(components=[{"id": "root", "component": "Card"}])
    prompts = []
    tools, canvas = make_tools(lambda prompt: prompts.append(prompt) or (invalid if len(prompts) == 1 else valid_draft()))

    result = tools.update_interactive_canvas("Create a sword interaction")

    assert result["status"] == "displayed"
    assert len(prompts) == 2
    assert "Card component root needs a child" in prompts[1]
    canvas.upsert_interactive_surface.assert_called_once()


def test_update_interactive_canvas_emits_a2ui_updates_and_preserves_user_placement():
    prompts = []
    updated_draft = valid_draft(target_surface_id="health_hud")
    updated_draft.components[2]["text"] = "Health: 7 / 10"
    tools, canvas = make_tools(lambda prompt, *_: prompts.append(prompt) or updated_draft)
    existing = {
        "surface_id": "health_hud",
        "persistent": True,
        "placement": {"left_pct": 83, "top_pct": 12, "width_pct": 25},
        "messages": [{
            "version": "v1.0",
            "createSurface": {
                "surfaceId": "health_hud",
                "components": valid_draft().components,
                "dataModel": {"health": 10},
            },
        }],
    }
    canvas.interactive_surfaces = {"health_hud": existing}
    canvas.get_latest_state.return_value["interactive_surfaces"] = [existing]

    result = tools.update_interactive_canvas("Health fell to seven")

    assert result["status"] == "updated"
    surface = canvas.upsert_interactive_surface.call_args.args[0]
    assert surface["placement"] == existing["placement"]
    assert surface["persistent"] is True
    assert "updateComponents" in surface["messages"][1]
    assert surface["messages"][2]["updateDataModel"]["surfaceId"] == "health_hud"
    assert "health_hud" in prompts[0]
    assert "Health: 7 / 10" not in prompts[0]
    assert "existing_surfaces" in prompts[0]


def test_update_interactive_canvas_rejects_missing_surface():
    tools, canvas = make_tools(lambda *_: valid_draft(target_surface_id="missing"))
    canvas.interactive_surfaces = {}
    result = tools.update_interactive_canvas("Update it")
    assert "error" in result
    canvas.upsert_interactive_surface.assert_not_called()


def test_update_interactive_canvas_creation_rejects_unknown_component():
    draft = valid_draft()
    draft.components[0]["component"] = "Script"
    tools, canvas = make_tools(lambda *_: draft)
    result = tools.update_interactive_canvas("Make it executable")

    assert "error" in result
    canvas.upsert_interactive_surface.assert_not_called()


def test_update_interactive_canvas_creation_requires_player_action():
    draft = valid_draft()
    draft.components[-1]["action"]["event"]["context"] = {}
    tools, canvas = make_tools(lambda *_: draft)
    result = tools.update_interactive_canvas("Make a button")

    assert "error" in result
    canvas.upsert_interactive_surface.assert_not_called()


def test_update_interactive_canvas_has_configurable_cooldown():
    tools, canvas = make_tools(lambda *_: valid_draft())
    tools.cooldown_duration = 60

    first = tools.update_interactive_canvas("Create a status card")
    second = tools.update_interactive_canvas("Change the status card")

    assert first["status"] == "displayed"
    assert isinstance(second, str)
    assert second.startswith("Error: update_interactive_canvas is on cooldown")
    assert canvas.upsert_interactive_surface.call_count == 1


def test_adventure_mode_locks_interactive_canvas_until_story_plan_completes():
    tools, canvas = make_tools(
        lambda *_: valid_draft(),
        {"max_surfaces": 3},
        adventure_mode=True,
    )
    tools.cooldown_duration = 0

    assert tools.adventure_mode
    assert not tools.is_story_plan_completed

    blocked = tools.update_interactive_canvas("Create an action card")

    assert "Waiting for the story planner" in blocked["error"]
    canvas.upsert_interactive_surface.assert_not_called()

    tools.record_story_plan_completed()
    displayed = tools.update_interactive_canvas("Create an action card")

    assert displayed["status"] == "displayed"
    assert not tools.is_story_plan_completed
    blocked_again = tools.clear_interactive_canvas()
    assert "Waiting for the story planner" in blocked_again["error"]


def test_non_adventure_mode_does_not_lock_interactive_canvas_mutations():
    tools, canvas = make_tools(lambda *_: valid_draft())

    assert tools.update_interactive_canvas("Create a status card")["status"] == "displayed"
    assert tools.clear_interactive_canvas()["status"] == "cleared"
    canvas.delete_interactive_surface.assert_called_once_with("all")


def test_interactive_canvas_ignores_theater_model_setting():
    canvas = MagicMock()
    service = MagicMock()
    service.get.return_value = canvas
    provider = MagicMock()
    provider.client = None
    tools = InteractiveCanvasTools(
        {"model": "theater-controlled-model"},
        theater_id="stage",
        canvas_state_service=service,
        text_response_provider=provider,
        model="app-controlled-model",
    )
    assert tools.model == "app-controlled-model"


def test_interactive_canvas_emits_debug_lifecycle(caplog):
    tools, _ = make_tools(lambda *_: valid_draft())
    with caplog.at_level(logging.DEBUG, logger="tools.interactive_canvas_tool"):
        result = tools.update_interactive_canvas("Create a debug status card")

    assert result["status"] == "displayed"
    log_text = caplog.text
    assert "[InteractiveCanvasTools] Update requested" in log_text
    assert "[InteractiveCanvasTools] Captured context" in log_text
    assert "[InteractiveCanvasTools] Dispatching UI generation" in log_text
    assert "[InteractiveCanvasTools] Generated draft" in log_text
    assert "[InteractiveCanvasTools] Draft validated" in log_text
    assert "[InteractiveCanvasTools] Created surface" in log_text
