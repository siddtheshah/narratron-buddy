from unittest.mock import MagicMock

from tools.interactive_canvas_tool import A2UISurfaceDraft, CANVAS_CATALOG_ID, InteractiveCanvasTools


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


def make_tools(generator):
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
    return InteractiveCanvasTools(
        {"max_surfaces": 3}, theater_id="stage", canvas_state_service=service, generator=generator
    ), canvas


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
