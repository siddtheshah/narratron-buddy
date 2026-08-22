"""Tests for session-scoped Narratron agent construction."""

import unittest
from unittest.mock import ANY, MagicMock, patch

from services.agent import AGENT_INSTRUCTION_TEMPLATE, create_agent


class TestCreateAgent(unittest.TestCase):
    def test_music_instruction_prefers_reuse_and_requires_scene_and_tone_change(self):
        self.assertIn("Music continuity is the default", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("both** the story has moved to a materially different scene **and** the emotional tone", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("confirmed by at least two distinct narrative events or user actions", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("use_generated_music", AGENT_INSTRUCTION_TEMPLATE)

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

    @patch("services.agent.get_playlists_context")
    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
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

    @patch("services.agent.get_playlists_context")
    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
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

    @patch("services.agent.get_text_response_provider")
    @patch("services.agent.ImageTools")
    @patch("services.agent.AnimationTools")
    @patch("services.agent.ChatTools")
    @patch("services.agent.StoryPlanningTools")
    @patch("services.agent.MusicTools")
    @patch("services.agent.Agent")
    def test_create_agent_passes_canvas_state_service_to_every_tool(
        self, mock_agent_cls, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_animation_cls, mock_image_cls, mock_get_text_provider
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
        mock_image_cls.assert_called_once_with(config, **managed_tool_kwargs, adventure_mode=False)
        mock_animation_cls.assert_not_called()
        mock_chat_cls.assert_called_once_with(config.get("chat", {}), **expected_kwargs)
        mock_story_planning_cls.assert_called_once_with(
            config.get("story_planning", {}),
            **managed_tool_kwargs,
            text_response_provider=ANY,
        )
        mock_music_cls.assert_called_once_with(
            config.get("music", {}),
            **managed_tool_kwargs,
            music_catalog=ANY,
        )

    @patch("services.agent.get_text_response_provider")
    @patch("services.agent.ImageTools")
    @patch("services.agent.AnimationTools")
    @patch("services.agent.ChatTools")
    @patch("services.agent.StoryPlanningTools")
    @patch("services.agent.MusicTools")
    @patch("services.agent.Agent")
    def test_animation_tools_are_created_only_when_theater_enables_them(
        self, mock_agent_cls, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_animation_cls, mock_image_cls, mock_get_text_provider
    ):
        mock_image_cls.return_value.list_references.return_value = []
        config = {"animation": {"enabled": True}}

        create_agent(theater_id="animated_theater", config=config)

        mock_animation_cls.assert_called_once_with(
            mock_image_cls.return_value,
            mock_image_cls.return_value._get_image_provider.return_value,
            config["animation"],
        )

    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_animation_instructions_appear_only_when_enabled(self, mock_agent_cls, mock_bundle_fn):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle.preloaded_playlists_context = "No playlists."
        mock_bundle_fn.return_value = mock_bundle

        create_agent(theater_id="animated_prompt", config={"animation": {"enabled": True}})

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("## Animation", instruction)
        self.assertIn("create_triframe", instruction)

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

    @patch("services.agent.get_text_response_provider")
    @patch("services.agent.ImageTools")
    @patch("services.agent.ChatTools")
    @patch("services.agent.StoryPlanningTools")
    @patch("services.agent.MusicTools")
    def test_create_tool_bundle_conditional_create_music(
        self, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_image_cls, mock_get_text_provider
    ):
        from services.agent import create_tool_bundle_for_session
        music_inst = mock_music_cls.return_value
        music_inst.use_generated_music = False
        bundle = create_tool_bundle_for_session("test_t", config={"music": {"use_generated_music": False}})
        tool_funcs = [getattr(t, "func", t) for t in bundle.tools]
        self.assertNotIn(music_inst.create_music, tool_funcs)

        music_inst.use_generated_music = True
        bundle_enabled = create_tool_bundle_for_session("test_t", config={"music": {"use_generated_music": True}})
        tool_funcs_enabled = [getattr(t, "func", t) for t in bundle_enabled.tools]
        self.assertIn(music_inst.create_music, tool_funcs_enabled)

    @patch("services.agent.get_text_response_provider")
    def test_create_tool_bundle_only_includes_observability_tool_when_enabled(self, mock_get_text_provider):
        from services.agent import create_tool_bundle_for_session

        base_config = {
            "image_generation": {"provider": "hybrid-flux-gemini"},
            "music": {"provider": "lyria"},
        }
        disabled = create_tool_bundle_for_session(
            "test_t",
            config={**base_config, "observability_tool": {"enabled": False}},
        )
        enabled = create_tool_bundle_for_session(
            "test_t",
            config={**base_config, "observability_tool": {"enabled": True}},
        )

        disabled_names = [tool.name for tool in disabled.tools]
        enabled_names = [tool.name for tool in enabled.tools]
        self.assertNotIn("request_canvas_observability", disabled_names)
        self.assertIn("request_canvas_observability", enabled_names)

    @patch("services.agent.get_text_response_provider")
    def test_create_tool_bundle_only_includes_interactive_canvas_when_explicitly_enabled(
        self, mock_get_text_provider
    ):
        from services.agent import create_tool_bundle_for_session

        base_config = {
            "story_planning": {"adventure_mode": False},
            "image_generation": {"provider": "hybrid-flux-gemini"},
            "music": {"provider": "lyria"},
        }
        absent = create_tool_bundle_for_session("a2ui_absent", config=base_config)
        disabled = create_tool_bundle_for_session(
            "a2ui_disabled",
            config={**base_config, "interactive_canvas": {"enabled": False}},
        )
        enabled = create_tool_bundle_for_session(
            "a2ui_enabled",
            config={**base_config, "interactive_canvas": {"enabled": True}},
        )

        for bundle in (absent, disabled):
            names = [tool.name for tool in bundle.tools]
            self.assertNotIn("create_interactive_canvas", names)
            self.assertNotIn("update_interactive_canvas", names)
            self.assertNotIn("clear_interactive_canvas", names)
        enabled_names = [tool.name for tool in enabled.tools]
        self.assertNotIn("create_interactive_canvas", enabled_names)
        self.assertIn("update_interactive_canvas", enabled_names)
        self.assertIn("clear_interactive_canvas", enabled_names)

    def test_get_references_context_with_references(self):
        from services.agent import get_references_context
        mock_tool = MagicMock()
        mock_tool.name = "list_references"
        mock_tool.func = MagicMock(return_value=[
            {"name": "hero", "alias": "hero_alias", "description": "Hero desc", "path": "/path/hero.png"}
        ])
        bundle = MagicMock()
        bundle.tools = [mock_tool]
        res = get_references_context(bundle)
        self.assertIn("hero", res)
        self.assertIn("hero_alias", res)
        self.assertIn("Hero desc", res)

    def test_get_references_context_empty(self):
        from services.agent import get_references_context
        bundle = MagicMock()
        bundle.tools = []
        res = get_references_context(bundle)
        self.assertEqual(res, "No preloaded reference images found.")

    def test_get_playlists_context_empty(self):
        from services.agent import get_playlists_context
        mock_theater = MagicMock()
        mock_theater.playlists_dir.return_value = "/nonexistent/playlists"
        mock_theater.music_artifacts_dir.return_value = "/nonexistent/output"
        res = get_playlists_context(mock_theater)
        self.assertEqual(res, "No music playlists or generated tracks found.")

    def test_get_playlists_context_with_files(self):
        import tempfile
        import shutil
        import os
        from services.agent import get_playlists_context
        tmp_dir = tempfile.mkdtemp()
        try:
            playlists_dir = os.path.join(tmp_dir, "playlists")
            output_dir = os.path.join(tmp_dir, "output", "music")
            playlist_sub = os.path.join(playlists_dir, "epic_theme")
            os.makedirs(playlist_sub)
            os.makedirs(output_dir)
            with open(os.path.join(playlist_sub, "description.txt"), "w") as f:
                f.write("Epic soundtrack")
            with open(os.path.join(playlist_sub, "song.mp3"), "w") as f:
                f.write("mp3 data")

            mock_theater = MagicMock()
            mock_theater.playlists_dir.return_value = playlists_dir
            mock_theater.music_artifacts_dir.return_value = output_dir

            res = get_playlists_context(mock_theater)
            self.assertIn("epic_theme", res)
            self.assertIn("Epic soundtrack", res)
            self.assertIn("song.mp3", res)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @patch("services.agent.get_playlists_context")
    @patch("services.agent.create_tool_bundle_for_session")
    @patch("services.agent.Agent")
    def test_create_agent_adventure_mode_instructions(
        self, mock_agent_cls, mock_bundle_fn, mock_playlists_fn
    ):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle
        mock_playlists_fn.return_value = ""

        config = {
            "story_planning": {"adventure_mode": True}
        }
        create_agent(theater_id="adv_agent_theater", config=config)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("## Adventure Mode", instruction)
        self.assertNotIn("* generate_character", instruction)
        self.assertIn("process_user_action", instruction)
        self.assertIn("never speak, act, decide, think, or feel for the player", instruction)
        self.assertIn("AFTER the user action is processed", instruction)
        self.assertIn("CRITICAL TIMING FOR ADVENTURE MODE", instruction)
        self.assertIn("ONLY invoke `create_image` / `show_image` and `play_music` / `create_music` AFTER the user action is processed", instruction)
