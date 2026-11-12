import unittest
from pathlib import Path

from testing.ui.canvas_source import read_ui_source


class TestCanvasSpeechReplayUI(unittest.TestCase):
    def test_speech_silencing_triggers_wired(self) -> None:
        canvas_path = Path("templates/canvas.html")
        content = read_ui_source(canvas_path)

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
