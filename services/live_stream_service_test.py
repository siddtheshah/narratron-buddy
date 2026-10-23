import asyncio
import os
import unittest
from io import BytesIO

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
import pytest
from fastapi import WebSocketDisconnect
from google.genai import types
from PIL import Image
from providers import TextResponseProvider

from services.live_stream_service import format_canvas_state, handle_live_websocket_connection
from tools.story import StoryTool
from components.canvas.canvas_state_manager import CanvasStateManager
from components.canvas.tool_response_state import ToolResponseState
from components.notepad import Notepad
from components.reference_manager import Character, ReferenceManager, PlayerCharacter
from components.theater_manager import TheaterManager
from components.canvas.story_state import CharacterState, PlayerCharacterState, StoryState
from components.canvas.game_state import GameState
from tools.notepad_tool import NotepadTool
from tools.reference_tool import ReferenceTool


@dataclass
class CanvasVisualFixture:
    shown_image_path: str | None = None
    shown_image_prompt: str | None = None
    pinned: bool = False


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
    story: StoryState = field(default_factory=StoryState)
    game: GameState | None = None
    notepad: Notepad | None = None
    tool_response: ToolResponseState | None = None

    def persist(self) -> None:
        pass

    def notify_changed(self, *domains: str) -> None:
        pass


@pytest.fixture
def notepad() -> MagicMock:
    pad = MagicMock(spec=Notepad)
    pad.get_present_elements.return_value = []
    return pad


@pytest.fixture
def reference_manager() -> MagicMock:
    manager = MagicMock(spec=ReferenceManager)
    manager.recent_reference_tags.return_value = []
    manager.available_scene_images.return_value = {}
    manager.get_player_character.return_value = None
    manager.available_character_images.return_value = {}
    return manager


def test_format_canvas_state_uses_only_recent_reference_tags(reference_manager: MagicMock) -> None:
    reference_manager.recent_reference_tags.return_value = ["<Mara>", "<Harbor>", "map"]
    reference_manager.available_character_images.return_value = {"Untracked": "private/untracked.png"}
    state = format_canvas_state(CanvasFixture(), reference_manager)

    assert "[Available References]: <Mara>, <Harbor>, map" in state
    assert "list_references" in state
    assert "search_references" in state
    assert "Untracked" not in state
    assert "private/" not in state
    reference_manager.recent_reference_tags.assert_called_once_with(limit=10)
    reference_manager.available_character_images.assert_not_called()
    reference_manager.available_scene_images.assert_not_called()


def test_format_canvas_state_limits_tool_catalog_to_ten_newest_references(tmp_path: Path) -> None:
    theater = TheaterManager(tmp_path).theater("stage")
    manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
    reference_tool = ReferenceTool(theater, manager)
    paths: list[Path] = []
    for index in range(12):
        if index % 3 == 0:
            image = theater.references_dir() / f"asset_{index}.png"
        elif index % 3 == 1:
            image = theater.updated_characters_dir() / f"Hero_{index}" / "1.png"
        else:
            image = theater.updated_scenes_dir() / f"Place_{index}" / "1.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "blue").save(image)
        os.utime(image, (1000 + index, 1000 + index))
        paths.append(image)

    output = theater.directory() / "output" / "untracked_output.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "red").save(output)

    entries = reference_tool.list_references()
    tags_by_path = {entry["path"]: entry["alias"] for entry in entries}
    expected = [tags_by_path[str(image)] for image in reversed(paths[2:])]
    state = format_canvas_state(CanvasFixture(), manager)
    assert f"[Available References]: {', '.join(expected)}." in state
    for image in paths[:2]:
        assert tags_by_path[str(image)] not in state
    for image in paths:
        assert str(image) not in state
    assert "untracked_output" not in state
    assert ".png" not in state

    # Updating an older reference brings it into the next state update.
    os.utime(paths[0], (2000, 2000))
    refreshed = format_canvas_state(CanvasFixture(), manager)
    assert f"[Available References]: {tags_by_path[str(paths[0])]}, " in refreshed
    assert tags_by_path[str(paths[2])] not in refreshed


@pytest.mark.parametrize("reload_manager", [False, True])
def test_recent_canvas_captures_share_reference_limit_without_paths(
    tmp_path: Path, reload_manager: bool,
) -> None:
    theater = TheaterManager(tmp_path).theater("stage")
    manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
    portrait = theater.updated_characters_dir() / "Mara" / "1.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(portrait)
    os.utime(portrait, (1010.5, 1010.5))

    handles: list[str] = []
    for index in range(12):
        buffer = BytesIO()
        Image.new("RGB", (8, 8), (index, 0, 0)).save(buffer, format="PNG")
        handle = manager.save_canvas_capture(buffer.getvalue(), "image/png")
        assert handle is not None
        handles.append(handle)
        capture_path = manager.get_canvas_capture_path(handle)
        assert capture_path is not None
        os.utime(capture_path, (1000 + index, 1000 + index))

    if reload_manager:
        manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
    tool = ReferenceTool(theater, manager)
    entries = tool.list_references()
    assert len(entries) == 13  # One entry per capture, without lookup aliases.
    assert tool.search_references(handles[-1])[0]["alias"] == f"<{handles[-1]}>"
    expected = [f"<{handles[-1]}>", "<Mara>"] + [
        f"<{handle}>" for handle in reversed(handles[3:-1])
    ]
    assert manager.recent_reference_tags() == expected
    state = format_canvas_state(CanvasFixture(), manager)
    assert f"[Available References]: {', '.join(expected)}." in state
    for entry in entries:
        assert entry["path"] not in state
    for handle in handles[:3]:
        assert f"<{handle}>" not in state
    assert "<canvas_capture>" not in state
    assert ".png" not in state


def test_format_canvas_state_reads_current_character_catalog_without_story_sync(tmp_path: Path) -> None:
    theater = TheaterManager(tmp_path).theater("stage")
    manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
    story = StoryState()
    canvas = CanvasFixture(story=story)
    assert "[Available References]" not in format_canvas_state(canvas, manager)

    portrait = theater.updated_characters_dir() / "Arthur_Modella" / "1.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(portrait)
    assert "[Available References]: <Arthur Modella>" in format_canvas_state(canvas, manager)

    portrait.unlink()
    assert "[Available References]" not in format_canvas_state(canvas, manager)
    assert "available_character_images" not in story.serialize()


def test_format_canvas_state_reads_current_scene_visuals(tmp_path: Path) -> None:
    theater = TheaterManager(tmp_path).theater("stage")
    manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
    canvas = CanvasFixture()
    assert "[Available References]" not in format_canvas_state(canvas, manager)

    authored = theater.scenes_dir() / "Old_Harbor" / "1.png"
    authored.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "blue").save(authored)
    forest = theater.scenes_dir() / "Moonlit_Forest" / "1.png"
    forest.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "green").save(forest)
    state = format_canvas_state(canvas, manager)
    assert "[Available References]:" in state
    assert "<Old Harbor>" in state
    assert "<Moonlit Forest>" in state
    assert str(authored) not in state
    assert str(forest) not in state
    assert "Use these exact reference tags in image and animation prompts" in state

    updated = theater.updated_scenes_dir() / "Old_Harbor" / "2.png"
    updated.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(updated)
    state = format_canvas_state(canvas, manager)
    assert "<Old Harbor>" in state
    assert str(updated) not in state
    assert str(authored) not in state

    updated.unlink()
    assert "<Old Harbor>" in format_canvas_state(canvas, manager)
    authored.unlink()
    forest.unlink()
    assert "[Available References]" not in format_canvas_state(canvas, manager)


@pytest.mark.parametrize("image_path", ["/private/images/secret.png", r"C:\private\images\secret.png"])
def test_format_canvas_state_hides_current_image_path_and_filename(image_path: str) -> None:
    canvas = CanvasFixture(visual=CanvasVisualFixture(
        shown_image_path=image_path, shown_image_prompt="A moonlit harbor",
    ))
    state = format_canvas_state(canvas)
    assert "[Canvas Image]: shown, A moonlit harbor" in state
    assert image_path not in state
    assert "secret.png" not in state


def test_format_canvas_state_includes_sticky_notes(tmp_path: Path, reference_manager: MagicMock) -> None:
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage"))
    theater.config = MagicMock(return_value={})
    elements = StoryTool(
        theater,
        canvas_manager=CanvasFixture(),
        text_response_provider=MagicMock(),
        reference_manager=reference_manager,
    )
    elements.update_sticky_note("hero", "Mara, a cartographer")
    elements.update_sticky_note("tone", "Hopeful and tense")

    canvas = CanvasFixture(notepad=elements.notepad)
    state = format_canvas_state(
        canvas,
        reference_manager=reference_manager,
    )

    assert "[Sticky Notes]: hero: Mara, a cartographer; tone: Hopeful and tense" in state


@pytest.mark.parametrize("adventure_mode", [False, True])
def test_format_canvas_state_only_includes_raw_notes_outside_adventure_mode(
    adventure_mode: bool, tmp_path: Path, reference_manager: MagicMock,
) -> None:
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage"))
    theater.config.return_value = {"story_planning": {"adventure_mode": adventure_mode, "enforce_structured": False}}
    theater.theater_id = "stage"
    canvas = CanvasStateManager(theater)
    assert canvas.story.adventure_mode is adventure_mode
    pad = Notepad(theater, canvas_manager=canvas, enforce_structured=False)
    pad.config["adventure_mode"] = not adventure_mode
    pad.update_sticky_note("secret", "The guide is the villain")
    reference_manager.recent_reference_tags.return_value = ["<Guide>", "<Harbor>"]
    reference_manager.available_character_images.return_value = {"Guide": "guide.png"}
    reference_manager.available_scene_images.return_value = {"Harbor": "harbor.png"}

    canvas.notepad = pad
    state = format_canvas_state(canvas, reference_manager)

    if adventure_mode:
        assert "[Sticky Notes]" not in state
        assert "secret" not in state
        assert "The guide is the villain" not in state
    else:
        assert "[Sticky Notes]: secret: The guide is the villain" in state
    assert "[Canvas Image]" in state
    assert "[Canvas music]" in state
    assert "[Available References]: <Guide>" in state
    assert "<Harbor>" in state
    assert "harbor.png" not in state


def test_format_canvas_state_uses_notepad(
    notepad: MagicMock, reference_manager: MagicMock,
) -> None:
    story = StoryState()
    notepad.get_present_elements.return_value = [
        {"name": "objective", "content": "Find the lost compass"},
    ]

    state = format_canvas_state(CanvasFixture(story=story, notepad=notepad), reference_manager)

    assert "[Sticky Notes]: objective: Find the lost compass" in state
    notepad.get_present_elements.assert_called_once_with()


def test_format_canvas_state_omits_active_character_details(tmp_path: Path) -> None:
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage_chars"))
    theater.config = MagicMock(return_value={"adventure_mode": True})
    canvas = CanvasFixture()
    ref_mgr = ReferenceManager(theater=theater, canvas_manager=canvas, story_state=canvas.story)
    elements = StoryTool(
        theater,
        canvas_manager=canvas,
        text_response_provider=MagicMock(),
        reference_manager=ref_mgr,
    )
    elements.create_or_update_character(name="Vaelen", personality="Brave", motivation="Find the talisman", quirk="Flips a coin on choices")

    canvas = CanvasFixture(notepad=elements.notepad)
    state = format_canvas_state(
        canvas,
        reference_manager=elements.reference_manager,
    )

    assert "[Active Characters]" not in state
    assert "[Character Image References]:" not in state


def test_format_canvas_state_includes_elements_from_notepad_tool(tmp_path: Path, reference_manager: MagicMock) -> None:
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage_notepad"))
    theater.config = MagicMock(return_value={})
    mock_notepad = MagicMock(spec=Notepad)
    mock_notepad.get_present_elements.return_value = [
        {"name": "objective", "content": "Find the lost compass"}
    ]
    canvas = CanvasFixture(story=StoryState(), notepad=mock_notepad)
    notepad_tool = NotepadTool(
        theater,
        canvas_manager=canvas,  # type: ignore[arg-type]
    )
    notepad_tool.update_sticky_note("objective", "Find the lost compass")
    mock_notepad.update_sticky_note.assert_called_once_with("objective", "Find the lost compass")

    state = format_canvas_state(canvas, reference_manager)
    assert "[Sticky Notes]: objective: Find the lost compass" in state


def test_format_canvas_state_omits_character_details_from_reference_manager(tmp_path: Path, reference_manager: MagicMock) -> None:
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage_char_tool"))
    theater.config = MagicMock(return_value={})
    canvas = CanvasFixture()
    char_mgr = reference_manager
    char_mgr.get_present_characters.return_value = [
        Character(
            name="Rowan",
            gender="nonbinary",
            personality="Stealthy",
            motivation="Freedom",
            quirk="Whistles quietly",
        )
    ]
    state = format_canvas_state(canvas, char_mgr)
    assert "[Active Characters]" not in state


def test_format_canvas_state_omits_character_image_handles_for_npcs(tmp_path: Path) -> None:
    canvas = CanvasFixture(story=StoryState())
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage_char_refs"))
    theater.config = MagicMock(return_value={"adventure_mode": True})
    ref_mgr = ReferenceManager(theater=theater, canvas_manager=canvas, story_state=canvas.story)
    elements = StoryTool(
        theater,
        canvas_manager=canvas,
        text_response_provider=MagicMock(),
        reference_manager=ref_mgr,
    )
    elements.reference_manager.create_or_update_character(
        name="Lyra",
        gender="female",
        personality="Curious",
        motivation="Truth",
        quirk="Hums melodies",
        image_reference="lyra_portrait",
    )

    canvas.notepad = elements.notepad
    state = format_canvas_state(canvas, elements.reference_manager)

    assert "[Active Characters]" not in state
    assert "lyra_portrait" not in state
    assert "[Character Image References]" not in state
    assert "Image Reference" not in state


def test_format_canvas_state_omits_character_image_handles_for_player(tmp_path: Path) -> None:
    canvas = CanvasFixture(story=StoryState())
    theater = MagicMock(wraps=TheaterManager(tmp_path).theater("stage_player_refs"))
    theater.config = MagicMock(return_value={"adventure_mode": True})
    ref_mgr = ReferenceManager(theater=theater, canvas_manager=canvas, story_state=canvas.story)
    elements = StoryTool(
        theater,
        canvas_manager=canvas,
        text_response_provider=MagicMock(),
        reference_manager=ref_mgr,
    )
    elements.reference_manager.set_player_character(
        PlayerCharacter(
            name="Valen",
            reference="valen_img",
            image_description="A dashing space rogue",
        )
    )

    canvas.notepad = elements.notepad
    state = format_canvas_state(canvas, elements.reference_manager)

    assert "[Player Character]" not in state
    assert "valen_img" not in state
    assert "[Character Image References]" not in state
    assert "Image Reference" not in state


def test_format_canvas_state_shows_reference_tags_without_character_details() -> None:
    story = StoryState()
    story.set_player_character(
        PlayerCharacterState(
            name="Mara",
            reference="mara_portrait",
        )
    )
    story.set_characters({
        "Cedric": CharacterState(
            name="Cedric",
            personality="Loyal",
            motivation="Honor",
            quirk="Polishes sword",
            image_reference="cedric_img",
        )
    })
    story.set_character_references(["mara_portrait", "cedric_img"])

    canvas = CanvasFixture(story=story)
    manager = MagicMock(spec=ReferenceManager)
    manager.recent_reference_tags.return_value = ["<Mara>", "<Cedric>"]
    state = format_canvas_state(canvas, manager)

    assert "[Available References]: <Mara>, <Cedric>" in state
    assert "<Cedric>" in state
    assert "Loyal" not in state
    assert "Honor" not in state
    assert "Polishes sword" not in state
    assert "private/" not in state
    assert "[Player Character]" not in state
    assert "[Active Characters]" not in state
    assert "mara_portrait" not in state
    assert "cedric_img" not in state
    assert "[Character Image References]" not in state
    assert "Image Reference" not in state


def test_canvas_observability_preserves_collaboration_data_when_disabled(reference_manager: MagicMock) -> None:
    doodles = [{"type": "draw", "x0": 0, "y0": 0, "x1": 1, "y1": 1}]
    canvas = CanvasFixture(doodles=doodles)

    format_canvas_state(canvas, reference_manager)

    canvas.chat.consume_top_suggestion.assert_not_called()
    assert canvas.doodles == doodles


def test_canvas_observability_preserves_suggestion_when_collaboration_is_enabled(reference_manager: MagicMock) -> None:
    canvas = CanvasFixture(ui=CanvasUIFixture(viewer_collab_enabled=True))
    canvas.chat.consume_top_suggestion.return_value = {
        "author": "Ada", "text": "Open the hidden door", "upvote_count": 2,
    }

    state = format_canvas_state(canvas, reference_manager)

    assert "[Viewer Suggestion]" not in state
    canvas.chat.consume_top_suggestion.assert_not_called()


def test_canvas_observability_informs_agent_when_orator_pins_visual(reference_manager: MagicMock) -> None:
    state = format_canvas_state(CanvasFixture(visual=CanvasVisualFixture(pinned=True)), reference_manager)

    assert "[Canvas Pin]: The orator has pinned the current canvas." in state
    assert "tools will decline while pinned" in state


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

        mock_live_agent_manager = MagicMock()
        mock_live_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_mic_muted",
                live_agent_manager=mock_live_agent_manager,
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

        mock_live_agent_manager = MagicMock()
        mock_live_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_chunking_theater",
                live_agent_manager=mock_live_agent_manager,
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

        mock_live_agent_manager = MagicMock()
        mock_live_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_disconnect_flush",
                live_agent_manager=mock_live_agent_manager,
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

        mock_live_agent_manager = MagicMock()
        mock_live_agent_manager.get_or_create_session.return_value = mock_session

        asyncio.run(
            handle_live_websocket_connection(
                websocket=mock_ws,
                theater_id="test_baton_handoff",
                live_agent_manager=mock_live_agent_manager,
                user_id=1,
                send_setup_complete_immediately=False,
            )
        )

        mock_session.send_realtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
