from components.canvas.audio_state import AudioState


def test_pinned_music_blocks_background_changes_and_restores() -> None:
    state = AudioState(lambda *_: None)
    state.update_music("current", ["current.mp3"])
    state.set_pinned(True)
    state.update_music("replacement", ["replacement.mp3"])
    state.pause()
    assert state.current_music_id == "current"
    assert state.music_paused is False
    restored = AudioState(lambda *_: None)
    restored.load(state.serialize())
    assert restored.pinned is True
    restored.set_pinned(False)
    restored.update_music("replacement", ["replacement.mp3"])
    assert restored.current_music_id == "replacement"


def test_audio_state_transitions_publish_latest() -> None:
    domains: list[str] = []
    state = AudioState(domains.append)
    state.update_music("rain", ["rain.mp3"])
    state.pause()
    state.resume()
    assert state.payload()["playlist"] == "rain"
    assert domains == ["latest", "latest", "latest"]


def test_audio_state_initial_state() -> None:
    state = AudioState(lambda *_: None)
    assert state.current_music_id is None
    assert state.current_playlist is None
    assert state.current_playlist_tracks == []
    assert state.music_paused is False
    assert state.current_playlist_time == 0.0

    payload = state.payload()
    assert payload == {
        "music_id": None,
        "playlist": None,
        "tracks": [],
        "paused": False,
        "time": 0.0,
        "orator_cursor": 0,
    }


def test_audio_state_update_music_and_payload() -> None:
    events = []
    state = AudioState(events.append)
    state.update_music("tavern_theme", ["track1.mp3", "track2.mp3"])

    assert state.current_music_id == "tavern_theme"
    assert state.current_playlist == "tavern_theme"
    assert state.current_playlist_tracks == ["track1.mp3", "track2.mp3"]
    assert state.music_paused is False
    assert state.current_playlist_time > 0.0

    payload = state.payload()
    assert payload["music_id"] == "tavern_theme"
    assert payload["playlist"] == "tavern_theme"
    assert payload["tracks"] == ["track1.mp3", "track2.mp3"]
    assert payload["paused"] is False
    assert payload["time"] == state.current_playlist_time


def test_audio_state_pause_and_resume_updates_payload_and_time() -> None:
    state = AudioState(lambda *_: None)
    state.update_music("battle", ["battle.mp3"])
    initial_time = state.current_playlist_time

    state.pause()
    assert state.music_paused is True
    assert state.payload()["paused"] is True
    assert state.current_playlist_time >= initial_time

    paused_time = state.current_playlist_time
    state.resume()
    assert state.music_paused is False
    assert state.payload()["paused"] is False
    assert state.current_playlist_time >= paused_time


def test_audio_state_serialize_and_load_round_trip() -> None:
    state = AudioState(lambda *_: None)
    state.update_music("ambient", ["amb1.mp3", "amb2.mp3"])
    state.pause()

    serialized = state.serialize()
    assert serialized == {
        "current_music_id": "ambient",
        "current_playlist": "ambient",
        "current_playlist_tracks": ["amb1.mp3", "amb2.mp3"],
        "music_paused": True,
        "current_playlist_time": state.current_playlist_time,
        "music_pinned": False,
        "music_history": [{"music_id": "ambient", "tracks": ["amb1.mp3", "amb2.mp3"]}],
        "music_orator_cursor": 0,
        "orator_cursor": 0,
    }

    new_state = AudioState(lambda *_: None)
    new_state.load(serialized)
    assert new_state.current_music_id == "ambient"
    assert new_state.current_playlist == "ambient"
    assert new_state.current_playlist_tracks == ["amb1.mp3", "amb2.mp3"]
    assert new_state.music_paused is True
    assert new_state.current_playlist_time == state.current_playlist_time
    assert new_state.serialize() == serialized


def test_audio_state_loads_legacy_playback_with_a_fresh_timestamp() -> None:
    state = AudioState(lambda *_: None)
    state.load({
        "current_music_id": "ambient",
        "current_playlist": "ambient",
        "current_playlist_tracks": ["amb1.mp3"],
        "music_paused": False,
    })

    assert state.current_playlist_time > 0
    assert state.music_history == [{"music_id": "ambient", "tracks": ["amb1.mp3"]}]


def test_audio_state_load_handles_malformed_and_missing_data() -> None:
    state = AudioState(lambda *_: None)
    state.update_music("ambient", ["amb1.mp3"])
    state.pause()

    # Load empty payload resets fields
    state.load({})
    assert state.current_music_id is None
    assert state.current_playlist is None
    assert state.current_playlist_tracks == []
    assert state.music_paused is False

    # Filter out non-string tracks and invalid types
    state.load({
        "current_music_id": 12345,  # Non-string -> None
        "current_playlist": "valid_playlist",
        "current_playlist_tracks": ["track1.mp3", 999, None, {}, "track2.mp3"],
        "music_paused": "truthy_string",  # bool() conversion -> True
    })
    assert state.current_music_id is None
    assert state.current_playlist == "valid_playlist"
    assert state.current_playlist_tracks == ["track1.mp3", "track2.mp3"]
    assert state.music_paused is True

    # Fallback to current_playlist in payload when current_music_id is None
    payload = state.payload()
    assert payload["music_id"] == "valid_playlist"
    assert payload["playlist"] == "valid_playlist"


def test_audio_state_previous_music_navigation() -> None:
    events: list[str] = []
    state = AudioState(events.append)

    # Empty history
    assert state.previous_music() is False

    # Single track in history
    state.update_music("track1", ["1.mp3"])
    assert state.previous_music() is False

    # Multiple tracks
    state.update_music("track2", ["2.mp3"])
    state.update_music("track3", ["3.mp3"])
    assert state.current_music_id == "track3"

    # Go back to track2
    assert state.previous_music() is True
    assert state.current_music_id == "track2"
    assert state.current_playlist_tracks == ["2.mp3"]
    assert state.orator_cursor == 1

    # Go back to track1
    assert state.previous_music() is True
    assert state.current_music_id == "track1"
    assert state.current_playlist_tracks == ["1.mp3"]
    assert state.orator_cursor == 0

    # Cannot go back further
    assert state.previous_music() is False
    assert state.current_music_id == "track1"
    assert state.orator_cursor == 0

    # New music resets navigation index
    state.update_music("track4", ["4.mp3"])
    assert state.current_music_id == "track4"
    assert state.orator_cursor == 3
    assert state.previous_music() is True
    assert state.current_music_id == "track3"
    assert state.orator_cursor == 2



def test_audio_state_orator_cursor_load_and_clamping() -> None:
    state = AudioState(lambda *_: None)
    state.load({
        "current_music_id": "track2",
        "current_playlist": "track2",
        "current_playlist_tracks": ["2.mp3"],
        "music_history": [
            {"music_id": "track1", "tracks": ["1.mp3"]},
            {"music_id": "track2", "tracks": ["2.mp3"]},
            {"music_id": "track3", "tracks": ["3.mp3"]},
        ],
        "music_orator_cursor": 1,
    })
    assert state.orator_cursor == 1
    assert state.music_orator_cursor == 1

    # Out of range clamped to head
    state.load({
        "current_music_id": "track3",
        "music_history": [
            {"music_id": "track1", "tracks": ["1.mp3"]},
            {"music_id": "track2", "tracks": ["2.mp3"]},
        ],
        "music_orator_cursor": 10,
    })
    assert state.orator_cursor == 1

    # Empty history resets cursor to None
    state.load({
        "music_history": [],
        "music_orator_cursor": 5,
    })
    assert state.orator_cursor is None
