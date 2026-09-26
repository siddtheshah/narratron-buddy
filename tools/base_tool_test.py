import asyncio
import time
import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.base_tool import BaseTools, with_cooldown, with_cycle_cooldown, single_flight


class SampleTools(BaseTools):
    @with_cooldown(action_desc="showing a sample image", duration=0)
    def show_sample_image(self, file_path: str, transition: str = "crossfade") -> str:
        return "Success"

    @with_cooldown(action_desc="doing action")
    def decorated_tool(self) -> str:
        return "Success"

    @with_cooldown(action_desc="doing quick action", duration=0.1)
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

    @with_cycle_cooldown(action_desc="cycle tool", duration=0.1)
    def cycle_tool(self, value: str) -> str:
        if not hasattr(self, "cycle_calls"):
            self.cycle_calls = []
        self.cycle_calls.append(value)
        return f"Ran {value}"

    @with_cycle_cooldown(duration=0.1)
    async def async_cycle_tool(self, value: str) -> str:
        if not hasattr(self, "async_cycle_calls"):
            self.async_cycle_calls = []
        self.async_cycle_calls.append(value)
        return f"Async ran {value}"

    @with_cycle_cooldown(duration=10.0)
    def cycle_tool_dict_error(self, succeed: bool) -> dict:
        if not succeed:
            return {"error": "Something went wrong"}
        return {"status": "ok"}

    def handle_timeout(self):
        self.timeout_called = True


class TestBaseTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.config = {
            "cooldown_duration": 10.0
        }
        self.theater = MagicMock(theater_id="test_theater")
        self.theater.config = MagicMock(return_value=self.config)
        self.canvas_manager = MagicMock()
        self.base_tools = BaseTools(self.theater, self.canvas_manager)

    def make_sample(self, config: dict) -> SampleTools:
        theater = MagicMock(theater_id="test_theater")
        theater.config = MagicMock(return_value=config)
        return SampleTools(theater, self.canvas_manager)

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
        sample = self.make_sample({"cooldown_duration": 10.0})
        res1 = sample.decorated_tool()
        self.assertEqual(res1, "Success")

        res2 = sample.decorated_tool()
        self.assertIn("decorated_tool is on cooldown", res2)

    def test_with_cooldown_decorator_uses_override_duration(self):
        sample = self.make_sample({"cooldown_duration": 10.0})
        self.assertEqual(sample.quick_tool(), "Success")
        self.assertIn("quick_tool is on cooldown", sample.quick_tool())

        time.sleep(0.2)
        self.assertEqual(sample.quick_tool(), "Success")

    def test_with_cooldown_logs_named_arguments(self):
        sample = self.make_sample({})

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
        sample = self.make_sample({})
        res = sample.fast_single_flight()
        self.assertEqual(res, {"status": "ok"})
        self.assertFalse(sample.is_in_flight("fast_single_flight"))

    def test_single_flight_requires_a_callable_timeout_handler(self):
        with self.assertRaises(TypeError):
            single_flight(on_timeout="handle_timeout")

    def test_single_flight_decorator_timeout_and_callback(self):
        sample = self.make_sample({})
        sample.timeout_called = False
        with self.assertRaises(TimeoutError):
            sample.slow_tool()
        self.assertTrue(sample.timeout_called)
        self.assertFalse(sample.is_in_flight("slow_tool"))

    def test_async_single_flight_decorator_timeout(self):
        sample = self.make_sample({})
        sample.timeout_called = False
        with self.assertRaises(TimeoutError):
            asyncio.run(sample.async_slow_tool())
        self.assertTrue(sample.timeout_called)
        self.assertFalse(sample.is_in_flight("async_slow_tool"))


    def test_with_cycle_cooldown_immediate_and_scheduled_calls(self):
        sample = self.make_sample({})
        # First call executes immediately
        res1 = sample.cycle_tool("A")
        self.assertEqual(res1, "Ran A")
        self.assertEqual(sample.cycle_calls, ["A"])

        # Second call within cooldown is scheduled
        res2 = sample.cycle_tool("B")
        self.assertEqual(res2, "Tool 'cycle_tool' scheduled for next cycle when cooldown expires.")
        self.assertEqual(sample.cycle_calls, ["A"])

        # Third call within cooldown updates parameters instead of creating another entry
        res3 = sample.cycle_tool("C")
        self.assertEqual(res3, "Tool 'cycle_tool' parameters updated for next cycle.")
        self.assertEqual(sample.cycle_calls, ["A"])

        # Wait for cooldown to expire and background timer to execute the pending call
        time.sleep(0.25)
        self.assertEqual(sample.cycle_calls, ["A", "C"])

    def test_with_cycle_cooldown_cancel_pending(self):
        sample = self.make_sample({})
        sample.cycle_tool("A")
        sample.cycle_tool("B")
        self.assertIsNotNone(sample.get_pending_cycle_call("cycle_tool"))

        cancelled = sample.cancel_pending_cycle_call("cycle_tool")
        self.assertTrue(cancelled)
        self.assertIsNone(sample.get_pending_cycle_call("cycle_tool"))

        time.sleep(0.2)
        self.assertEqual(sample.cycle_calls, ["A"])

    def test_with_cycle_cooldown_does_not_fire_expired_cb_when_pending_runs(self):
        sample = self.make_sample({})
        mock_expired = MagicMock()
        sample.on_cooldown_expired = mock_expired

        sample.cycle_tool("A")
        sample.cycle_tool("B")

        # After the first cycle, B runs and starts a new cooldown cycle, so on_cooldown_expired is not fired yet
        time.sleep(0.2)
        self.assertEqual(sample.cycle_calls, ["A", "B"])
        mock_expired.assert_not_called()

        # After the second cycle finishes with no pending calls, on_cooldown_expired is fired
        time.sleep(0.2)
        mock_expired.assert_called_with("cycle_tool")

    def test_with_cycle_cooldown_async(self):
        sample = self.make_sample({})
        res1 = asyncio.run(sample.async_cycle_tool("A"))
        self.assertEqual(res1, "Async ran A")

        res2 = asyncio.run(sample.async_cycle_tool("B"))
        self.assertEqual(res2, "Tool 'async_cycle_tool' scheduled for next cycle when cooldown expires.")

        time.sleep(0.25)
        self.assertEqual(sample.async_cycle_calls, ["A", "B"])

    def test_with_cycle_cooldown_dict_error_clears_cooldown(self):
        sample = self.make_sample({})
        err = sample.cycle_tool_dict_error(False)
        self.assertEqual(err, {"error": "Something went wrong"})
        # Should not be on cooldown
        res = sample.cycle_tool_dict_error(True)
        self.assertEqual(res, {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
