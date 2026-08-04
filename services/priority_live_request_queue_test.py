import asyncio
import time
import unittest
from unittest.mock import MagicMock

from google.adk.agents.live_request_queue import LiveRequest
from google.genai import types
from services.priority_live_request_queue import PriorityLiveRequestQueue


class TestPriorityLiveRequestQueue(unittest.TestCase):
    def test_audio_priority_over_system_notifications(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.5)

            system_content = types.Content(parts=[types.Part(text="[System Notification] Cooldown expired")])
            audio_blob = types.Blob(mime_type="audio/pcm;rate=16000", data=b"\x00" * 320)

            # Push system notification first, then audio chunk
            queue.send_content(system_content)
            queue.send_realtime(audio_blob)

            # Pop first item -> MUST be audio chunk due to priority
            req1 = await queue.get()
            self.assertIsNotNone(req1.blob)
            self.assertTrue(req1.blob.mime_type.startswith("audio/"))

            # Pop second item -> MUST be system notification (after window expires)
            req2 = await queue.get()
            self.assertIsNotNone(req2.content)
            self.assertIn("Cooldown expired", req2.content.parts[0].text)

        asyncio.run(run_test())

    def test_retention_window_holds_priority(self):
        async def run_test():
            # Use short retention window for fast test (0.2s)
            queue = PriorityLiveRequestQueue(retention_window=0.2)

            audio_blob1 = types.Blob(mime_type="audio/pcm;rate=16000", data=b"audio1")
            system_content = types.Content(parts=[types.Part(text="[System Notification] Canvas updated")])
            audio_blob2 = types.Blob(mime_type="audio/pcm;rate=16000", data=b"audio2")

            queue.send_realtime(audio_blob1)
            queue.send_content(system_content)

            # Pop first item -> audio_blob1
            req1 = await queue.get()
            self.assertEqual(req1.blob.data, b"audio1")

            # While still inside window (< 0.2s), send audio_blob2 asynchronously after 0.05s
            async def push_audio2():
                await asyncio.sleep(0.05)
                queue.send_realtime(audio_blob2)

            asyncio.create_task(push_audio2())

            # Next get() should wait for window and receive audio_blob2 BEFORE system_content
            req2 = await queue.get()
            self.assertEqual(req2.blob.data, b"audio2")

            # Next get() after window expires should return system_content
            req3 = await queue.get()
            self.assertEqual(req3.content.parts[0].text, "[System Notification] Canvas updated")

        asyncio.run(run_test())

    def test_system_notification_yields_after_window_expires(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.1)

            audio_blob = types.Blob(mime_type="audio/pcm;rate=16000", data=b"audio")
            system_content = types.Content(parts=[types.Part(text="[System Notification]")])

            queue.send_realtime(audio_blob)
            queue.send_content(system_content)

            req1 = await queue.get()
            self.assertEqual(req1.blob.data, b"audio")

            start_time = time.monotonic()
            req2 = await queue.get()
            elapsed = time.monotonic() - start_time

            # Should return system_content after ~0.1s retention window
            self.assertIsNotNone(req2.content)
            self.assertGreaterEqual(elapsed, 0.08)

        asyncio.run(run_test())

    def test_system_notification_immediate_when_no_audio_input(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.5)
            system_content = types.Content(parts=[types.Part(text="[System Notification]")])

            queue.send_content(system_content)

            start_time = time.monotonic()
            req = await queue.get()
            elapsed = time.monotonic() - start_time

            # Delivery should be immediate (< 0.05s) when no audio input active
            self.assertLess(elapsed, 0.05)
            self.assertIsNotNone(req.content)

        asyncio.run(run_test())

    def test_record_input_detected_holds_priority(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.2)
            system_content = types.Content(parts=[types.Part(text="System message")])
            audio_blob = types.Blob(mime_type="audio/pcm;rate=16000", data=b"orator_speech")

            # Mic detection triggers priority retention
            queue.record_input_detected()
            queue.send_content(system_content)

            async def delayed_audio():
                await asyncio.sleep(0.05)
                queue.send_realtime(audio_blob)

            asyncio.create_task(delayed_audio())

            req1 = await queue.get()
            self.assertEqual(req1.blob.data, b"orator_speech")

            req2 = await queue.get()
            self.assertEqual(req2.content.parts[0].text, "System message")

        asyncio.run(run_test())

    def test_close_request(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.1)
            queue.close()
            req = await queue.get()
            self.assertTrue(req.close)

        asyncio.run(run_test())

    def test_activity_end_clears_priority_immediately(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=2.0)
            system_content = types.Content(parts=[types.Part(text="Immediate system notification")])

            queue.send_activity_start()
            queue.send_realtime(types.Blob(mime_type="audio/pcm;rate=16000", data=b"speech"))
            queue.send_activity_end()
            queue.send_content(system_content)

            # Pop start signal
            req_start = await queue.get()
            self.assertIsNotNone(req_start.activity_start)

            # Pop speech audio blob
            req_audio = await queue.get()
            self.assertEqual(req_audio.blob.data, b"speech")

            # Pop activity end signal
            req_end = await queue.get()
            self.assertIsNotNone(req_end.activity_end)

            # Next get() should return system notification IMMEDIATELY (< 0.05s) without waiting 2.0s retention window
            start_time = time.monotonic()
            req_sys = await queue.get()
            elapsed = time.monotonic() - start_time

            self.assertLess(elapsed, 0.05)
            self.assertIsNotNone(req_sys.content)
            self.assertEqual(req_sys.content.parts[0].text, "Immediate system notification")

        asyncio.run(run_test())

    def test_system_notification_cannot_interrupt_active_vad(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.01)
            system_content = types.Content(parts=[types.Part(text="Deferred system notification")])

            queue.send_activity_start()
            req_start = await queue.get()
            self.assertIsNotNone(req_start.activity_start)

            queue.send_content(system_content)
            await asyncio.sleep(0.02)  # Let the old timestamp window expire.

            pending_get = asyncio.create_task(queue.get())
            await asyncio.sleep(0.02)
            self.assertFalse(pending_get.done())

            queue.send_realtime(types.Blob(mime_type="audio/pcm;rate=16000", data=b"speech"))
            req_audio = await asyncio.wait_for(pending_get, timeout=0.1)
            self.assertEqual(req_audio.blob.data, b"speech")

            queue.send_activity_end()
            req_end = await queue.get()
            self.assertIsNotNone(req_end.activity_end)

            req_system = await asyncio.wait_for(queue.get(), timeout=0.1)
            self.assertEqual(req_system.content.parts[0].text, "Deferred system notification")

        asyncio.run(run_test())

    def test_arbitrary_send_routes_activity_end_with_audio(self):
        async def run_test():
            queue = PriorityLiveRequestQueue(retention_window=0.01)
            queue.send(LiveRequest(activity_start=types.ActivityStart()))
            queue.send_content(types.Content(parts=[types.Part(text="Deferred")]))
            queue.send(LiveRequest(activity_end=types.ActivityEnd()))

            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)
            self.assertEqual((await queue.get()).content.parts[0].text, "Deferred")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
