"""Tests for persisting and resetting canvas doodles."""

import json
from pathlib import Path

from testing.ui.base import UITestCase


class TestDoodles(UITestCase):

    def test_deselecting_drawing_color_enables_scrollable_narration_pane(self):
        canvas = (Path(__file__).resolve().parents[2] / "templates" / "canvas.html").read_text(encoding="utf-8")
        narration_js = (Path(__file__).resolve().parents[2] / "static" / "js" / "narration-pane.js").read_text(encoding="utf-8")
        narration_css = (Path(__file__).resolve().parents[2] / "static" / "css" / "narration-pane.css").read_text(encoding="utf-8")
        narration_html = (Path(__file__).resolve().parents[2] / "static" / "html" / "narration-pane.html").read_text(encoding="utf-8")

        self.assertIn("function setDrawingColor(color)", canvas)
        self.assertIn("const nextColor = btn.classList.contains('active') ? null : btn.dataset.color;", canvas)
        self.assertIn("canvas.classList.toggle('drawing-disabled', currentColor === null);", canvas)
        self.assertIn("narrationPane.setDrawingColorSelected(currentColor !== null);", canvas)
        self.assertIn('id="narration-pane-mount"', canvas)
        self.assertIn('id="scene-dialogue-overlay"', narration_html)
        self.assertIn("fetch('/static/html/narration-pane.html')", narration_js)
        self.assertIn("container.classList.toggle('narration-interactive', !drawingColorSelected);", narration_js)
        self.assertIn("#scene-dialogue-overlay.narration-interactive .scene-description:hover", narration_css)
        self.assertIn("overflow-y: auto;", narration_css)
        self.assertIn("overscroll-behavior: contain;", narration_css)

    def test_canvas_starts_without_a_drawing_color_and_click_controls_page_bar(self):
        canvas = (Path(__file__).resolve().parents[2] / "templates" / "canvas.html").read_text(encoding="utf-8")

        self.assertIn("let currentColor = null;", canvas)
        self.assertNotIn('class="color-btn active"', canvas)
        self.assertIn("const clickedCanvas = imgContainer.contains(event.target);", canvas)
        self.assertIn("const clickedDrawingControls = doodleToolbar?.contains(event.target);", canvas)
        self.assertIn("if (!clickedCanvas && !clickedDrawingControls && currentColor !== null) {", canvas)
        self.assertIn("setDrawingColor(null);", canvas)
        self.assertIn(
            "pagingControls?.classList.toggle('canvas-selected', clickedCanvas && currentColor === null);",
            canvas,
        )
        self.assertIn("#history-paging-controls.canvas-selected,", canvas)

    def test_selected_white_color_uses_a_black_ring(self):
        canvas = (Path(__file__).resolve().parents[2] / "templates" / "canvas.html").read_text(encoding="utf-8")

        self.assertIn('.color-btn[data-color="#ffffff"].active {', canvas)
        self.assertIn("border-color: #000;", canvas)
        self.assertIn("box-shadow: 0 0 0 2px #000;", canvas)

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
        self.assertIn("window.open('/api/tiktok/connect'", obs)
        self.assertIn("function showTikTokDraftHandoff(clip)", obs)
        self.assertIn("showTikTokDraftHandoff(clip);", obs)
        self.assertIn("fetch('/api/tiktok/draft-upload'", obs)
        self.assertIn(".split(';', 1)[0]", obs)
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
