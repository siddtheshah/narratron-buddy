import asyncio
import time
import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.base_tool import BaseTools, with_cooldown, single_flight


class SampleTools(BaseTools):
    @with_cooldown("showing a sample image", duration=0)
    def show_sample_image(self, file_path: str, transition: str = "crossfade") -> str:
        return "Success"

    @with_cooldown("doing action")
    def decorated_tool(self) -> str:
        return "Success"

    @with_cooldown("doing quick action", duration=0.1)
    def quick_tool(self) -> str:
        return "Success"

    @single_flight(timeout=0.1, on_timeout=lambda tool: tool.handle_timeout())
    def slow_tool(self) -> str:
        time.sleep(0.3)
        return "Done"

    @single_flight(timeout=0.5)
    def fast_single_flight(self) -> dict:
        return {"status": "ok"}

    @single_flight(timeout=0.1, on_timeout=lambda tool: tool.handle_timeout())
    async def async_slow_tool(self) -> str:
        await asyncio.sleep(0.3)
        return "Async Done"

    def handle_timeout(self):
        self.timeout_called = True


class TestBaseTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.config = {
            "cooldown_duration": 10.0
        }
        self.base_tools = BaseTools(self.config, theater_id="test_theater")

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

        self.base_tools.record_tool_call("play_music")
        time.sleep(0.25)

        mock_on_expired.assert_called_with("play_music")

    def test_with_cooldown_decorator(self):
        sample = SampleTools({"cooldown_duration": 10.0}, theater_id="test_theater")
        res1 = sample.decorated_tool()
        self.assertEqual(res1, "Success")

        res2 = sample.decorated_tool()
        self.assertIn("decorated_tool is on cooldown", res2)

    def test_with_cooldown_decorator_uses_override_duration(self):
        sample = SampleTools({"cooldown_duration": 10.0}, theater_id="test_theater")
        self.assertEqual(sample.quick_tool(), "Success")
        self.assertIn("quick_tool is on cooldown", sample.quick_tool())

        time.sleep(0.2)
        self.assertEqual(sample.quick_tool(), "Success")

    def test_with_cooldown_logs_named_arguments(self):
        sample = SampleTools({}, theater_id="test_theater")

        with self.assertLogs("tools.base_tool", level="INFO") as logs:
            self.assertEqual(sample.show_sample_image("scene.png", transition="fade"), "Success")

        self.assertIn("show_sample_image called (theater=test_theater, args={'file_path': 'scene.png', 'transition': 'fade'})", logs.output[0])

    def test_in_flight_tracking(self):
        self.assertFalse(self.base_tools.is_in_flight("my_tool"))
        self.assertTrue(self.base_tools.acquire_in_flight("my_tool"))
        self.assertTrue(self.base_tools.is_in_flight("my_tool"))
        self.assertFalse(self.base_tools.acquire_in_flight("my_tool"))

        self.base_tools.release_in_flight("my_tool")
        self.assertFalse(self.base_tools.is_in_flight("my_tool"))
        self.assertTrue(self.base_tools.acquire_in_flight("my_tool"))

    def test_single_flight_decorator_success(self):
        sample = SampleTools({}, theater_id="test_theater")
        res = sample.fast_single_flight()
        self.assertEqual(res, {"status": "ok"})
        self.assertFalse(sample.is_in_flight("fast_single_flight"))

    def test_single_flight_requires_a_callable_timeout_handler(self):
        with self.assertRaises(TypeError):
            single_flight(on_timeout="handle_timeout")

    def test_single_flight_decorator_timeout_and_callback(self):
        sample = SampleTools({}, theater_id="test_theater")
        sample.timeout_called = False
        with self.assertRaises(TimeoutError):
            sample.slow_tool()
        self.assertTrue(sample.timeout_called)
        self.assertFalse(sample.is_in_flight("slow_tool"))

    def test_async_single_flight_decorator_timeout(self):
        sample = SampleTools({}, theater_id="test_theater")
        sample.timeout_called = False
        with self.assertRaises(TimeoutError):
            asyncio.run(sample.async_slow_tool())
        self.assertTrue(sample.timeout_called)
        self.assertFalse(sample.is_in_flight("async_slow_tool"))


if __name__ == "__main__":
    unittest.main()
