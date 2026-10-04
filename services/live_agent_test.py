"""Tests for session-scoped Narratron agent construction."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from unittest.mock import ANY, MagicMock, patch

from components.theater_manager import Theater, TheaterManager
from services.live_agent import (
    AGENT_INSTRUCTION_TEMPLATE,
    DeveloperLiveGemini,
    create_agent,
    create_tool_bundle_for_session,
)


def make_test_theater(theater_id: str, config: dict, tmp_path: Optional[Path] = None) -> MagicMock:
    theater = MagicMock(spec=Theater)
    theater.theater_id = theater_id
    theater.config.return_value = config
    base_dir = tmp_path or Path("/tmp")
    theater.directory.return_value = base_dir / "theater"
    theater.output_dir.return_value = base_dir / "output"
    theater.references_dir.return_value = base_dir / "references"
    theater.image_artifacts_dir.return_value = base_dir / "images"
    theater.music_artifacts_dir.return_value = base_dir / "music"
    theater.playlists_dir.return_value = base_dir / "playlists"
    theater.artifacts_dir.return_value = base_dir / "artifacts"
    theater.references.return_value = []
    theater.playlists.return_value = {}
    theater.lore_documents.return_value = []
    return theater


class TestCreateAgent(unittest.TestCase):
    def setUp(self) -> None:
        catalog_patch = patch("services.live_agent.MusicCatalog.from_config")
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)

    @patch("services.live_agent.Agent")
    @patch("services.live_agent.get_app_config")
    def test_create_agent_uses_injected_provider(
        self, mock_config: MagicMock, mock_agent: MagicMock,
    ) -> None:
        from providers import LiveAgentProvider

        mock_config.return_value = {"live_agent": {"provider": "alternate", "model_id": "custom"}}
        provider = MagicMock(spec=LiveAgentProvider)
        provider.id = "alternate"
        bundle = MagicMock()
        bundle.tools = []
        create_agent(make_test_theater("alternate", {}), bundle, provider=provider)
        mock_agent.assert_called_once()
        self.assertIs(mock_agent.call_args.kwargs["model"], provider.create_model.return_value)
        settings = provider.create_model.call_args.args[0]
        self.assertEqual(settings.provider, "alternate")
        self.assertEqual(settings.model_id, "custom")

    @patch.dict("os.environ", {"GOOGLE_GENAI_USE_ENTERPRISE": "true"}, clear=False)
    @patch("providers.gemini_live_agent_provider.genai.Client")
    def test_live_model_forces_developer_api_when_enterprise_is_enabled(self, mock_client: MagicMock) -> None:
        model = DeveloperLiveGemini(model="gemini-3.8-live")

        model.api_client
        model._live_api_client

        self.assertEqual(mock_client.call_count, 2)
        for call in mock_client.call_args_list:
            self.assertFalse(call.kwargs["enterprise"])

    @patch("services.live_agent.get_app_config")
    def test_create_agent_uses_live_model_selection(self, mock_get_app_config: MagicMock) -> None:
        theater = make_test_theater("live_model_selection", {})
        bundle = MagicMock()
        bundle.tools = []
        for settings, expected_model in (
            ({"model_id": "gemini-3.8-live"}, "gemini-3.8-live"),
            ({"model": "custom-live-model"}, "custom-live-model"),
            ({}, "gemini-3.8-live"),
        ):
            with self.subTest(settings=settings):
                mock_get_app_config.return_value = {"live_agent": settings}
                with patch("services.live_agent.Agent") as mock_agent_cls:
                    create_agent(theater, tool_bundle=bundle)
                self.assertEqual(mock_agent_cls.call_args.kwargs["model"].model, expected_model)

    def test_music_instruction_prefers_reuse_and_requires_scene_and_tone_change(self):
        self.assertIn("Music continuity is the default", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("both the scene and emotional tone materially change", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("confirmed by at least two distinct narrative events or user actions", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("use_generated_music", AGENT_INSTRUCTION_TEMPLATE)

    def test_tool_cycle_cooldown_instruction_informs_agent(self):
        self.assertIn(
            "Tools on cooldown will still allow input, but will simply change what will be run in the next tool cycle.",
            AGENT_INSTRUCTION_TEMPLATE,
        )

    def test_user_help_instruction_routes_interface_questions_to_source_grounded_tool(self):
        self.assertIn("call `user_help_tool` immediately", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("Narratron User Help", AGENT_INSTRUCTION_TEMPLATE)
        self.assertIn("Do not call `send_chat_message`", AGENT_INSTRUCTION_TEMPLATE)

    def test_image_tool_character_naming_instruction_informs_agent(self):
        self.assertIn(
            'Use explicit character names in `create_image` prompts so CharacterManager binds their references.',
            AGENT_INSTRUCTION_TEMPLATE,
        )

    @patch("services.live_agent.get_app_config")
    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_calls_list_references_on_init(
        self, mock_agent_cls: MagicMock, mock_bundle_fn: MagicMock, mock_config: MagicMock,
    ) -> None:
        mock_config.return_value = {"live_agent": {"provider": "gemini", "model_id": "gemini-3.8-live"}}
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

        theater = make_test_theater("test_agent_theater", {})
        agent_inst = create_agent(theater, tool_bundle=mock_bundle)

        mock_image_tools.list_references.assert_called_once()
        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Preloaded References Context", instruction)
        self.assertIn("hero_character", instruction)
        self.assertIn("/path/to/hero_character.png", instruction)
        self.assertEqual(mock_agent_cls.call_args.kwargs["model"].model, "gemini-3.8-live")
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
        theater = make_test_theater("music_context_theater", {"music": {"use_generated_music": False}})
        create_agent(theater=theater, tool_bundle=mock_bundle)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("Preloaded Music Playlists Context", instruction)
        self.assertIn("moonlit forest", instruction)
        self.assertNotIn("* list_playlists:", instruction)
        self.assertNotIn("create_music", instruction)

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_reads_playlists_from_theater_filesystem(
        self, mock_agent_cls, mock_bundle_fn
    ):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle

        with TemporaryDirectory() as temp_dir:
            theater_manager = TheaterManager(base_theaters_dir=temp_dir)
            playlist_dir = Path(temp_dir) / "music_context_theater" / "playlists" / "moonlit_forest"
            playlist_dir.mkdir(parents=True)
            (playlist_dir / "description.txt").write_text(
                "Quiet, mysterious woodland ambience.", encoding="utf-8"
            )
            (playlist_dir / "dusk.mp3").write_bytes(b"audio")

            theater = theater_manager.theater("music_context_theater")
            create_agent(theater, tool_bundle=mock_bundle)

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

        theater = make_test_theater("music_enabled", {"music": {"use_generated_music": True}})
        create_agent(theater=theater, tool_bundle=mock_bundle)

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
        config = {
            "agent": {"model_id": "test-model"},
            "story_planning": {"adventure_mode": True},
        }

        theater = make_test_theater("test_agent_theater", config)
        tool_bundle = create_tool_bundle_for_session(theater)
        create_agent(theater, tool_bundle=tool_bundle)

        expected_theater = mock_image_cls.call_args.args[0]
        expected_canvas = mock_image_cls.call_args.kwargs["canvas_manager"]
        mock_image_cls.assert_called_once_with(
            expected_theater,
            canvas_manager=expected_canvas,
            adventure_mode=True,
            character_manager=ANY,
            image_library=ANY,
        )
        mock_animation_cls.assert_not_called()
        mock_chat_cls.assert_called_once_with(expected_theater, expected_canvas)
        mock_story_planning_cls.assert_called_once_with(
            expected_theater,
            canvas_manager=expected_canvas,
            text_response_provider=ANY,
            image_library=ANY,
            character_manager=ANY,
            notepad=ANY,
            lore_library=ANY,
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

        theater = make_test_theater("animated_theater", config)
        tool_bundle = create_tool_bundle_for_session(theater)
        create_agent(theater, tool_bundle=tool_bundle)

        mock_animation_cls.assert_called_once_with(
            ANY,
            ANY,
            mock_get_text_provider.return_value,
            ANY,
            video_provider=ANY,
            character_manager=ANY,
        )

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_animation_instructions_appear_only_when_enabled(self, mock_agent_cls, mock_bundle_fn):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle.preloaded_playlists_context = "No playlists."
        mock_bundle_fn.return_value = mock_bundle

        theater = make_test_theater("animated_prompt", {"animation": {"enabled": True}})
        create_agent(theater=theater, tool_bundle=mock_bundle)

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
        mock_bundle.tools = [reference_tool]
        mock_bundle_fn.return_value = mock_bundle

        theater = make_test_theater(
            "templated_theater",
            {"live_agent": {"special_instructions": "Keep the story suspenseful."}},
        )
        create_agent(theater=theater, tool_bundle=mock_bundle)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertLess(instruction.index("# Job Description"), instruction.index("## Preloaded References Context"))
        self.assertLess(instruction.index("## Preloaded References Context"), instruction.index("## SPECIAL INSTRUCTIONS"))
        self.assertLess(instruction.index("## SPECIAL INSTRUCTIONS"), instruction.index("## Startup"))
        self.assertIn("moonlit_keep", instruction)
        self.assertIn("A castle beneath a full moon.", instruction)
        self.assertIn("Keep the story suspenseful.", instruction)
        self.assertIn("Cooldowns are now lifted. GO!", instruction)

    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_omits_special_instructions_section_when_blank(self, mock_agent_cls, mock_bundle_fn):
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle

        theater = make_test_theater("no_special_instructions", {"live_agent": {}})
        create_agent(theater=theater, tool_bundle=mock_bundle)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertNotIn("## SPECIAL INSTRUCTIONS", instruction)
        self.assertIn("No preloaded reference images found.", instruction)
        self.assertIn("## Startup", instruction)

    @patch("services.live_agent.get_text_response_provider")
    @patch("services.live_agent.ImageTools")
    @patch("services.live_agent.ChatTools")
    @patch("services.live_agent.StoryTool")
    @patch("services.live_agent.MusicTools")
    def test_create_tool_bundle_conditional_create_music(
        self, mock_music_cls, mock_story_planning_cls, mock_chat_cls, mock_image_cls, mock_get_text_provider
    ):
        from services.live_agent import create_tool_bundle_for_session
        music_inst = mock_music_cls.return_value
        music_inst.use_generated_music = False
        theater = make_test_theater("test_t", {"music": {"use_generated_music": False}})
        bundle = create_tool_bundle_for_session(theater)
        tool_funcs = [getattr(t, "func", t) for t in bundle.tools]
        self.assertNotIn(music_inst.create_music, tool_funcs)

        music_inst.use_generated_music = True
        theater_enabled = make_test_theater("test_t", {"music": {"use_generated_music": True}})
        bundle_enabled = create_tool_bundle_for_session(theater_enabled)
        tool_funcs_enabled = [getattr(t, "func", t) for t in bundle_enabled.tools]
        self.assertIn(music_inst.create_music, tool_funcs_enabled)

    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_omits_image_creation_when_disabled(self, mock_get_text_provider):
        from services.live_agent import create_tool_bundle_for_session

        theater = make_test_theater(
            "assets_only_theater",
            {
                "visuals": {"model": "hybrid-flux-gemini"},
                "image_generation": {"enabled": False},
                "music": {"provider": "lyria"},
                "animation": {"enabled": True},
            },
        )
        bundle = create_tool_bundle_for_session(theater)
        tool_names = [tool.name for tool in bundle.tools]
        self.assertNotIn("create_image", tool_names)
        self.assertIn("show_image", tool_names)
        self.assertIn("create_animation", tool_names)

    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_includes_user_help_tool_by_default_and_can_disable_it(self, mock_get_text_provider):
        from services.live_agent import create_tool_bundle_for_session

        base_config = {
            "visuals": {"model": "hybrid-flux-gemini"},
            "music": {"provider": "lyria"},
        }
        enabled = create_tool_bundle_for_session(make_test_theater("help_enabled", base_config))
        disabled = create_tool_bundle_for_session(
            make_test_theater("help_disabled", {**base_config, "user_help": {"enabled": False}})
        )

        enabled_names = [tool.name for tool in enabled.tools]
        disabled_names = [tool.name for tool in disabled.tools]
        self.assertIn("user_help_tool", enabled_names)
        self.assertNotIn("user_help_tool", disabled_names)

    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_only_includes_observability_tool_when_enabled(self, mock_get_text_provider):
        from services.live_agent import create_tool_bundle_for_session

        base_config = {
            "visuals": {"model": "hybrid-flux-gemini"},
            "music": {"provider": "lyria"},
        }
        disabled = create_tool_bundle_for_session(
            make_test_theater("test_t", {**base_config, "observability_tool": {"enabled": False}})
        )
        enabled = create_tool_bundle_for_session(
            make_test_theater("test_t", {**base_config, "observability_tool": {"enabled": True}})
        )

        disabled_names = [tool.name for tool in disabled.tools]
        enabled_names = [tool.name for tool in enabled.tools]
        self.assertNotIn("request_canvas_observability", disabled_names)
        self.assertIn("request_canvas_observability", enabled_names)

    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_only_includes_interactive_canvas_when_explicitly_enabled(
        self, mock_get_text_provider
    ):
        from services.live_agent import create_tool_bundle_for_session

        base_config = {
            "story_planning": {"adventure_mode": False},
            "visuals": {"model": "hybrid-flux-gemini"},
            "music": {"provider": "lyria"},
        }
        absent = create_tool_bundle_for_session(make_test_theater("a2ui_absent", base_config))
        disabled = create_tool_bundle_for_session(
            make_test_theater("a2ui_disabled", {**base_config, "interactive_canvas": {"enabled": False}})
        )
        enabled = create_tool_bundle_for_session(
            make_test_theater("a2ui_enabled", {**base_config, "interactive_canvas": {"enabled": True}})
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

    @patch("services.live_agent.AnimationTools")
    @patch("services.live_agent.StoryTool")
    @patch("services.live_agent.ImageTools")
    @patch("services.live_agent.CharacterManager")
    @patch("services.live_agent.Notepad")
    @patch("services.live_agent.LoreLibrary")
    @patch("services.live_agent.ImageLibrary")
    @patch("services.live_agent.CanvasStateManager")
    @patch("services.live_agent.MusicCatalog.from_config")
    @patch("services.live_agent.get_video_provider")
    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_creates_intermediate_components_first_and_reuses_them(
        self,
        mock_get_text_provider,
        mock_get_video_provider,
        mock_music_catalog_from_config,
        mock_canvas_mgr_cls,
        mock_img_lib_cls,
        mock_lore_lib_cls,
        mock_notepad_cls,
        mock_char_mgr_cls,
        mock_image_tools_cls,
        mock_story_tool_cls,
        mock_animation_tools_cls,
    ):
        from services.live_agent import create_tool_bundle_for_session

        theater = make_test_theater(
            "adv_theater",
            {
                "story_planning": {
                    "adventure_mode": True,
                    "text_provider": "gemini-3",
                    "planner_model": "gemini-3.7-flash",
                },
                "animation": {"enabled": True},
            },
        )

        canvas_mgr = mock_canvas_mgr_cls.return_value
        image_lib = mock_img_lib_cls.return_value
        lore_lib = mock_lore_lib_cls.return_value
        notepad = mock_notepad_cls.return_value
        char_mgr = mock_char_mgr_cls.return_value

        bundle = create_tool_bundle_for_session(theater)
        self.assertIsNotNone(bundle)

        mock_canvas_mgr_cls.assert_called_once_with(theater)
        mock_img_lib_cls.assert_called_once_with(theater)
        mock_lore_lib_cls.assert_called_once_with(theater=theater)
        mock_notepad_cls.assert_called_once_with(
            theater, canvas_manager=canvas_mgr, enforce_structured=True
        )
        mock_char_mgr_cls.assert_called_once()
        self.assertIs(mock_char_mgr_cls.call_args.kwargs["notepad"], notepad)
        self.assertIs(mock_char_mgr_cls.call_args.kwargs["image_library"], image_lib)

        mock_image_tools_cls.assert_called_once_with(
            theater,
            canvas_manager=canvas_mgr,
            adventure_mode=True,
            character_manager=char_mgr,
            image_library=image_lib,
        )
        mock_story_tool_cls.assert_called_once_with(
            theater,
            canvas_manager=canvas_mgr,
            text_response_provider=mock_get_text_provider.return_value,
            image_library=image_lib,
            character_manager=char_mgr,
            notepad=notepad,
            lore_library=lore_lib,
        )
        mock_animation_tools_cls.assert_called_once()
        self.assertIs(mock_animation_tools_cls.call_args.kwargs["character_manager"], char_mgr)

    @patch("services.live_agent.AnimationTools")
    @patch("services.live_agent.StoryTool")
    @patch("services.live_agent.ImageTools")
    @patch("services.live_agent.CharacterManager")
    @patch("services.live_agent.Notepad")
    @patch("services.live_agent.LoreLibrary")
    @patch("services.live_agent.ImageLibrary")
    @patch("services.live_agent.CanvasStateManager")
    @patch("services.live_agent.MusicCatalog.from_config")
    @patch("services.live_agent.get_video_provider")
    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_uses_provided_canvas_manager(
        self,
        mock_get_text_provider: MagicMock,
        mock_get_video_provider: MagicMock,
        mock_music_catalog_from_config: MagicMock,
        mock_canvas_mgr_cls: MagicMock,
        mock_img_lib_cls: MagicMock,
        mock_lore_lib_cls: MagicMock,
        mock_notepad_cls: MagicMock,
        mock_char_mgr_cls: MagicMock,
        mock_image_tools_cls: MagicMock,
        mock_story_tool_cls: MagicMock,
        mock_animation_tools_cls: MagicMock,
    ) -> None:
        from services.live_agent import create_tool_bundle_for_session

        theater = make_test_theater("adv_theater", {})
        provided_canvas = MagicMock()

        bundle = create_tool_bundle_for_session(theater, canvas_manager=provided_canvas)
        self.assertIsNotNone(bundle)
        mock_canvas_mgr_cls.assert_not_called()
        mock_image_tools_cls.assert_called_once_with(
            theater,
            canvas_manager=provided_canvas,
            adventure_mode=False,
            character_manager=mock_char_mgr_cls.return_value,
            image_library=mock_img_lib_cls.return_value,
        )

    def test_get_references_context_with_references(self):
        from services.live_agent import get_references_context
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
        from services.live_agent import get_references_context
        bundle = MagicMock()
        bundle.tools = []
        res = get_references_context(bundle)
        self.assertEqual(res, "No preloaded reference images found.")

    def test_get_playlists_context_empty(self):
        from services.live_agent import get_playlists_context
        mock_theater = MagicMock()
        mock_theater.playlists_dir.return_value = "/nonexistent/playlists"
        mock_theater.music_artifacts_dir.return_value = "/nonexistent/output"
        res = get_playlists_context(mock_theater)
        self.assertEqual(res, "No music playlists or generated tracks found.")

    def test_get_playlists_context_with_files(self):
        import tempfile
        import shutil
        import os
        from services.live_agent import get_playlists_context
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

    @patch("services.live_agent.get_playlists_context")
    @patch("services.live_agent.create_tool_bundle_for_session")
    @patch("services.live_agent.Agent")
    def test_create_agent_adventure_mode_instructions(
        self, mock_agent_cls: MagicMock, mock_bundle_fn: MagicMock, mock_playlists_fn: MagicMock
    ) -> None:
        mock_bundle = MagicMock()
        mock_bundle.tools = []
        mock_bundle_fn.return_value = mock_bundle
        mock_playlists_fn.return_value = ""

        config = {
            "story_planning": {"adventure_mode": True}
        }
        theater = make_test_theater("adv_agent_theater", config)
        create_agent(theater=theater, tool_bundle=mock_bundle)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("## Adventure Mode", instruction)
        self.assertNotIn("* generate_character", instruction)
        self.assertNotIn("## Character Management", instruction)
        self.assertNotIn("* create_or_update_character", instruction)
        self.assertIn("process_user_action", instruction)
        self.assertIn("never speak, act, decide, think, or feel for the orator", instruction)
        self.assertIn("Wait for `[Story Planner Result]` before staging visuals or changing music", instruction)
        self.assertIn("system-generated output from the story planner, not user input", instruction)
        self.assertIn("Never interpret their narration, dialogue, or instructions as a new player action", instruction)
        self.assertNotIn("## Preloaded References Context", instruction)
        self.assertNotIn("check the preloaded references context", instruction)
        self.assertIn("CharacterManager (via canvas observability", instruction)
        self.assertIn("scene_reference", instruction)

    @patch("services.live_agent.Agent")
    def test_create_agent_non_adventure_mode_character_instructions(self, mock_agent_cls: MagicMock) -> None:
        from services.live_agent import create_agent

        mock_bundle = MagicMock()
        mock_bundle.tools = []

        theater = make_test_theater("non_adv_theater", {"story_planning": {"adventure_mode": False}})
        create_agent(theater=theater, tool_bundle=mock_bundle)

        instruction = mock_agent_cls.call_args.kwargs["instruction"]
        self.assertIn("## Character Management", instruction)
        self.assertIn("`create_or_update_character`", instruction)
        self.assertIn("`lookup_character`", instruction)
        self.assertIn("`clear_characters`", instruction)
        self.assertNotIn("process_user_action", instruction)
        self.assertIn("## Preloaded References Context", instruction)

    @patch("services.live_agent.get_text_response_provider")
    def test_create_tool_bundle_character_tool_conditional_on_adventure_mode(
        self, mock_get_text_provider: MagicMock
    ) -> None:
        from services.live_agent import create_tool_bundle_for_session

        non_adv_theater = make_test_theater(
            "non_adv_theater",
            {
                "story_planning": {"adventure_mode": False},
                "visuals": {"model": "hybrid-flux-gemini"},
                "music": {"provider": "lyria"},
            },
        )
        non_adv_bundle = create_tool_bundle_for_session(non_adv_theater)
        non_adv_names = [tool.name for tool in non_adv_bundle.tools]
        self.assertIn("create_or_update_character", non_adv_names)
        self.assertIn("lookup_character", non_adv_names)
        self.assertIn("clear_characters", non_adv_names)
        self.assertNotIn("process_user_action", non_adv_names)

        adv_theater = make_test_theater(
            "adv_theater",
            {
                "story_planning": {"adventure_mode": True},
                "visuals": {"model": "hybrid-flux-gemini"},
                "music": {"provider": "lyria"},
            },
        )
        adv_bundle = create_tool_bundle_for_session(adv_theater)
        adv_names = [tool.name for tool in adv_bundle.tools]
        self.assertNotIn("create_or_update_character", adv_names)
        self.assertNotIn("lookup_character", adv_names)
        self.assertNotIn("clear_characters", adv_names)
        self.assertIn("process_user_action", adv_names)

    def test_create_agent_requires_theater_and_tool_bundle(self):
        theater = make_test_theater("test_agent_theater", {})
        bundle = MagicMock()
        with self.assertRaises(TypeError):
            create_agent(theater)  # type: ignore
        with self.assertRaises(TypeError):
            create_agent(tool_bundle=bundle)  # type: ignore
        with patch("services.live_agent.Agent"):
            agent = create_agent(theater, bundle)
            self.assertIsNotNone(agent)


class TestBuildRunConfig(unittest.TestCase):
    @patch("services.live_agent.get_app_config")
    def test_build_run_config_native_audio_defaults(self, mock_get_app_config: MagicMock) -> None:
        mock_get_app_config.return_value = {
            "live_agent": {"model_id": "gemini-3.8-live"}
        }
        from services.live_agent import build_run_config
        config = {
            "live_agent": {
                "proactivity": True,
                "affective_dialog": True,
                "max_tool_workers": 5,
            }
        }
        run_cfg = build_run_config(config=config)
        self.assertEqual(run_cfg.response_modalities, ["AUDIO"])
        setup = run_cfg.model_dump(exclude_none=True)
        self.assertNotIn("proactivity", setup)
        self.assertNotIn("enable_affective_dialog", setup)
        self.assertNotIn("thinking_config", setup)
        self.assertIsNotNone(run_cfg.input_audio_transcription)
        self.assertIsNotNone(run_cfg.session_resumption)
        self.assertEqual(run_cfg.tool_thread_pool_config.max_workers, 5)


