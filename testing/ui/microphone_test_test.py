from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_canvas_contains_pcm_microphone_test_controls() -> None:
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    for element in (
        'id="menu-mic-test-record"',
        'id="menu-mic-test-play"',
        'id="menu-mic-test-status"',
        "listenForSpeech",
        "MIC_TEST_SAMPLE_RATE = 16000",
        "getInt16(index * 2, true)",
    ):
        assert element in content


def test_first_time_orator_opens_microphone_configuration_after_tutorial() -> None:
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "openOratorHowtoModal({ openMicConfigOnClose: true })" in content
    assert "if (openMicConfigOnClose) openMicConfigModal();" in content


def test_enabling_mic_summons_an_inactive_agent_before_recording() -> None:
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "agentConnectBtn.addEventListener('click'" in content
    assert "enableMic();" in content
    mic_handler = content.split("async function enableMic() {", 1)[1].split(
        "function releaseMicPress() {", 1
    )[0]
    assert "if (!isAgentStarted) await startAgentTheater();" in mic_handler
    assert "await startMicStream(" in mic_handler
