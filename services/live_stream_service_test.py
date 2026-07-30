import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from google.genai import types

from services.live_stream_service import handle_live_websocket_connection


class TestLiveStreamServiceAudioChunking(unittest.TestCase):
    def test_audio_chunking_accumulates_into_3_second_blobs(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        # Simulate sending 100 small chunks of 1,000 bytes each (total 100,000 bytes)
        small_chunk = b"\x00" * 1000
        messages = [{"bytes": small_chunk} for _ in range(100)]

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

        # Total 100,000 bytes sent.
        # Should result in:
        # - 1 x 96,000 byte chunk (3.0s audio) sent during stream
        # - 1 x 4,000 byte chunk sent during disconnect flush
        self.assertTrue(mock_session.send_realtime.called)
        blobs_sent = [
            call.args[0] for call in mock_session.send_realtime.call_args_list
        ]
        
        chunk_lengths = [len(b.data) for b in blobs_sent if isinstance(b, types.Blob)]
        self.assertEqual(chunk_lengths, [96000, 4000])

    def test_audio_chunking_flushes_partial_buffer_on_disconnect(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        
        # Send 32,000 bytes (1 second of audio)
        partial_audio = b"\x01" * 32000

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
        self.assertEqual(len(blob.data), 32000)


if __name__ == "__main__":
    unittest.main()
