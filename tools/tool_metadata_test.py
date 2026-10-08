"""Verify metadata survives decorators, bound methods, and ADK wrapping."""

from functools import wraps
from inspect import signature

from tools.animation_tool import AnimationTools
from tools.character_tool import CharacterTool
from tools.chat_tool import ChatTools
from tools.image.image_tool import ImageTools
from tools.interactive_canvas_tool import InteractiveCanvasTools
from tools.music_tool import MusicTools
from tools.notepad_tool import NotepadTool
from tools.observability_tool import ObservabilityTools
from tools.story.story_tool import StoryTool
from tools.tool_bundle import ToolBundle
from tools.tool_metadata import annotated_function_tool, terminal
from services.user_help_service import UserHelpService
from tools.user_help_tool import UserHelpTool


def test_terminal_annotation_preserves_signature_and_bound_method_behavior() -> None:
    class Actions:
        @terminal
        def display(self, name: str) -> str:
            """Display an image."""
            return f"Successfully displayed {name}."

        def browse(self) -> list[str]:
            """Find images."""
            return ["forest"]

    actions = Actions()
    bundle = ToolBundle([actions.display, actions.browse])
    assert actions.display.__dict__["terminal"] is True
    assert actions.display("forest") == "Successfully displayed forest."
    assert bundle.tools[0].custom_metadata == {"terminal": True}
    assert bundle.tools[1].custom_metadata == {"terminal": False}
    assert list(signature(actions.display).parameters) == ["name"]
    assert bundle.get_declarations()[0].name == "display"


def test_terminal_attribute_survives_functools_wrappers() -> None:
    @terminal
    def action(name: str) -> str:
        """Complete an action."""
        return name

    @wraps(action)
    def wrapped(name: str) -> str:
        return action(name)

    assert annotated_function_tool(wrapped).custom_metadata == {"terminal": True}
    assert wrapped("forest") == "forest"


def test_finalized_actions_are_terminal_and_lookups_are_not() -> None:
    for action in (
        ImageTools.create_image, ImageTools.show_image, ChatTools.send_chat_message,
        MusicTools.create_music, MusicTools.play_music, MusicTools.pause_music,
        MusicTools.resume_music,
        AnimationTools.create_animation, AnimationTools.play_animation,
        CharacterTool.create_or_update_character, CharacterTool.clear_characters,
        NotepadTool.update_sticky_note,
        StoryTool.process_user_action, StoryTool.update_sticky_note,
        InteractiveCanvasTools.update_interactive_canvas,
        InteractiveCanvasTools.clear_interactive_canvas,
        UserHelpTool.user_help_tool,
    ):
        assert action.__dict__["terminal"] is True
    for lookup in (
        ImageTools.browse_images, ImageTools.search_image_by_metadata,
        ImageTools.list_references, AnimationTools.browse_animations,
        CharacterTool.lookup_character, ObservabilityTools.request_canvas_observability,
        UserHelpService.list_help_files, UserHelpService.search_help_files,
        UserHelpService.read_help_file,
    ):
        assert lookup.__dict__.get("terminal", False) is False
