"""Tests for persisting and resetting canvas doodles."""

import json
from pathlib import Path

from testing.ui.base import UITestCase


class TestDoodles(UITestCase):

    def test_canvas_retries_active_doodle_websocket_connections(self):
        canvas = (Path(__file__).resolve().parents[2] / "templates" / "canvas.html").read_text(encoding="utf-8")
        self.assertIn("function connectDoodleSocket(initialConnection = false)", canvas)
        self.assertIn("scheduleDoodleReconnect", canvas)
        self.assertIn("document.visibilityState === 'visible' && document.hasFocus()", canvas)
        self.assertIn("pendingDoodleMessages", canvas)
        self.assertIn("inFlightDoodleMessages", canvas)
        self.assertIn("doodle_ack", canvas)
        self.assertIn("if (!pendingDoodleMessages.length || doodleReconnectTimer || !isActiveCanvasWindow()) return;", canvas)
        self.assertIn("connectDoodleSocket(true);", canvas)

    def test_canvas_clip_sharing_mixes_opt_in_voice_and_tab_audio_locally(self):
        canvas = (Path(__file__).resolve().parents[2] / "templates" / "canvas.html").read_text(encoding="utf-8")
        obs = (Path(__file__).resolve().parents[2] / "templates" / "obs.html").read_text(encoding="utf-8")
        self.assertIn('id="clip-share-btn"', canvas)
        self.assertIn("new URL('/obs', window.location.origin)", canvas)
        self.assertIn("clipUrl.searchParams.set('clip', '1');", canvas)
        self.assertIn('id="clip-capture-panel"', obs)
        self.assertIn("const CLIP_DURATION_MS = 30_000;", obs)
        self.assertIn("navigator.mediaDevices.getDisplayMedia", obs)
        self.assertIn("audio: true", obs)
        self.assertIn('id="clip-include-mic"', obs)
        self.assertIn("navigator.mediaDevices.getUserMedia({ audio, video: false })", obs)
        self.assertIn("createMediaStreamDestination()", obs)
        self.assertIn("new MediaStream([videoTrack, ...audioMix.tracks])", obs)
        self.assertIn("'video/webm;codecs=vp8,opus'", obs)
        self.assertIn("'video/mp4;codecs=avc1.42E01E,mp4a.40.2'", obs)
        self.assertIn('id="clip-recording-bar"', obs)
        self.assertIn('id="clip-start-recording-btn"', obs)
        self.assertIn('id="clip-stop-early-btn"', obs)
        self.assertIn('id="clip-start-over-btn"', obs)
        self.assertIn("activeClipControl?.stop(false)", obs)
        self.assertIn("activeClipControl?.stop(true)", obs)
        self.assertIn("clipStartRecordingBtn?.addEventListener('click', beginClipCapture)", obs)
        self.assertIn("const clipMode = urlParams.get('clip') === '1';", obs)
        self.assertIn('id="clip-tiktok-draft-link"', obs)
        self.assertIn('href="https://www.tiktok.com/tiktokstudio/upload?lang=en"', obs)
        self.assertIn("function showTikTokDraftHandoff()", obs)
        self.assertIn("showTikTokDraftHandoff();", obs)
        self.assertNotIn("/api/clips", obs)

    def test_doodles_persist_and_reset_when_cleared(self):
        theater_id = "doodle_persistence"
        first_image = self.workspace / "img1.jpg"
        second_image = self.workspace / "img2.jpg"
        first_image.write_bytes(b"image one")
        second_image.write_bytes(b"image two")
        manager = self.make_canvas_state(theater_id)
        manager.visual.show_image(str(first_image))

        first_doodle = {
            "type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2,
            "color": "#ffffff", "size": 3,
        }
        second_doodle = {
            "type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3,
            "color": "#ff0000", "size": 5,
        }
        manager.doodles.add([first_doodle])
        manager.doodles.add([second_doodle])

        theater_file = self.theaters_dir / theater_id / "theater.json"
        with theater_file.open(encoding="utf-8") as file:
            saved_doodles = json.load(file)["canvas_state"]["doodles"]
        self.assertEqual(saved_doodles, [first_doodle, second_doodle])

        reloaded_manager = self.make_canvas_state(theater_id)
        self.assertEqual(reloaded_manager.doodles.doodles, [first_doodle, second_doodle])

        manager.visual.show_image(str(second_image))
        self.assertEqual(manager.doodles.doodles, [first_doodle, second_doodle])

        manager.doodles.add([{"type": "clear"}])
        self.assertEqual(manager.doodles.doodles, [])
