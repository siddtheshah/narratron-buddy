import pytest
from unittest.mock import MagicMock
from pathlib import Path

from components.canvas.story_state import (
    CharacterState,
    PlayerCharacterState,
    StoryState,
)
from components.canvas_state import CanvasStateManager
from components.notepad import Notepad
from components.theater_manager import Theater
from components.story.story_snapshot import (
    create_snapshot,
    diff,
)
from components.reference_manager import ReferenceManager
from tools.story.story_tool import StoryTool


def test_story_snapshot_and_diff_built_from_story_state_and_notepad_together(tmp_path: Path) -> None:
    theater = MagicMock(spec=Theater)
    theater.theater_id = "snapshot_theater"
    theater.directory.return_value = tmp_path
    theater.output_dir.return_value = tmp_path / "output"
    theater.config.return_value = {
        "notepad": {
            "initial_stickies": {
                "HUD": "HP: 100",
                "OldNote": "Keep quiet",
            }
        }
    }
    theater.read_planning_schema.return_value = None

    canvas_manager = MagicMock(spec=CanvasStateManager)
    story = StoryState()
    canvas_manager.story = story
    notepad = Notepad(theater, canvas_manager=canvas_manager)
    canvas_manager.notepad = notepad

    prev_snapshot = create_snapshot(
        story,
        notepad,
        player_character=PlayerCharacterState(name="Hero", image_description="A warrior"),
        characters={"Guide": CharacterState(name="Guide", personality="Helpful", motivation="Save realm", quirk="Hums")},
        known_image_names={"castle_gate"},
        notepad_revision=1,
    )

    assert prev_snapshot.stickies["HUD"] == "HP: 100"
    assert prev_snapshot.stickies["OldNote"] == "Keep quiet"
    assert prev_snapshot.player_character is not None
    assert prev_snapshot.player_character.name == "Hero"

    # Now change notepad and story state
    notepad.update_sticky_note("HUD", "HP: 75")
    notepad.update_sticky_note("NewClue", "Ancient key found")
    notepad.replace_from_deep_update(
        MagicMock(sticky_notes=[
            {"topic": "HUD", "info": "HP: 75"},
            {"topic": "NewClue", "info": "Ancient key found"},
        ])
    )

    diff_result = diff(
        previous=prev_snapshot,
        story_state=story,
        notepad=notepad,
        current_pc=PlayerCharacterState(name="Hero", image_description="A wounded warrior", reference="hero_portrait"),
        current_chars={
            "Guide": CharacterState(name="Guide", personality="Helpful", motivation="Run away", quirk="Hums", image_reference="private/guide/2.png"),
            "Villain": CharacterState(name="Villain", personality="Cruel", motivation="Rule all", quirk="Laughs", image_reference="villain_alias"),
        },
        recent_images=[
            {"name": "castle_gate", "alias": "gate"},
            {"name": "dungeon_cell", "alias": "cell", "title": "Cell"},
        ],
        current_notepad_revision=2,
        current_through_turn=3,
    )

    # Check sticky diffs
    assert "+ Added [NewClue]: Ancient key found" in diff_result.sticky_diffs
    assert '~ Updated [HUD]: was "HP: 100" -> now "HP: 75"' in diff_result.sticky_diffs
    assert "- Removed [OldNote]" in diff_result.sticky_diffs

    # Check player character diff
    assert "A wounded warrior" in diff_result.player_character_diff
    assert "hero_portrait" not in diff_result.player_character_diff

    # Check character diffs
    assert any("Updated Guide" in d for d in diff_result.character_diffs)
    assert any("Added Villain" in d for d in diff_result.character_diffs)

    # Check image diffs
    assert len(diff_result.new_images) == 1
    assert diff_result.new_images[0]["name"] == "dungeon_cell"

    # Check deep plan diff
    assert "revision advanced from 1 to 2" in diff_result.notepad_diff

    # Test tuple unpacking support
    sticky_diffs, pc_diff, char_diffs, new_images, dp_diff = diff_result
    assert sticky_diffs == diff_result.sticky_diffs
    assert pc_diff == diff_result.player_character_diff


def test_story_tool_creates_snapshot_and_diff(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    theater = MagicMock(spec=Theater)
    theater.theater_id = "tool_snapshot_theater"
    theater.directory.return_value = tmp_path
    theater.output_dir.return_value = tmp_path / "output"
    theater.config.return_value = {
        "notepad": {
            "initial_stickies": {
                "Quest": "Defeat dragon",
            }
        }
    }
    theater.read_planning_schema.return_value = None

    canvas_manager = MagicMock(spec=CanvasStateManager)
    story = StoryState()
    canvas_manager.story = story
    notepad = Notepad(theater, canvas_manager=canvas_manager)
    canvas_manager.notepad = notepad

    ref_mgr = ReferenceManager(theater=theater, canvas_manager=canvas_manager, story_state=story)
    tool = StoryTool(
        theater,
        canvas_manager=canvas_manager,
        text_response_provider=MagicMock(),
        reference_manager=ref_mgr,
    )

    request.addfinalizer(tool.close)
    snap1 = tool.create_snapshot(
        player_character=PlayerCharacterState(name="Lyra", image_description="Mage"),
        characters={"Boran": CharacterState(name="Boran", personality="Bold")},
        notepad_revision=1,
    )
    assert snap1.stickies["Quest"] == "Defeat dragon"

    tool.notepad.update_sticky_note("Quest", "Dragon befriended")

    diff_res = tool.diff(
        previous=snap1,
        current_pc=PlayerCharacterState(name="Lyra", image_description="Archmage"),
        current_notepad_revision=2,
    )
    assert '~ Updated [Quest]: was "Defeat dragon" -> now "Dragon befriended"' in diff_res.sticky_diffs
    assert "Archmage" in diff_res.player_character_diff

    # Revision defaults come from the shared notepad without planner metadata.
    snap2 = tool.create_snapshot()
    assert snap2.notepad_revision == tool.notepad.revision
    tool.notepad.update_sticky_note("Quest", "Travel with the dragon")
    diff_res = tool.diff(previous=snap2)
    assert f"to {tool.notepad.revision}" in diff_res.notepad_diff
