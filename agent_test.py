import unittest
from unittest.mock import MagicMock, patch

from agent import create_agent


class TestAgentInit(unittest.TestCase):
    @patch("agent.ImageTools")
    @patch("agent.ChatTools")
    @patch("agent.NotesTools")
    @patch("agent.MusicTools")
    @patch("agent.Agent")
    def test_create_agent_calls_list_reference_library_on_init(
        self, mock_agent_cls, mock_music_cls, mock_notes_cls, mock_chat_cls, mock_image_cls
    ):
        mock_image_tools = MagicMock()
        mock_image_tools.list_reference_library.return_value = [
            {
                "name": "hero_character",
                "alias": "hero_character",
                "path": "/path/to/hero_character.png",
                "description": "Hero character reference image",
            }
        ]
        mock_image_cls.return_value = mock_image_tools

        session_id = "test_agent_session"
        agent_inst, tools_dict = create_agent(session_id=session_id)

        # Verify list_reference_library was called immediately during create_agent
        mock_image_tools.list_reference_library.assert_called_once()

        # Verify Agent instruction includes the preloaded reference library context
        mock_agent_cls.assert_called_once()
        _, kwargs = mock_agent_cls.call_args
        self.assertIn("Preloaded Reference Library Context", kwargs["instruction"])
        self.assertIn("hero_character", kwargs["instruction"])
        self.assertIn("/path/to/hero_character.png", kwargs["instruction"])


if __name__ == "__main__":
    unittest.main()
