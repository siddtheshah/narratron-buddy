from unittest.mock import Mock
import pytest

from components.canvas.ui_state import UIState, clamp_surface_placement


def test_clamp_surface_placement_bounds_coordinates() -> None:
    assert clamp_surface_placement(-1, 120) == {"left_pct": 2.0, "top_pct": 98.0}


def test_ui_state_owns_surface_collection() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.interactive_surfaces["hud"] = {"surface_id": "hud"}
    assert list(state.interactive_surfaces) == ["hud"]


def test_ui_state_initial_state() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    assert state.viewer_collab_enabled is False
    assert state.interactive_surfaces == {}
    persist.assert_not_called()
    notify.assert_not_called()


def test_set_viewer_collab_enabled() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    state.set_viewer_collab_enabled(True)
    assert state.viewer_collab_enabled is True
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")

    persist.reset_mock()
    notify.reset_mock()

    state.set_viewer_collab_enabled(False)
    assert state.viewer_collab_enabled is False
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")

    persist.reset_mock()
    notify.reset_mock()

    # Truthy non-bool values are cast to bool
    state.set_viewer_collab_enabled(1)
    assert state.viewer_collab_enabled is True
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_upsert_surface_adds_and_copies_surface() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    original = {"surface_id": "card_1", "title": "Card One"}
    state.upsert_surface(original)

    assert "card_1" in state.interactive_surfaces
    assert state.interactive_surfaces["card_1"] == {"surface_id": "card_1", "title": "Card One"}

    # Mutating original does not mutate internal copy
    original["title"] = "Modified"
    assert state.interactive_surfaces["card_1"]["title"] == "Card One"

    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_upsert_surface_updates_existing_surface() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    state.upsert_surface({"surface_id": "card_1", "title": "Initial"})
    persist.reset_mock()
    notify.reset_mock()

    state.upsert_surface({"surface_id": "card_1", "title": "Updated"})
    assert len(state.interactive_surfaces) == 1
    assert state.interactive_surfaces["card_1"]["title"] == "Updated"
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_upsert_surface_requires_surface_id() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    for invalid in [{}, {"surface_id": ""}, {"surface_id": None}]:
        with pytest.raises(ValueError, match="Interactive surface requires a surface_id."):
            state.upsert_surface(invalid)

    persist.assert_not_called()
    notify.assert_not_called()


def test_upsert_surface_fifo_eviction_at_default_max() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    # Default max_surfaces is 5
    for i in range(1, 6):
        state.upsert_surface({"surface_id": f"s{i}"})

    assert list(state.interactive_surfaces.keys()) == ["s1", "s2", "s3", "s4", "s5"]

    # Inserting 6th evicts the oldest (s1)
    state.upsert_surface({"surface_id": "s6"})
    assert list(state.interactive_surfaces.keys()) == ["s2", "s3", "s4", "s5", "s6"]


def test_upsert_surface_custom_max_surfaces() -> None:
    state = UIState(lambda: None, lambda *_: None)

    state.upsert_surface({"surface_id": "a"}, max_surfaces=2)
    state.upsert_surface({"surface_id": "b"}, max_surfaces=2)
    state.upsert_surface({"surface_id": "c"}, max_surfaces=2)
    assert list(state.interactive_surfaces.keys()) == ["b", "c"]

    # max_surfaces clamped to at least 1
    state.upsert_surface({"surface_id": "d"}, max_surfaces=0)
    assert list(state.interactive_surfaces.keys()) == ["d"]


def test_delete_surface_single_existing() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)
    state.upsert_surface({"surface_id": "s1"})
    state.upsert_surface({"surface_id": "s2"})
    persist.reset_mock()
    notify.reset_mock()

    removed = state.delete_surface("s1")
    assert removed == 1
    assert "s1" not in state.interactive_surfaces
    assert "s2" in state.interactive_surfaces
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_delete_surface_single_not_found() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)
    state.upsert_surface({"surface_id": "s1"})
    persist.reset_mock()
    notify.reset_mock()

    removed = state.delete_surface("non_existent")
    assert removed == 0
    assert "s1" in state.interactive_surfaces
    persist.assert_not_called()
    notify.assert_not_called()


def test_delete_surface_all_with_surfaces() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)
    state.upsert_surface({"surface_id": "s1"})
    state.upsert_surface({"surface_id": "s2"})
    persist.reset_mock()
    notify.reset_mock()

    removed = state.delete_surface("all")
    assert removed == 2
    assert state.interactive_surfaces == {}
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_delete_surface_default_is_all() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({"surface_id": "s1"})
    assert state.delete_surface() == 1
    assert state.interactive_surfaces == {}


def test_delete_surface_all_when_empty() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    removed = state.delete_surface("all")
    assert removed == 0
    persist.assert_not_called()
    notify.assert_not_called()


def test_move_surface_in_bounds() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)
    state.upsert_surface({"surface_id": "hud"})
    persist.reset_mock()
    notify.reset_mock()

    result = state.move_surface("hud", 25.456, 80.123)
    assert result == {"left_pct": 25.46, "top_pct": 80.12}
    assert state.interactive_surfaces["hud"]["placement"] == {"left_pct": 25.46, "top_pct": 80.12}
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_move_surface_clamps_out_of_bounds() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({"surface_id": "hud"})

    # Clamped to min 2.0 and max 98.0
    result = state.move_surface("hud", -10.0, 150.0)
    assert result == {"left_pct": 2.0, "top_pct": 98.0}
    assert state.interactive_surfaces["hud"]["placement"] == {"left_pct": 2.0, "top_pct": 98.0}


def test_move_surface_missing_returns_none() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)

    result = state.move_surface("missing", 50.0, 50.0)
    assert result is None
    persist.assert_not_called()
    notify.assert_not_called()


def test_move_surface_resets_non_dict_placement() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({"surface_id": "hud", "placement": "invalid_placement"})

    result = state.move_surface("hud", 10.0, 20.0)
    assert result == {"left_pct": 10.0, "top_pct": 20.0}
    assert state.interactive_surfaces["hud"]["placement"] == {"left_pct": 10.0, "top_pct": 20.0}


def test_get_interactive_action_from_create_surface() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({
        "surface_id": "inventory",
        "messages": [
            {
                "createSurface": {
                    "components": [
                        {
                            "id": "item_sword",
                            "action": {
                                "event": {
                                    "name": "equip",
                                    "itemId": "sword_01",
                                }
                            },
                        }
                    ]
                }
            }
        ],
    })

    action = state.get_interactive_action("inventory", "item_sword", "equip")
    assert action == {"name": "equip", "itemId": "sword_01"}


def test_get_interactive_action_from_update_components() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({
        "surface_id": "hud",
        "messages": [
            {
                "updateComponents": {
                    "components": [
                        {
                            "id": "potion_btn",
                            "action": {
                                "event": {
                                    "name": "use_potion",
                                    "type": "health",
                                }
                            },
                        }
                    ]
                }
            }
        ],
    })

    action = state.get_interactive_action("hud", "potion_btn", "use_potion")
    assert action == {"name": "use_potion", "type": "health"}


def test_get_interactive_action_overwrites_earlier_component_definition() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({
        "surface_id": "hud",
        "messages": [
            {
                "createSurface": {
                    "components": [
                        {
                            "id": "toggle_btn",
                            "action": {"event": {"name": "turn_on"}},
                        }
                    ]
                }
            },
            {
                "updateComponents": {
                    "components": [
                        {
                            "id": "toggle_btn",
                            "action": {"event": {"name": "turn_off"}},
                        }
                    ]
                }
            },
        ],
    })

    # Old action name returns None
    assert state.get_interactive_action("hud", "toggle_btn", "turn_on") is None
    # New action name returns the updated event
    assert state.get_interactive_action("hud", "toggle_btn", "turn_off") == {"name": "turn_off"}


def test_get_interactive_action_not_found_or_mismatched() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({
        "surface_id": "hud",
        "messages": [
            {
                "createSurface": {
                    "components": [
                        {
                            "id": "btn",
                            "action": {"event": {"name": "click"}},
                        }
                    ]
                }
            }
        ],
    })

    # Non-existent surface
    assert state.get_interactive_action("non_existent", "btn", "click") is None
    # Non-existent component
    assert state.get_interactive_action("hud", "other_btn", "click") is None
    # Mismatched action name
    assert state.get_interactive_action("hud", "btn", "hover") is None


def test_get_interactive_action_resilient_to_malformed_messages() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.upsert_surface({
        "surface_id": "hud",
        "messages": [
            None,
            "not_a_dict",
            {},
            {"createSurface": None},
            {"createSurface": {"components": ["not_a_dict", {}, {"id": ""}, {"id": "no_action"}]}},
        ],
    })

    assert state.get_interactive_action("hud", "no_action", "click") is None
    assert state.get_interactive_action("hud", "nonexistent", "click") is None


def test_serialize_and_load_round_trip() -> None:
    persist, notify = Mock(), Mock()
    state = UIState(persist, notify)
    state.set_viewer_collab_enabled(True)
    state.upsert_surface({"surface_id": "hud", "placement": {"left_pct": 10.0, "top_pct": 20.0}})
    state.upsert_surface({"surface_id": "inventory", "items": ["potion", "shield"]})

    serialized = state.serialize()
    assert serialized == {
        "viewer_collab_enabled": True,
        "interactive_surfaces": [
            {"surface_id": "hud", "placement": {"left_pct": 10.0, "top_pct": 20.0}},
            {"surface_id": "inventory", "items": ["potion", "shield"]},
        ],
    }

    # Load into fresh state
    new_persist, new_notify = Mock(), Mock()
    new_state = UIState(new_persist, new_notify)
    new_state.load(serialized)

    assert new_state.viewer_collab_enabled is True
    assert list(new_state.interactive_surfaces.keys()) == ["hud", "inventory"]
    assert new_state.interactive_surfaces["hud"] == {"surface_id": "hud", "placement": {"left_pct": 10.0, "top_pct": 20.0}}
    assert new_state.interactive_surfaces["inventory"] == {"surface_id": "inventory", "items": ["potion", "shield"]}
    assert new_state.serialize() == serialized

    # Loading does not trigger persist or notify callbacks
    new_persist.assert_not_called()
    new_notify.assert_not_called()


def test_load_handles_empty_or_invalid_payload() -> None:
    state = UIState(lambda: None, lambda *_: None)
    state.set_viewer_collab_enabled(True)
    state.upsert_surface({"surface_id": "existing"})

    # Loading empty dict resets to defaults
    state.load({})
    assert state.viewer_collab_enabled is False
    assert state.interactive_surfaces == {}

    # Invalid non-list surfaces resets to empty dict
    state.load({"viewer_collab_enabled": True, "interactive_surfaces": "not_a_list"})
    assert state.viewer_collab_enabled is True
    assert state.interactive_surfaces == {}

    # Filtering out malformed items without surface_id
    state.load({
        "interactive_surfaces": [
            "string_item",
            {},
            {"surface_id": ""},
            {"surface_id": None},
            {"surface_id": "valid_surface", "data": 123},
        ]
    })
    assert list(state.interactive_surfaces.keys()) == ["valid_surface"]
    assert state.interactive_surfaces["valid_surface"]["data"] == 123
