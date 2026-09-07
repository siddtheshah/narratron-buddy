from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_canvas_contains_pcm_microphone_test_controls():
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


def test_first_time_orator_opens_microphone_configuration_after_tutorial():
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "openOratorHowtoModal({ openMicConfigOnClose: true })" in content
    assert "if (openMicConfigOnClose) openMicConfigModal();" in content


def test_enabling_mic_summons_an_inactive_agent_before_recording():
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    mic_handler = content.split("agentConnectBtn.addEventListener('click', async () => {", 1)[1].split(
        "// ========================================\n        // Configurable Mic Hotkey Logic", 1
    )[0]
    assert "if (!isAgentStarted) await startAgentTheater();" in mic_handler
    assert "await startMicStream();" in mic_handler
