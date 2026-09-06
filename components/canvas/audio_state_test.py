from components.canvas.audio_state import AudioState


def test_audio_state_transitions_publish_latest() -> None:
    domains: list[str] = []
    state = AudioState(domains.append)
    state.update_music("rain", ["rain.mp3"])
    state.pause(); state.resume()
    assert state.payload()["playlist"] == "rain"
    assert domains == ["latest", "latest", "latest"]
