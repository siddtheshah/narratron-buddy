"""Tests for session-scoped Narratron agent construction."""

import unittest
from unittest.mock import MagicMock, patch

from services.agent import create_agent


class TestCreateAgent(unittest.TestCase):
    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_calls_list_references_on_init(
        self, mock_agent_cls, mock_bundle_fn
    ):
        mock_image_tools = MagicMock()
        mock_image_tools.list_references.return_value = [
            {
                "name": "hero_character",
                "alias": "hero_character",
                "path": "/path/to/hero_character.png",
                "description": "Hero character reference image",
            }
        ]
        mock_tool = MagicMock()
        mock_tool.name = "list_references"
        mock_tool.func = mock_image_tools.list_references
        mock_bundle = MagicMock()
        mock_bundle.tools = [mock_tool]
        mock_bundle_fn.return_value = mock_bundle

        agent_inst = create_agent(theater_id="test_agent_theater")

        mock_image_tools.list_references.assert_called_once()
        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Preloaded References Context", instruction)
        self.assertIn("hero_character", instruction)
        self.assertIn("/path/to/hero_character.png", instruction)
        self.assertIs(agent_inst, mock_agent_cls.return_value)

    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_embeds_playlists_without_exposing_a_listing_tool(
        self, mock_agent_cls, mock_bundle_fn
    ):
        mock_bundle = MagicMock()
        mock_bundle.tools = [
            MagicMock(name="play_playlist"),
            MagicMock(name="pause_playlist"),
            MagicMock(name="resume_playlist"),
        ]
        mock_bundle.preloaded_playlists_context = (
            "- Playlist: 'moonlit forest'\n  Description: Quiet, mysterious woodland ambience.\n  Tracks: dusk.mp3"
        )
        mock_bundle_fn.return_value = mock_bundle

        create_agent(theater_id="music_context_theater")

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Preloaded Music Playlists Context", instruction)
        self.assertIn("moonlit forest", instruction)
        self.assertNotIn("* list_playlists:", instruction)

    @patch("services.agent.ImageTools")
    @patch("services.agent.ChatTools")
    @patch("services.agent.NamedElementTools")
    @patch("services.agent.MusicTools")
    @patch("services.agent.Agent")
    def test_create_agent_passes_canvas_state_service_to_every_tool(
        self, mock_agent_cls, mock_music_cls, mock_named_elements_cls, mock_chat_cls, mock_image_cls
    ):
        mock_image_cls.return_value.list_references.return_value = []
        canvas_state_service = MagicMock()
        config = {"agent": {"model_id": "test-model"}}

        create_agent(
            theater_id="test_agent_theater",
            config=config,
            canvas_state_service=canvas_state_service,
        )

        expected_kwargs = {
            "theater_id": "test_agent_theater",
            "canvas_state_service": canvas_state_service,
        }
        expected_manager = mock_image_cls.call_args.kwargs["theater_manager"]
        managed_tool_kwargs = {**expected_kwargs, "theater_manager": expected_manager}
        mock_image_cls.assert_called_once_with(config, **managed_tool_kwargs)
        mock_chat_cls.assert_called_once_with(config.get("chat", {}), **expected_kwargs)
        mock_named_elements_cls.assert_called_once_with(config.get("named_elements", {}), **expected_kwargs)
        mock_music_cls.assert_called_once_with(config.get("music", {}), **managed_tool_kwargs)

    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_renders_instruction_sections_in_order(self, mock_agent_cls, mock_bundle_fn):
        reference_tool = MagicMock()
        reference_tool.name = "list_references"
        reference_tool.func = MagicMock(return_value=[
            {
                "name": "moonlit_keep",
                "alias": "moonlit_keep",
                "path": "/references/moonlit_keep.png",
                "description": "A castle beneath a full moon.",
            },
        ])
        mock_bundle = MagicMock()
        mock_bundle.tools = [reference_tool]
        mock_bundle_fn.return_value = mock_bundle

        create_agent(
            theater_id="templated_theater",
            config={"agent": {"special_instructions": "Keep the story suspenseful."}},
        )

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertLess(instruction.index("# Objective"), instruction.index("## Preloaded References Context"))
        self.assertLess(instruction.index("## Preloaded References Context"), instruction.index("## SPECIAL INSTRUCTIONS"))
        self.assertLess(instruction.index("## SPECIAL INSTRUCTIONS"), instruction.index("## Startup"))
        self.assertIn("moonlit_keep", instruction)
        self.assertIn("A castle beneath a full moon.", instruction)
        self.assertIn("Keep the story suspenseful.", instruction)
        self.assertIn("Cooldowns are now lifted. GO!", instruction)

    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_omits_special_instructions_section_when_blank(self, mock_agent_cls, mock_bundle_fn):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle

        create_agent(theater_id="no_special_instructions", config={"agent": {}})

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertNotIn("## SPECIAL INSTRUCTIONS", instruction)
        self.assertIn("No preloaded reference images found.", instruction)
        self.assertIn("## Startup", instruction)
