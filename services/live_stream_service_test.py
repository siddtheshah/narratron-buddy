import asyncio
import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock, AsyncMock
from fastapi import WebSocketDisconnect
from google.genai import types

from services.live_stream_service import format_canvas_state, handle_live_websocket_connection
from tools.story import StoryTool


@dataclass
class CanvasVisualFixture:
    shown_image_path: str | None = None
    shown_image_prompt: str | None = None


@dataclass
class CanvasAudioFixture:
    current_playlist: str | None = None


@dataclass
class CanvasUIFixture:
    viewer_collab_enabled: bool = False


@dataclass
class CanvasFixture:
    visual: CanvasVisualFixture = field(default_factory=CanvasVisualFixture)
    audio: CanvasAudioFixture = field(default_factory=CanvasAudioFixture)
    ui: CanvasUIFixture = field(default_factory=CanvasUIFixture)
    chat: MagicMock = field(default_factory=MagicMock)
    doodles: list[dict] = field(default_factory=list)


def test_format_canvas_state_includes_present_scene_elements():
    theater = MagicMock(theater_id="stage")
    theater.config = MagicMock(return_value={})
    elements = StoryTool(
        theater,
        canvas_manager=MagicMock(),
        text_response_provider=MagicMock(),
    )
    elements.update_or_insert_named_element("hero", "Mara, a cartographer")
    elements.update_or_insert_named_element("tone", "Hopeful and tense")

    state = format_canvas_state(
        CanvasFixture(),
        elements,
    )

    assert "[Present Scene Elements]: hero: Mara, a cartographer; tone: Hopeful and tense" in state


def test_format_canvas_state_includes_active_characters():
    theater = MagicMock(theater_id="stage_chars")
    theater.config = MagicMock(return_value={"adventure_mode": True})
    elements = StoryTool(
        theater,
        canvas_manager=MagicMock(),
        text_response_provider=MagicMock(),
    )
    elements.generate_character(name="Vaelen", personality="Brave", motivation="Find the talisman", quirk="Flips a coin on choices")

    state = format_canvas_state(
        CanvasFixture(),
        elements,
    )

    assert "[Active Characters]: Vaelen (Personality: Brave, Motivation: Find the talisman, Quirk: Flips a coin on choices)" in state


def test_canvas_observability_preserves_collaboration_data_when_disabled():
    doodles = [{"type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1}]
    canvas = CanvasFixture(doodles=doodles)

    format_canvas_state(canvas)

    canvas.chat.consume_top_suggestion.assert_not_called()
    assert canvas.doodles == doodles


def test_canvas_observability_includes_suggestion_when_collaboration_is_enabled():
    canvas = CanvasFixture(ui=CanvasUIFixture(viewer_collab_enabled=True))
    canvas.chat.consume_top_suggestion.return_value = {
        "author": "Ada", "text": "Open the hidden door", "upvote_count": 2,
    }

    state = format_canvas_state(canvas)

    assert "[Viewer Suggestion]: Open the hidden door (by Ada, 2 upvotes)" in state
    canvas.chat.consume_top_suggestion.assert_called_once_with()


def _make_opus_packet(samples: int = 480) -> bytes:
    import av
    enc = av.CodecContext.create("opus", "w")
    enc.sample_rate = 16000
    enc.layout = "mono"
    enc.format = av.AudioFormat("s16")
    enc.bit_rate = 24000
    enc.open()

    raw_pcm = b"\x00\x00" * samples
    frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
    frame.sample_rate = 16000
    frame.planes[0].update(raw_pcm)

    packets = enc.encode(frame)
    return bytes(packets[0])


class TestLiveStreamServiceAudioChunking(unittest.TestCase):
    def test_mic_activity_end_flushes_audio_then_ends_the_queue_turn(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive = AsyncMock(side_effect=[
            {"bytes": _make_opus_packet(samples=480)},
            {"text": '{"type":"activity_end","reason":"microphone_muted"}'},
            WebSocketDisconnect(),
        ])

        mock_session = MagicMock()
        mock_session.add_websocket = AsyncMock()
        mock_session.remove_websocket = AsyncMock()
        mock_session.can_accept_controller_input.return_value = True

        mock_agent_manager = MagicMock()
        mock_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_mic_muted",
                agent_manager=mock_agent_manager,
                send_setup_complete_immediately=False,
            )
        )

        mock_session.send_realtime.assert_called_once()
        mock_session.send_activity_end.assert_called_once()

    def test_audio_chunking_accumulates_into_30ms_blobs(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        # Send multiple Opus frames
        opus_packet = _make_opus_packet(samples=480)
        messages = [{"bytes": opus_packet} for _ in range(3)]

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

        self.assertTrue(mock_session.send_realtime.called)
        blobs_sent = [
            call.args[0] for call in mock_session.send_realtime.call_args_list
        ]
        
        chunk_lengths = [len(b.data) for b in blobs_sent if isinstance(b, types.Blob)]
        assert len(chunk_lengths) >= 1
        for length in chunk_lengths:
            assert length > 0
            assert length % 2 == 0  # 16-bit PCM

    def test_audio_chunking_flushes_partial_buffer_on_disconnect(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        
        opus_packet = _make_opus_packet(samples=480)

        from fastapi import WebSocketDisconnect
        mock_ws.receive = AsyncMock(side_effect=[{"bytes": opus_packet}, WebSocketDisconnect()])

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

        mock_session.send_realtime.assert_called()
        blob = mock_session.send_realtime.call_args[0][0]
        assert len(blob.data) > 0
        assert len(blob.data) % 2 == 0

    def test_discards_buffered_audio_after_baton_changes_hands(self):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        opus_packet = _make_opus_packet(samples=480)
        from fastapi import WebSocketDisconnect
        mock_ws.receive = AsyncMock(side_effect=[{"bytes": opus_packet}, WebSocketDisconnect()])

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
