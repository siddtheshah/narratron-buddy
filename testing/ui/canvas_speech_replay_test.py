import unittest
from pathlib import Path


class TestCanvasSpeechReplayUI(unittest.TestCase):
    def test_canvas_pin_follows_description_and_bookmark_in_upper_left(self) -> None:
        canvas_path = Path("templates/canvas.html")
        self.assertTrue(canvas_path.exists(), "canvas.html should exist")
        content = canvas_path.read_text(encoding="utf-8")

        self.assertIn("#canvas-pin-btn {", content)
        pin_css_snippet = content[
            content.find("#canvas-pin-btn {") : content.find("#canvas-pin-btn {") + 600
        ]
        self.assertIn("top: 1.5rem;", pin_css_snippet)
        self.assertIn("left: calc(1.5rem + 104px);", pin_css_snippet)

    def test_canvas_contains_latest_speech_replay_button_in_lower_right(self) -> None:
        canvas_path = Path("templates/canvas.html")
        content = canvas_path.read_text(encoding="utf-8")

        self.assertIn('id="latest-speech-replay-btn"', content)
        self.assertIn("#latest-speech-replay-btn {", content)
        replay_css_snippet = content[
            content.find("#latest-speech-replay-btn {") : content.find("#latest-speech-replay-btn {") + 600
        ]
        self.assertIn("right: 1.5rem;", replay_css_snippet)
        self.assertIn("bottom: 1.5rem;", replay_css_snippet)

    def test_speech_silencing_triggers_wired(self) -> None:
        canvas_path = Path("templates/canvas.html")
        content = canvas_path.read_text(encoding="utf-8")

        # Push to talk interruption
        self.assertIn("function beginMicPress(source) {", content)
        mic_press_snippet = content[
            content.find("function beginMicPress(source) {") : content.find("function beginMicPress(source) {") + 250
        ]
        self.assertIn("stopSceneSpeech();", mic_press_snippet)

        # Text input open interruption
        self.assertIn("function openOratorCommand() {", content)
        open_cmd_snippet = content[
            content.find("function openOratorCommand() {") : content.find("function openOratorCommand() {") + 250
        ]
        self.assertIn("stopSceneSpeech();", open_cmd_snippet)

        # VAD 1s interruption
        self.assertIn("vadSpeechTimer", content)
        self.assertIn("setTimeout", content)
        self.assertIn("1000", content)
        self.assertIn("stopSceneSpeech();", content)

    def test_canvas_scene_module_exports_replay_helpers(self) -> None:
        scene_path = Path("static/js/canvas-scene.js")
        self.assertTrue(scene_path.exists(), "canvas-scene.js should exist")
        content = scene_path.read_text(encoding="utf-8")

        self.assertIn("replayLatestSpeech", content)
        self.assertIn("silencedSceneSpeechGeneration", content)
        self.assertIn("latestSpeechClips", content)
