import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from google.genai import types

from services.live_stream_service import format_canvas_state, handle_live_websocket_connection
from tools.named_element_tool import NamedElementTools


def test_format_canvas_state_includes_present_scene_elements():
    elements = NamedElementTools(theater_id="stage")
    elements.update_or_insert_named_element("hero", "Mara, a cartographer")
    elements.update_or_insert_named_element("tone", "Hopeful and tense")

    state = format_canvas_state(
        SimpleNamespace(
            shown_image_path=None,
            shown_image_prompt=None,
            current_playlist=None,
            viewer_collab_enabled=False,
        ),
        elements,
    )

    assert "[Present Scene Elements]: hero: Mara, a cartographer; tone: Hopeful and tense" in state


class TestLiveStreamServiceAudioChunking(unittest.TestCase):
    def test_audio_chunking_accumulates_into_30ms_blobs(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        # Simulate sending 10 small chunks of 100 bytes each (total 1,000 bytes)
        small_chunk = b"\x00" * 100
        messages = [{"bytes": small_chunk} for _ in range(10)]

        receive_call_count = 0

        async def mock_receive():
            nonlocal receive_call_count
            if receive_call_count < len(messages):
                msg = messages[receive_call_count]
                receive_call_count += 1
                return msg
            else:
                from fastapi import WebSocketDisconnect
                raise WebSocketDisconnect()

        mock_ws.receive = AsyncMock(side_effect=mock_receive)

        mock_session = MagicMock()
        mock_session.add_websocket = AsyncMock()
        mock_session.remove_websocket = AsyncMock()
        mock_session.record_audio_input = MagicMock()
        mock_session.send_realtime = MagicMock()

        mock_agent_manager = MagicMock()
        mock_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_chunking_theater",
                agent_manager=mock_agent_manager,
                send_setup_complete_immediately=False,
            )
        )

        # Total 1,000 bytes sent.
        # Should result in:
        # - 1 x 960 byte chunk (30ms audio at 16kHz 16-bit PCM mono) sent during stream
        # - 1 x 40 byte chunk sent during disconnect flush
        self.assertTrue(mock_session.send_realtime.called)
        blobs_sent = [
            call.args[0] for call in mock_session.send_realtime.call_args_list
        ]
        
        chunk_lengths = [len(b.data) for b in blobs_sent if isinstance(b, types.Blob)]
        self.assertEqual(chunk_lengths, [960, 40])

    def test_audio_chunking_flushes_partial_buffer_on_disconnect(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        
        # Send 500 bytes (< 30ms chunk of 960 bytes)
        partial_audio = b"\x01" * 500

        from fastapi import WebSocketDisconnect
        mock_ws.receive = AsyncMock(side_effect=[{"bytes": partial_audio}, WebSocketDisconnect()])

        mock_session = MagicMock()
        mock_session.add_websocket = AsyncMock()
        mock_session.remove_websocket = AsyncMock()
        mock_session.record_audio_input = MagicMock()
        mock_session.send_realtime = MagicMock()

        mock_agent_manager = MagicMock()
        mock_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_disconnect_flush",
                agent_manager=mock_agent_manager,
                send_setup_complete_immediately=False,
            )
        )

        mock_session.send_realtime.assert_called_once()
        blob = mock_session.send_realtime.call_args[0][0]
        self.assertEqual(len(blob.data), 500)

    def test_discards_buffered_audio_after_baton_changes_hands(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        from fastapi import WebSocketDisconnect
        mock_ws.receive = AsyncMock(side_effect=[{"bytes": bytes(500)}, WebSocketDisconnect()])

        mock_session = MagicMock()
        mock_session.add_websocket = AsyncMock()
        mock_session.remove_websocket = AsyncMock()
        # The frame was received while this user held the baton, but the
        # baton changes before the disconnect flush forwards the partial data.
        mock_session.can_accept_controller_input.side_effect = [True, False]

        mock_agent_manager = MagicMock()
        mock_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_baton_handoff",
                agent_manager=mock_agent_manager,
                user_id=1,
                send_setup_complete_immediately=False,
            )
        )

        mock_session.send_realtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
