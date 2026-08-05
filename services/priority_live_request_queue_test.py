import asyncio
import unittest

from google.adk.agents.live_request_queue import LiveRequest
from google.genai import types
from services.priority_live_request_queue import PriorityLiveRequestQueue


class TestPriorityLiveRequestQueue(unittest.TestCase):
    def test_non_audio_is_delivered_before_unframed_audio(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()

            system_content = types.Content(parts=[types.Part(text="[System Notification] Cooldown expired")])
            audio_blob = types.Blob(mime_type="audio/pcm;rate=16000", data=b"\x00" * 320)

            queue.send_content(system_content)
            queue.send_realtime(audio_blob)

            req1 = await queue.get()
            self.assertIn("Cooldown expired", req1.content.parts[0].text)

            req2 = await queue.get()
            self.assertTrue(req2.blob.mime_type.startswith("audio/"))

        asyncio.run(run_test())

    def test_close_request(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            queue.close()
            req = await queue.get()
            self.assertTrue(req.close)

        asyncio.run(run_test())

    def test_activity_end_clears_priority_immediately(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            system_content = types.Content(parts=[types.Part(text="Immediate system notification")])

            queue.send_activity_start()
            req_start = await queue.get()
            self.assertIsNotNone(req_start.activity_start)

            queue.send_realtime(types.Blob(mime_type="audio/pcm;rate=16000", data=b"speech"))
            queue.send_activity_end()
            queue.send_content(system_content)

            req_audio = await queue.get()
            self.assertEqual(req_audio.blob.data, b"speech")

            req_end = await queue.get()
            self.assertIsNotNone(req_end.activity_end)

            req_sys = await queue.get()

            self.assertIsNotNone(req_sys.content)
            self.assertEqual(req_sys.content.parts[0].text, "Immediate system notification")

        asyncio.run(run_test())

    def test_non_audio_is_flushed_before_activity_start(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            first_content = types.Content(parts=[types.Part(text="First system notification")])
            second_content = types.Content(parts=[types.Part(text="Second system notification")])

            queue.send_content(first_content)
            queue.send_content(second_content)
            queue.send_activity_start()
            queue.send_realtime(types.Blob(mime_type="audio/pcm;rate=16000", data=b"speech"))

            self.assertEqual((await queue.get()).content.parts[0].text, "First system notification")
            self.assertEqual((await queue.get()).content.parts[0].text, "Second system notification")
            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertEqual((await queue.get()).blob.data, b"speech")

        asyncio.run(run_test())

    def test_system_notification_cannot_interrupt_active_vad(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
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

    def test_non_audio_after_activity_start_waits_for_the_next_non_audio_phase(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            deferred_content = types.Content(parts=[types.Part(text="Deferred")])

            queue.send_activity_start()
            queue.send_content(deferred_content)
            queue.send_activity_end()

            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)
            self.assertEqual((await queue.get()).content.parts[0].text, "Deferred")

        asyncio.run(run_test())

    def test_multiple_vad_pairs_are_preserved_in_the_audio_queue(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()

            queue.send_activity_start()
            queue.send_activity_end()
            queue.send_activity_start()
            queue.send_activity_end()

            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)
            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)

        asyncio.run(run_test())

    def test_non_audio_between_vad_pairs_is_sent_before_the_next_pair(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            content = types.Content(parts=[types.Part(text="Between VAD pairs")])

            queue.send_activity_start()
            queue.send_activity_end()
            queue.send_activity_start()
            queue.send_activity_end()

            self.assertIsNotNone((await queue.get()).activity_start)
            queue.send_content(content)
            self.assertIsNotNone((await queue.get()).activity_end)
            self.assertEqual((await queue.get()).content.parts[0].text, "Between VAD pairs")
            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)

        asyncio.run(run_test())

    def test_arbitrary_send_routes_activity_end_with_audio(self):
        async def run_test():
            queue = PriorityLiveRequestQueue()
            queue.send(LiveRequest(activity_start=types.ActivityStart()))
            queue.send_content(types.Content(parts=[types.Part(text="Deferred")]))
            queue.send(LiveRequest(activity_end=types.ActivityEnd()))

            self.assertIsNotNone((await queue.get()).activity_start)
            self.assertIsNotNone((await queue.get()).activity_end)
            self.assertEqual((await queue.get()).content.parts[0].text, "Deferred")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
