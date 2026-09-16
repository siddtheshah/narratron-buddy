"""Tests for session-scoped Narratron agent construction."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, MagicMock, patch

from components.theater_manager import TheaterManager
from services.live_agent import AGENT_INSTRUCTION_TEMPLATE, DeveloperLiveGemini, create_agent


class TestCreateAgent(unittest.TestCase):
    @patch.dict("os.environ", {"GOOGLE_GENAI_USE_ENTERPRISE": "true"}, clear=False)
    @patch("services.live_agent.genai.Client")
    def test_live_model_forces_developer_api_when_enterprise_is_enabled(self, mock_client):
        model = DeveloperLiveGemini(model="gemini-3.1-flash-live-preview")

        model.api_client
        model._live_api_client

        self.assertEqual(mock_client.call_count, 2)
        for call in mock_client.call_args_list:
            self.assertFalse(call.kwargs["enterprise"])

    def test_music_instruction_prefers_reuse_and_requires_scene_and_tone_change(self):
        self.assertIn("Music continuity is the default", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("both** the story has moved to a materially different scene **and** the emotional tone", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("confirmed by at least two distinct narrative events or user actions", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("use_generated_music", AGENT_INSTRUCTION_TEMPLATE)

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
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
        self.assertIsInstance(mock_agent_cls.call_args.kwargs["model"], DeveloperLiveGemini)
        self.assertIs(agent_inst, mock_agent_cls.return_value)

    @patch("services.live_agent.get_playlists_context")
    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_embeds_playlists_without_exposing_a_listing_tool(
        self, mock_agent_cls, mock_bundle_fn, mock_playlists_fn
    ):
        mock_bundle = MagicMock()
        mock_bundle.tools = [
            MagicMock(name="play_music"),
            MagicMock(name="pause_music"),
            MagicMock(name="resume_music"),
        ]
        mock_playlists_fn.return_value = (
            "- Playlist: 'moonlit forest'\n  Description: Quiet, mysterious woodland ambience.\n  Tracks: dusk.mp3"
        )
        mock_bundle_fn.return_value = mock_bundle

        create_agent(theater_id="music_context_theater", config={"music": {"use_generated_music": False}})

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Preloaded Music Playlists Context", instruction)
        self.assertIn("moonlit forest", instruction)
        self.assertNotIn("* list_playlists:", instruction)
        self.assertNotIn("create_music", instruction)

    @patch("services.live_agent.Agent")
    def test_create_agent_reads_playlists_from_theater_filesystem(self, mock_agent_cls):
        with TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            playlist_dir = Path(temp_dir) / "music_context_theater" / "playlists" / "moonlit_forest"
            playlist_dir.mkdir(parents=True)
            (playlist_dir / "description.txt").write_text(
                "Quiet, mysterious woodland ambience.", encoding="utf-8"
            )
            (playlist_dir / "dusk.mp3").write_bytes(b"audio")
            tool_bundle = MagicMock()
            tool_bundle.tools = []

            create_agent(
                theater_id="music_context_theater",
                config={"music": {"use_generated_music": False}},
                tool_bundle=tool_bundle,
                theater_manager=theater_manager,
            )

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Music ID: 'moonlit_forest'", instruction)
        self.assertIn("Quiet, mysterious woodland ambience.", instruction)
        self.assertIn("dusk.mp3", instruction)
        self.assertNotIn("Error loading playlists context", instruction)

    @patch("services.live_agent.get_playlists_context")
    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_includes_create_music_instruction_only_when_enabled(
        self, mock_agent_cls, mock_bundle_fn, mock_playlists_fn
    ):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle
        mock_playlists_fn.return_value = "No music playlists or generated tracks found."

        create_agent(theater_id="music_enabled", config={"music": {"use_generated_music": True}})

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("create_music", instruction)
        self.assertIn("Last resort", instruction)

    @patch("services.live_agent.get_text_response_provider")
    @patch("services.live_agent.ImageTools")
    @patch("services.live_agent.AnimationTools")
    @patch("services.live_agent.ChatTools")
    @patch("services.live_agent.StoryTool")
    @patch("services.live_agent.MusicTools")
    @patch("services.live_agent.Agent")
    def test_create_agent_passes_canvas_state_service_to_every_tool(
        self, mock_agent_cls, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_animation_cls, mock_image_cls, mock_get_text_provider
    ):
        mock_image_cls.return_value.list_references.return_value = []
        canvas_state_service = MagicMock()
        config = {
            "agent": {"model_id": "test-model"},
            "story_planning": {"adventure_mode": True},
        }

        create_agent(
            theater_id="test_agent_theater",
            config=config,
            canvas_state_service=canvas_state_service,
        )

        expected_theater = mock_image_cls.call_args.args[0]
        expected_canvas = mock_image_cls.call_args.kwargs["canvas_manager"]
        mock_image_cls.assert_called_once_with(
            expected_theater, canvas_manager=expected_canvas, adventure_mode=True
        )
        mock_animation_cls.assert_not_called()
        mock_chat_cls.assert_called_once_with(expected_theater, expected_canvas)
        mock_story_planning_cls.assert_called_once_with(
            expected_theater,
            canvas_manager=expected_canvas,
            text_response_provider=ANY,
        )
        mock_music_cls.assert_called_once_with(
            expected_theater,
            expected_canvas,
            music_catalog=ANY,
        )

    @patch("services.live_agent.get_text_response_provider")
    @patch("services.live_agent.ImageTools")
    @patch("services.live_agent.AnimationTools")
    @patch("services.live_agent.ChatTools")
    @patch("services.live_agent.StoryTool")
    @patch("services.live_agent.MusicTools")
    @patch("services.live_agent.Agent")
    def test_animation_tools_are_created_only_when_theater_enables_them(
        self, mock_agent_cls, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_animation_cls, mock_image_cls, mock_get_text_provider
    ):
        mock_image_cls.return_value.list_references.return_value = []
        config = {"animation": {"enabled": True}}

        create_agent(theater_id="animated_theater", config=config)

        mock_animation_cls.assert_called_once_with(
            ANY,
            ANY,
            mock_get_text_provider.return_value,
            ANY,
            video_provider=ANY,
        )

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_animation_instructions_appear_only_when_enabled(self, mock_agent_cls, mock_bundle_fn):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle.preloaded_playlists_context = "No playlists."
        mock_bundle_fn.return_value = mock_bundle

        create_agent(theater_id="animated_prompt", config={"animation": {"enabled": True}})

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("## Animation", instruction)
        self.assertIn("create_animation", instruction)

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
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

