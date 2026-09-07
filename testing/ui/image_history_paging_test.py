"""Tests for CanvasStateManager image history and paging payloads."""

from pathlib import Path

from testing.ui.base import UITestCase


class TestImageHistoryPaging(UITestCase):
    def test_paging_bar_is_at_the_top_and_reveals_story_log_inspector(self):
        canvas = Path("templates/canvas.html").read_text(encoding="utf-8")

        self.assertIn("#history-paging-controls {", canvas)
        self.assertIn("top: 1.25rem;", canvas)
        self.assertIn('id="story-log-inspector"', canvas)
        self.assertIn('id="story-log-toggle-btn"', canvas)
        self.assertIn('from "/static/js/story-log-inspector.js"', canvas)
        inspector = Path("static/js/story-log-inspector.js").read_text(encoding="utf-8")
        self.assertIn("initializeStoryLogInspector", inspector)

    def test_story_log_is_chronological_and_keeps_the_latest_action_visible(self):
        inspector = Path("static/js/story-log-inspector.js").read_text(encoding="utf-8")

        self.assertIn("return turns;", inspector)
        self.assertIn("function positionLatestAction(inspector, content)", inspector)
        self.assertIn("inspector.scrollTop = bottom;", inspector)
        self.assertIn("requestAnimationFrame(() => positionLatestAction(inspector, content))", inspector)

    def test_image_history_is_capped_at_100_entries(self):
        manager = self.make_canvas_state("history_cap")

        for index in range(105):
            manager.visual.show_image(f"/virtual/path/to/image_{index}.png")

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(manager.visual.shown_images_history), 100)
        self.assertEqual(len(history), 100)
        self.assertIn("image_5.png", history[0]["path"])
        self.assertIn("image_104.png", history[-1]["path"])

    def test_image_history_payload_contains_presentation_details(self):
        first_image = self.workspace / "scene1.png"
        second_image = self.workspace / "scene2.jpg"
        first_image.write_bytes(b"scene1_data")
        second_image.write_bytes(b"scene2_data")

        manager = self.make_canvas_state("history_payload")
        manager.visual.show_image(str(first_image), transition="fade", url_for_path=manager.theater.get_url_for_path)
        manager.visual.show_image(str(second_image), transition="crossfade", url_for_path=manager.theater.get_url_for_path)

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["path"], str(first_image))
        self.assertEqual(history[0]["transition"], "fade")
        self.assertEqual(history[0]["effect"], "gleam3")
        self.assertEqual(history[1]["path"], str(second_image))
        self.assertEqual(history[1]["transition"], "crossfade")
        self.assertEqual(history[1]["effect"], "gleam3")
        self.assertTrue(history[0]["url"])
        self.assertIn("prompt", history[0])

    def test_history_persists_in_the_test_theater_directory(self):
        theater_id = "history_persistence"
        manager = self.make_canvas_state(theater_id)
        manager.visual.show_image("/path/a.png", transition="fade")
        manager.visual.show_image("/path/b.png", transition="crossfade")

        manager.save_local_theater_data(theater_dir=self.theaters_dir / theater_id)

        reloaded_manager = self.make_canvas_state(theater_id)
        self.assertEqual(len(reloaded_manager.visual.shown_images_history), 2)

    def test_animations_are_recorded_in_history_with_original_prompt_and_payload(self):
        theater_id = "anim_history_theater"
        manager = self.make_canvas_state(theater_id)

        # 1. Static image
        img_file = self.workspace / "initial_scene.png"
        img_file.write_bytes(b"initial_data")
        manager.visual.show_image(str(img_file), url_for_path=manager.theater.get_url_for_path)

        # 2. Tri-frame animation with adjacent triframe.json
        anim_dir = manager.theater.output_dir() / "animations" / "crystal_glow"
        anim_dir.mkdir(parents=True)
        frame_paths = []
        for i in range(1, 4):
            fp = anim_dir / f"frame_{i}.jpg"
            fp.write_bytes(b"frame_data")
            frame_paths.append(str(fp))
        import json
        (anim_dir / "triframe.json").write_text(json.dumps({
            "id": "crystal_glow",
            "type": "triframe",
            "scene_prompt": "A glowing mystical crystal in an ancient cavern.",
        }), encoding="utf-8")

        manager.visual.show_triframe(
            frame_paths,
            prompt="A glowing mystical crystal in an ancient cavern.",
            url_for_path=manager.theater.get_url_for_path,
        )

        # 3. Video animation
        vid_dir = manager.theater.output_dir() / "animations" / "dragon_flight"
        vid_dir.mkdir(parents=True)
        vid_file = vid_dir / "video.mp4"
        vid_file.write_bytes(b"video_bytes")
        (vid_dir / "video.json").write_text(json.dumps({
            "id": "dragon_flight",
            "type": "video",
            "scene_prompt": "A fire-breathing dragon soaring above a volcanic crater.",
        }), encoding="utf-8")

        manager.visual.show_video_animation({
            "id": "dragon_flight",
            "video_path": str(vid_file),
            "video_url": f"/theaters/{theater_id}/output/animations/dragon_flight/video.mp4",
            "scene_prompt": "A fire-breathing dragon soaring above a volcanic crater.",
        })

        # 4. Layered animation
        layered_dir = manager.theater.output_dir() / "animations" / "layered_lake"
        layered_dir.mkdir(parents=True)
        base_img = layered_dir / "base.png"
        layer1_img = layered_dir / "waves.png"
        base_img.write_bytes(b"base")
        layer1_img.write_bytes(b"waves")
        manager.visual.show_layered_animation({
            "id": "layered_lake",
            "scene_prompt": "Gentle moonlit waves on a calm alpine lake.",
            "base_image": str(base_img),
            "layers": [
                {"name": "base", "path": str(base_img), "effect": "none"},
                {"name": "waves", "path": str(layer1_img), "effect": "ripple"},
            ],
        })

        state = manager.get_latest_state()
        history = state["history"]

        # 4 total items in history: initial static image + 3 distinct animations
        self.assertEqual(len(history), 4)

        # Item 0: Static image
        self.assertEqual(history[0]["path"], str(img_file))
        self.assertIsNone(history[0].get("animation"))

        # Item 1: Tri-frame animation
        self.assertEqual(history[1]["animation"]["type"], "triframe")
        self.assertEqual(len(history[1]["animation"]["frames"]), 3)
        self.assertEqual(history[1]["prompt"], "A glowing mystical crystal in an ancient cavern.")
        self.assertNotIn("Image: Video", history[1]["prompt"])

        # Item 2: Video animation
        self.assertEqual(history[2]["animation"]["type"], "video")
        self.assertEqual(history[2]["animation"]["id"], "dragon_flight")
        self.assertEqual(history[2]["prompt"], "A fire-breathing dragon soaring above a volcanic crater.")
        self.assertNotEqual(history[2]["prompt"], "Image: Video")

        # Item 3: Layered animation
        self.assertEqual(history[3]["animation"]["type"], "layered")
        self.assertEqual(history[3]["prompt"], "Gentle moonlit waves on a calm alpine lake.")

        # Active prompt should reflect the latest animation's scene prompt
        self.assertEqual(state["prompt"], "Gentle moonlit waves on a calm alpine lake.")

        # Test persistence
        manager.save_local_theater_data(theater_dir=self.theaters_dir / theater_id)
        reloaded = self.make_canvas_state(theater_id)
        reloaded_history = reloaded.get_latest_state()["history"]
        self.assertEqual(len(reloaded_history), 4)
        self.assertEqual(reloaded_history[1]["animation"]["type"], "triframe")
        self.assertEqual(reloaded_history[2]["animation"]["type"], "video")
        self.assertEqual(reloaded_history[2]["prompt"], "A fire-breathing dragon soaring above a volcanic crater.")
        self.assertEqual(reloaded_history[3]["animation"]["type"], "layered")

    def test_canvas_template_supports_animation_history_and_prompt_widget(self):
        canvas = Path("templates/canvas.html").read_text(encoding="utf-8")
        # Ensure displayHistoryImage checks animation signature and invokes playAnimation
        self.assertIn("const animSignature = getAnimationSignature(item.animation);", canvas)
        self.assertIn("playAnimation(item.animation);", canvas)
        # Ensure prompt button reads original animation prompt
        self.assertIn("item.prompt || (item.animation && item.animation.scene_prompt)", canvas)

