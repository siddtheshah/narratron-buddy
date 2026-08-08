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
