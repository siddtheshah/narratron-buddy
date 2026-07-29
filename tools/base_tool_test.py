import time
import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.base_tool import BaseTools, with_cooldown


class SampleTools(BaseTools):
    @with_cooldown("doing action")
    def decorated_tool(self) -> str:
        return "Success"


class TestBaseTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.config = {
            "cooldown_duration": 10.0
        }
        self.base_tools = BaseTools(self.config, session_id="test_session")

    def test_cooldown_checking_and_recording(self):
        self.assertIsNone(self.base_tools.check_cooldown("sample_tool", "running sample tool"))

        self.base_tools.record_tool_call("sample_tool")

        err = self.base_tools.check_cooldown("sample_tool", "running sample tool")
        self.assertIsNotNone(err)
        self.assertIn("sample_tool is on cooldown", err)

    def test_expiration_callbacks(self):
        self.base_tools.cooldown_duration = 0.1
        mock_on_expired = MagicMock()
        self.base_tools.on_cooldown_expired = mock_on_expired

        self.base_tools.record_tool_call("play_playlist")
        time.sleep(0.25)

        mock_on_expired.assert_called_with("play_playlist")

    def test_with_cooldown_decorator(self):
        sample = SampleTools({"cooldown_duration": 10.0}, session_id="test_session")
        res1 = sample.decorated_tool()
        self.assertEqual(res1, "Success")

        res2 = sample.decorated_tool()
        self.assertIn("decorated_tool is on cooldown", res2)
        self.assertIn("before doing action", res2)


if __name__ == "__main__":
    unittest.main()
