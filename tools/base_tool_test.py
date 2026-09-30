import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from testing.base import BaseTestCase
from tools.base_tool import (
    BaseTools,
    calculate_jaccard_bow_similarity,
    normalize_text_to_tokens,
    single_flight,
    with_cooldown,
    with_cycle_cooldown,
)


class SampleTools(BaseTools):
    def __init__(self, theater: Theater, canvas_manager: CanvasStateManager) -> None:
        super().__init__(theater, canvas_manager)
        self.cycle_calls: list[str] = []
        self.async_cycle_calls: list[str] = []
        self.slow_cycle_calls: list[str] = []
        self.active_executions: int = 0
        self.max_concurrent: int = 0
        self.timeout_called: bool = False

    @with_cooldown(action_desc="showing a sample image", duration=0)
    def show_sample_image(self, file_path: str, transition: str = "crossfade") -> str:
        return "Success"

    @with_cooldown(action_desc="doing action")
    def decorated_tool(self) -> str:
        return "Success"

    @with_cooldown(action_desc="doing quick action", duration=0.1)
    def quick_tool(self) -> str:
        return "Success"

    @with_cooldown(action_desc="doing duplicate action", duration=10.0, swallow_duplicates=True)
    def duplicate_tool(self) -> str:
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
        self.cycle_calls.append(value)
        return f"Ran {value}"

    @with_cycle_cooldown(duration=0.1)
    async def async_cycle_tool(self, value: str) -> str:
        self.async_cycle_calls.append(value)
        return f"Async ran {value}"

    @with_cycle_cooldown(duration=0.05)
    def slow_cycle_tool(self, value: str, sleep_s: float = 0.15) -> str:
        self.active_executions += 1
        if self.active_executions > self.max_concurrent:
            self.max_concurrent = self.active_executions
        try:
            time.sleep(sleep_s)
            self.slow_cycle_calls.append(value)
            return f"Slow ran {value}"
        finally:
            self.active_executions -= 1

    @with_cycle_cooldown(duration=10.0)
    def cycle_tool_dict_error(self, succeed: bool) -> dict:
        if not succeed:
            return {"error": "Something went wrong"}
        return {"status": "ok"}

    def handle_timeout(self) -> None:
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

    def test_with_cycle_cooldown_dict_error_clears_cooldown(self) -> None:
        sample = self.make_sample({})
        err = sample.cycle_tool_dict_error(False)
        self.assertEqual(err, {"error": "Something went wrong"})
        # Should not be on cooldown
        res = sample.cycle_tool_dict_error(True)
        self.assertEqual(res, {"status": "ok"})

    def test_with_cycle_cooldown_defers_to_call_completion_when_longer_than_cooldown(self) -> None:
        sample = self.make_sample({})
        t1 = threading.Thread(target=sample.slow_cycle_tool, args=("A", 0.15))
        t1.start()

        time.sleep(0.02)
        self.assertTrue(sample.is_in_flight("slow_cycle_tool"))

        res_b = sample.slow_cycle_tool("B", 0.01)
        self.assertEqual(
            res_b, "Tool 'slow_cycle_tool' scheduled for next cycle when cooldown expires."
        )

        time.sleep(0.06)
        self.assertEqual(sample.slow_cycle_calls, [])
        self.assertTrue(sample.is_in_flight("slow_cycle_tool"))

        t1.join()
        time.sleep(0.06)
        self.assertEqual(sample.slow_cycle_calls, ["A", "B"])
        self.assertEqual(sample.max_concurrent, 1)

    def test_with_cycle_cooldown_defers_to_cooldown_when_longer_than_call_completion(self) -> None:
        sample = self.make_sample({})
        res_a = sample.cycle_tool("A")
        self.assertEqual(res_a, "Ran A")
        self.assertEqual(sample.cycle_calls, ["A"])

        res_b = sample.cycle_tool("B")
        self.assertEqual(
            res_b, "Tool 'cycle_tool' scheduled for next cycle when cooldown expires."
        )

        time.sleep(0.03)
        self.assertEqual(sample.cycle_calls, ["A"])

        # Wait for cooldown (0.1s + 0.05s buffer) to elapse
        time.sleep(0.20)
        self.assertEqual(sample.cycle_calls, ["A", "B"])

    def test_with_cycle_cooldown_assures_only_one_tool_call_active_at_once(self) -> None:
        sample = self.make_sample({})
        t1 = threading.Thread(target=sample.slow_cycle_tool, args=("A", 0.1))
        t1.start()

        time.sleep(0.02)
        self.assertTrue(sample.is_in_flight("slow_cycle_tool"))

        res_b = sample.slow_cycle_tool("B", 0.05)
        self.assertIn("scheduled for next cycle", res_b)

        t1.join()
        time.sleep(0.1)
        self.assertEqual(sample.slow_cycle_calls, ["A", "B"])
        self.assertEqual(sample.max_concurrent, 1)

    def test_normalize_text_to_tokens(self) -> None:
        tokens1 = normalize_text_to_tokens("I open the wooden chest.")
        self.assertEqual(tokens1, {"open", "wooden", "chest"})

        tokens2 = normalize_text_to_tokens("Open the wooden chests!")
        self.assertEqual(tokens2, {"open", "wooden", "chest"})

        # Stop word fallback for purely stop words
        tokens_stop = normalize_text_to_tokens("I do it")
        self.assertEqual(tokens_stop, {"i", "do", "it"})

    def test_calculate_jaccard_bow_similarity(self):
        s1 = {"open", "wooden", "chest"}
        s2 = {"open", "wooden", "chest"}
        self.assertEqual(calculate_jaccard_bow_similarity(s1, s2), 1.0)

        s3 = {"open", "wooden", "door"}
        # Intersection: {"open", "wooden"} (2), Union: {"open", "wooden", "chest", "door"} (4)
        self.assertEqual(calculate_jaccard_bow_similarity(s1, s3), 0.5)

        self.assertEqual(calculate_jaccard_bow_similarity(set(), set()), 1.0)
        self.assertEqual(calculate_jaccard_bow_similarity(s1, set()), 0.0)

    def test_with_cycle_cooldown_swallows_duplicate_calls(self):
        sample = self.make_sample({})
        # First call executes immediately
        res1 = sample.cycle_tool("Open the treasure chest")
        self.assertEqual(res1, "Ran Open the treasure chest")
        self.assertEqual(sample.cycle_calls, ["Open the treasure chest"])

        # Second call within cooldown with basically identical text is swallowed,
        # but returns standard scheduling confirmation to keep model interaction smooth
        res2 = sample.cycle_tool("I open the treasure chest.")
        self.assertEqual(res2, "Tool 'cycle_tool' scheduled for next cycle when cooldown expires.")
        # Should NOT be queued for next cycle
        self.assertIsNone(sample.get_pending_cycle_call("cycle_tool"))
        self.assertEqual(sample.cycle_calls, ["Open the treasure chest"])

        # Third call with genuinely different parameters is scheduled
        res3 = sample.cycle_tool("Cast a protective shield spell")
        self.assertEqual(res3, "Tool 'cycle_tool' scheduled for next cycle when cooldown expires.")
        self.assertIsNotNone(sample.get_pending_cycle_call("cycle_tool"))

        # Fourth call that duplicates the pending call is also swallowed,
        # returning standard parameter update message
        res4 = sample.cycle_tool("cast protective shield spell")
        self.assertEqual(res4, "Tool 'cycle_tool' parameters updated for next cycle.")
        pending = sample.get_pending_cycle_call("cycle_tool")
        self.assertEqual(pending["args"], ("Cast a protective shield spell",))

        # Wait for cooldown to expire and verify only the genuinely new call executed
        time.sleep(0.25)
        self.assertEqual(
            sample.cycle_calls,
            ["Open the treasure chest", "Cast a protective shield spell"],
        )

    def test_with_cooldown_swallows_duplicate_calls(self):
        sample = self.make_sample({"cooldown_duration": 10.0})
        res1 = sample.duplicate_tool()
        self.assertEqual(res1, "Success")

        # Duplicate call during cooldown returns swallowed confirmation instead of error
        res2 = sample.duplicate_tool()
        self.assertIn("duplicate call ignored", res2)

    def test_single_flight_swallows_duplicate_calls(self):
        sample = self.make_sample({})
        sample.acquire_in_flight("fast_single_flight")
        # In-flight duplicate call is swallowed cleanly
        sample.record_call_args("fast_single_flight", (), {})
        res = sample.fast_single_flight()
        self.assertIn("duplicate call ignored", res)

    def test_with_cycle_cooldown_failure_disables_swallowing(self):
        sample = self.make_sample({"cooldown_duration": 10.0})
        # Initial call fails
        err1 = sample.cycle_tool_dict_error(False)
        self.assertEqual(err1, {"error": "Something went wrong"})

        # Subsequent call with identical arguments must NOT be swallowed because previous call failed
        err2 = sample.cycle_tool_dict_error(False)
        self.assertEqual(err2, {"error": "Something went wrong"})

        # Successful call
        res1 = sample.cycle_tool_dict_error(True)
        self.assertEqual(res1, {"status": "ok"})

        # Call with identical arguments after a successful call SHOULD be swallowed,
        # returning standard scheduling confirmation
        res2 = sample.cycle_tool_dict_error(True)
        self.assertEqual(res2, "Tool 'cycle_tool_dict_error' scheduled for next cycle when cooldown expires.")
        self.assertIsNone(sample.get_pending_cycle_call("cycle_tool_dict_error"))


if __name__ == "__main__":
    unittest.main()

