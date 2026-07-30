import unittest
from unittest.mock import MagicMock, patch

from testing.base import BaseTestCase
from agent import create_agent


class TestAgentInit(BaseTestCase):
    @patch("agent.ImageTools")
    @patch("agent.ChatTools")
    @patch("agent.NotesTools")
    @patch("agent.MusicTools")
    @patch("agent.Agent")
    def test_create_agent_calls_list_references_on_init(
        self, mock_agent_cls, mock_music_cls, mock_notes_cls, mock_chat_cls, mock_image_cls
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
        mock_image_cls.return_value = mock_image_tools

        theater_id = "test_agent_theater"
        agent_inst = create_agent(theater_id=theater_id)

        # Verify list_references was called immediately during create_agent
        mock_image_tools.list_references.assert_called_once()

        # Verify Agent instruction includes the preloaded references context
        mock_agent_cls.assert_called_once()
        _, kwargs = mock_agent_cls.call_args
        self.assertIn("Preloaded References Context", kwargs["instruction"])
        self.assertIn("hero_character", kwargs["instruction"])
        self.assertIn("/path/to/hero_character.png", kwargs["instruction"])
        self.assertIs(agent_inst, mock_agent_cls.return_value)

    @patch("agent.ImageTools")
    @patch("agent.ChatTools")
    @patch("agent.NotesTools")
    @patch("agent.MusicTools")
    @patch("agent.Agent")
    def test_create_agent_passes_canvas_state_service_to_every_tool(
        self, mock_agent_cls, mock_music_cls, mock_notes_cls, mock_chat_cls, mock_image_cls
    ):
        mock_image_cls.return_value.list_references.return_value = []
        canvas_state_service = MagicMock()
        theater_id = "test_agent_theater"
        config = {"agent": {"model_id": "test-model"}}

        create_agent(
            theater_id=theater_id,
            config=config,
            canvas_state_service=canvas_state_service,
        )

        expected_kwargs = {
            "theater_id": theater_id,
            "canvas_state_service": canvas_state_service,
        }
        mock_image_cls.assert_called_once_with(config.get("image_generation", {}), **expected_kwargs)
        mock_chat_cls.assert_called_once_with(config.get("chat", {}), **expected_kwargs)
        mock_notes_cls.assert_called_once_with(config.get("notes", {}), **expected_kwargs)
        mock_music_cls.assert_called_once_with(config.get("music", {}), **expected_kwargs)


if __name__ == "__main__":
    unittest.main()
