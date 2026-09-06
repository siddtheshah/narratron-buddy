import json
from pathlib import Path
import time
from unittest.mock import Mock, patch
import pytest

from components.canvas.visual_state import VisualState


# ---------------------------------------------------------------------------
# 1. Initial State & Defaults
# ---------------------------------------------------------------------------

def test_visual_state_starts_with_a_crossfade_presentation() -> None:
    state = VisualState()
    assert state.shown_image_path is None
    assert (state.shown_image_transition, state.shown_image_effect) == ("crossfade", "gleam3")
    assert state.current_image_basename is None
    assert state.shown_image_time == 0.0
    assert state.shown_image_prompt == ""
    assert state.shown_images_history == []
    assert state.shown_animation_frames == []
    assert state.shown_layered_animation is None
    assert state.shown_video_animation is None
    assert state.image_revision == 0


# ---------------------------------------------------------------------------
# 2. show_image Transitions, Revision & History
# ---------------------------------------------------------------------------

def test_show_image_sets_attributes_and_increments_revision() -> None:
    state = VisualState()
    before = time.time()
    changed = state.show_image(
        "scenes/mountain.png",
        transition="fade",
        effect="zoom",
        prompt="Snowy mountain peaks",
    )
    after = time.time()

    assert changed is True
    assert state.shown_image_path == "scenes/mountain.png"
    assert state.shown_image_transition == "fade"
    assert state.shown_image_effect == "zoom"
    assert state.shown_image_prompt == "Snowy mountain peaks"
    assert before <= state.shown_image_time <= after
    assert state.image_revision == 1
    assert len(state.shown_images_history) == 1

    entry = state.shown_images_history[0]
    assert isinstance(entry, dict)
    assert entry["path"] == "scenes/mountain.png"
    assert entry["url"] == "scenes/mountain.png"
    assert entry["prompt"] == "Snowy mountain peaks"
    assert entry["transition"] == "fade"
    assert entry["effect"] == "zoom"
    assert entry["time"] == state.shown_image_time


def test_show_image_uses_url_for_path_callback() -> None:
    state = VisualState()
    state.show_image(
        "images/hero.png",
        url_for_path=lambda path: f"https://cdn.example.com/{path}",
    )
    entry = state.shown_images_history[0]
    assert isinstance(entry, dict)
    assert entry["path"] == "images/hero.png"
    assert entry["url"] == "https://cdn.example.com/images/hero.png"


def test_show_image_falls_back_to_defaults_on_falsy_transition_or_effect() -> None:
    state = VisualState()
    state.show_image("bg.png", transition="", effect="")
    assert state.shown_image_transition == "crossfade"
    assert state.shown_image_effect == "gleam3"


def test_show_image_identical_call_returns_false_and_preserves_revision_and_time() -> None:
    state = VisualState()
    changed1 = state.show_image("img1.png", transition="fade", effect="zoom")
    assert changed1 is True
    first_time = state.shown_image_time
    assert state.image_revision == 1

    changed2 = state.show_image("img1.png", transition="fade", effect="zoom")
    assert changed2 is False
    assert state.shown_image_time == first_time
    assert state.image_revision == 1


def test_show_image_updating_prompt_on_same_image_updates_history_in_place() -> None:
    state = VisualState()
    state.show_image("scene.png", prompt="First draft prompt")
    assert len(state.shown_images_history) == 1
    assert state.shown_images_history[0]["prompt"] == "First draft prompt"

    changed = state.show_image("scene.png", prompt="Updated prompt")
    assert changed is False
    assert len(state.shown_images_history) == 1
    assert state.shown_images_history[0]["prompt"] == "Updated prompt"
    assert state.shown_image_prompt == "Updated prompt"


def test_show_image_changing_transition_or_effect_triggers_change() -> None:
    state = VisualState()
    state.show_image("scene.png", transition="crossfade", effect="gleam3")
    assert state.image_revision == 1

    changed_effect = state.show_image("scene.png", transition="crossfade", effect="zoom")
    assert changed_effect is True
    assert state.image_revision == 2
    assert state.shown_image_effect == "zoom"

    changed_transition = state.show_image("scene.png", transition="cut", effect="zoom")
    assert changed_transition is True
    assert state.image_revision == 3
    assert state.shown_image_transition == "cut"


def test_show_image_clears_active_animations_by_default() -> None:
    state = VisualState()
    state.show_triframe(["f1.png", "f2.png", "f3.png"], prompt="Animating frames")
    assert len(state.shown_animation_frames) == 3

    state.show_image("static.png")
    assert state.shown_animation_frames == []
    assert state.shown_layered_animation is None
    assert state.shown_video_animation is None


def test_show_image_preserves_animation_when_clear_animation_is_false() -> None:
    state = VisualState()
    custom_anim = {"type": "particle", "density": 50}
    state.show_image("space.png", clear_animation=False, animation=custom_anim)

    assert len(state.shown_images_history) == 1
    assert state.shown_images_history[0]["animation"] == custom_anim


def test_show_image_history_is_capped_at_100_entries() -> None:
    state = VisualState()
    for i in range(105):
        state.show_image(f"img_{i}.png")

    assert len(state.shown_images_history) == 100
    assert state.shown_images_history[0]["path"] == "img_5.png"
    assert state.shown_images_history[-1]["path"] == "img_104.png"


def test_show_image_empty_path_does_not_append_to_history() -> None:
    state = VisualState()
    state.show_image("")
    assert state.shown_images_history == []


# ---------------------------------------------------------------------------
# 3. show_triframe
# ---------------------------------------------------------------------------

def test_show_triframe_success() -> None:
    state = VisualState()
    frames = ["f1.png", "f2.png", "f3.png"]
    changed = state.show_triframe(frames, prompt="Walking loop")

    assert changed is True
    assert state.shown_animation_frames == frames
    assert state.shown_layered_animation is None
    assert state.shown_video_animation is None
    assert state.shown_image_path == "f1.png"
    assert state.shown_image_transition == "crossfade"
    assert state.shown_image_effect == "none"
    assert state.shown_image_prompt == "Walking loop"

    entry = state.shown_images_history[-1]
    assert entry["animation"] == {
        "type": "triframe",
        "frames": frames,
        "frame_paths": frames,
        "frame_duration_ms": 1400,
        "crossfade_duration_ms": 500,
        "scene_prompt": "Walking loop",
    }


def test_show_triframe_with_url_for_path() -> None:
    state = VisualState()
    frames = ["a.png", "b.png", "c.png"]
    state.show_triframe(frames, url_for_path=lambda p: f"/static/{p}")

    entry = state.shown_images_history[-1]
    anim = entry["animation"]
    assert anim["frames"] == ["/static/a.png", "/static/b.png", "/static/c.png"]
    assert anim["frame_paths"] == frames


@pytest.mark.parametrize(
    "invalid_frames",
    [
        [],
        ["f1.png", "f2.png"],
        ["f1.png", "f2.png", "f3.png", "f4.png"],
        ["f1.png", "", "f3.png"],
        ["", "f2.png", "f3.png"],
    ],
)
def test_show_triframe_requires_exactly_three_valid_paths(invalid_frames: list[str]) -> None:
    state = VisualState()
    with pytest.raises(ValueError, match="A tri-frame animation requires exactly three image paths."):
        state.show_triframe(invalid_frames)


# ---------------------------------------------------------------------------
# 4. show_layered_animation
# ---------------------------------------------------------------------------

def test_show_layered_animation_success() -> None:
    state = VisualState()
    manifest = {
        "id": "anim_layer_1",
        "scene_prompt": "A deep dungeon",
        "base_image": "dungeon_bg.png",
        "layers": [
            {"path": "layers/bg.png", "name": "background", "description": "stone walls", "effect": "pan", "order": 0},
            {"path": "layers/fog.png", "name": "fog", "description": "creeping mist", "effect": "float", "order": 1},
        ],
    }

    changed = state.show_layered_animation(manifest)
    assert changed is True
    assert state.shown_animation_frames == []
    assert state.shown_video_animation is None
    assert state.shown_image_path == "dungeon_bg.png"
    assert state.shown_image_transition == "crossfade"
    assert state.shown_image_effect == "none"

    assert state.shown_layered_animation is not None
    assert state.shown_layered_animation["id"] == "anim_layer_1"
    assert state.shown_layered_animation["scene_prompt"] == "A deep dungeon"
    assert len(state.shown_layered_animation["layers"]) == 2
    assert state.shown_layered_animation["layers"][0] == {
        "name": "background",
        "description": "stone walls",
        "effect": "pan",
        "order": 0,
        "url": "layers/bg.png",
    }


def test_show_layered_animation_fallbacks_and_defaults() -> None:
    state = VisualState()
    manifest = {
        "prompt": "Prompt fallback",
        "layers": [
            {"path": "first_layer.png"},
            {"path": "second_layer.png"},
        ],
    }

    state.show_layered_animation(manifest)
    assert state.shown_image_path == "first_layer.png"
    assert state.shown_layered_animation is not None
    assert state.shown_layered_animation["scene_prompt"] == "Prompt fallback"
    assert state.shown_layered_animation["layers"][0]["name"] == "layer_1"
    assert state.shown_layered_animation["layers"][0]["effect"] == "none"
    assert state.shown_layered_animation["layers"][0]["order"] == 0
    assert state.shown_layered_animation["layers"][1]["name"] == "layer_2"
    assert state.shown_layered_animation["layers"][1]["order"] == 1


def test_show_layered_animation_with_url_for_path() -> None:
    state = VisualState()
    manifest = {
        "base_image": "base.png",
        "layers": [
            {"path": "l1.png"},
            {"path": "l2.png"},
        ],
    }
    state.show_layered_animation(manifest, url_for_path=lambda p: f"/assets/{p}")
    assert state.shown_layered_animation is not None
    assert state.shown_layered_animation["layers"][0]["url"] == "/assets/l1.png"
    assert state.shown_layered_animation["layers"][1]["url"] == "/assets/l2.png"


@pytest.mark.parametrize(
    "invalid_manifest",
    [
        {},
        {"layers": "not-a-list"},
        {"layers": []},
        {"layers": [{"path": "only_one.png"}]},
    ],
)
def test_show_layered_animation_requires_at_least_two_layers(invalid_manifest: dict) -> None:
    state = VisualState()
    with pytest.raises(ValueError, match="A layered animation requires at least two layers."):
        state.show_layered_animation(invalid_manifest)


def test_show_layered_animation_requires_base_image() -> None:
    state = VisualState()
    manifest = {
        "layers": [
            {"no_path": "x"},
            {"path": "l2.png"},
        ]
    }
    with pytest.raises(ValueError, match="A layered animation requires a base image path."):
        state.show_layered_animation(manifest)


# ---------------------------------------------------------------------------
# 5. show_video_animation
# ---------------------------------------------------------------------------

def test_show_video_animation_success() -> None:
    state = VisualState()
    manifest = {
        "id": "vid_cutscene",
        "video_path": "cutscene.mp4",
        "poster_image": "cutscene_poster.png",
        "scene_prompt": "Dragon taking flight",
        "video_duration_seconds": 6.5,
        "loop": False,
        "muted": False,
    }

    changed = state.show_video_animation(manifest)
    assert changed is True
    assert state.shown_animation_frames == []
    assert state.shown_layered_animation is None
    assert state.shown_image_path == "cutscene_poster.png"
    assert state.shown_image_transition == "crossfade"
    assert state.shown_image_effect == "none"

    assert state.shown_video_animation is not None
    assert state.shown_video_animation["id"] == "vid_cutscene"
    assert state.shown_video_animation["video_url"] == "cutscene.mp4"
    assert state.shown_video_animation["local_video_url"] == "cutscene.mp4"
    assert state.shown_video_animation["fallback_url"] == "cutscene.mp4"
    assert state.shown_video_animation["poster_url"] == "cutscene_poster.png"
    assert state.shown_video_animation["video_duration_seconds"] == 6.5
    assert state.shown_video_animation["loop"] is False
    assert state.shown_video_animation["muted"] is False


def test_show_video_animation_display_target_and_duration_fallbacks() -> None:
    state = VisualState()
    # Without poster, display target falls back to video_path
    manifest1 = {"video_path": "scene.mp4", "duration_seconds": 12}
    state.show_video_animation(manifest1)
    assert state.shown_image_path == "scene.mp4"
    assert state.shown_video_animation["video_duration_seconds"] == 12

    # Without video_path, falls back to path and duration
    manifest2 = {"path": "direct.mp4", "duration": 8}
    state.show_video_animation(manifest2)
    assert state.shown_image_path == "direct.mp4"
    assert state.shown_video_animation["video_duration_seconds"] == 8

    # Default duration is 5, default loop/muted are True
    manifest3 = {"video_url": "https://example.com/stream.mp4"}
    state.show_video_animation(manifest3)
    assert state.shown_image_path == "https://example.com/stream.mp4"
    assert state.shown_video_animation["video_duration_seconds"] == 5
    assert state.shown_video_animation["loop"] is True
    assert state.shown_video_animation["muted"] is True


def test_show_video_animation_with_url_for_path() -> None:
    state = VisualState()
    manifest = {
        "video_path": "clip.mp4",
        "poster_image": "poster.png",
    }
    state.show_video_animation(manifest, url_for_path=lambda p: f"/media/{p}")
    assert state.shown_video_animation["video_url"] == "/media/clip.mp4"
    assert state.shown_video_animation["local_video_url"] == "/media/clip.mp4"
    assert state.shown_video_animation["poster_url"] == "/media/poster.png"


def test_show_video_animation_requires_video_path_or_url() -> None:
    state = VisualState()
    with pytest.raises(ValueError, match="A video animation requires a video URL or valid video path."):
        state.show_video_animation({})


# ---------------------------------------------------------------------------
# 6. serialize and load
# ---------------------------------------------------------------------------

def test_serialize_returns_all_expected_keys() -> None:
    state = VisualState()
    state.current_image_basename = "base.png"
    state.show_image("current.png", transition="fade", effect="zoom", prompt="Prompt")

    data = state.serialize()
    expected_keys = {
        "current_image_basename",
        "shown_image_path",
        "shown_image_prompt",
        "shown_images_history",
        "shown_image_transition",
        "shown_image_effect",
        "shown_animation_frames",
        "shown_layered_animation",
        "shown_video_animation",
    }
    assert set(data.keys()) == expected_keys
    assert data["current_image_basename"] == "base.png"
    assert data["shown_image_path"] == "current.png"
    assert data["shown_image_transition"] == "fade"
    assert data["shown_image_effect"] == "zoom"
    assert data["shown_image_prompt"] == "Prompt"
    assert len(data["shown_images_history"]) == 1


def test_load_restores_complete_state() -> None:
    data = {
        "current_image_basename": "cover.png",
        "shown_image_path": "library.png",
        "shown_image_prompt": "Ancient library",
        "shown_image_transition": "cut",
        "shown_image_effect": "zoom",
        "shown_images_history": [{"path": "library.png", "prompt": "Ancient library"}],
        "shown_animation_frames": ["frame1.png", "frame2.png", "frame3.png"],
        "shown_layered_animation": {"id": "layer_1", "layers": []},
        "shown_video_animation": {"id": "vid_1", "video_url": "clip.mp4"},
    }

    state = VisualState()
    state.load(data)

    assert state.current_image_basename == "cover.png"
    assert state.shown_image_path == "library.png"
    assert state.shown_image_prompt == "Ancient library"
    assert state.shown_image_transition == "cut"
    assert state.shown_image_effect == "zoom"
    assert state.shown_images_history == [{"path": "library.png", "prompt": "Ancient library"}]
    assert state.shown_animation_frames == ["frame1.png", "frame2.png", "frame3.png"]
    assert state.shown_layered_animation == {"id": "layer_1", "layers": []}
    assert state.shown_video_animation == {"id": "vid_1", "video_url": "clip.mp4"}


def test_load_handles_malformed_and_partial_data_gracefully() -> None:
    state = VisualState()
    # Populate initial non-default values
    state.current_image_basename = "kept.png"
    state.shown_image_path = "kept_path.png"

    # Load with non-string and non-list / non-dict values
    malformed = {
        "current_image_basename": 12345,
        "shown_image_path": None,
        "shown_images_history": "invalid_history",
        "shown_animation_frames": [1, "valid_frame.png", None],
        "shown_layered_animation": "not_a_dict",
        "shown_video_animation": ["not", "a", "dict"],
    }
    state.load(malformed)

    # Non-strings did not overwrite existing values
    assert state.current_image_basename == "kept.png"
    assert state.shown_image_path == "kept_path.png"
    # Malformed collections became safe defaults
    assert state.shown_images_history == []
    assert state.shown_animation_frames == ["valid_frame.png"]
    assert state.shown_layered_animation is None
    assert state.shown_video_animation is None


def test_serialize_and_load_roundtrip() -> None:
    original = VisualState()
    original.current_image_basename = "forest.png"
    original.show_triframe(["a.png", "b.png", "c.png"], prompt="Triframe test")

    serialized = original.serialize()
    restored = VisualState()
    restored.load(serialized)

    assert restored.serialize() == serialized


# ---------------------------------------------------------------------------
# 7. payload Generation
# ---------------------------------------------------------------------------

def test_payload_empty_state() -> None:
    state = VisualState()
    theater = Mock()
    p = state.payload(theater)

    assert p["latest"] is None
    assert p["time"] == 0.0
    assert p["prompt"] == ""
    assert p["transition"] == "crossfade"
    assert p["effect"] == "gleam3"
    assert p["history"] == []
    assert "animation" not in p
    theater.get_url_for_path.assert_not_called()


def test_payload_resolves_local_path_via_theater() -> None:
    state = VisualState()
    theater = Mock()
    theater.get_url_for_path.return_value = "/theaters/1/images/hero.png"

    state.show_image("local/hero.png", prompt="Hero")
    p = state.payload(theater)

    assert p["latest"] == "/theaters/1/images/hero.png"
    theater.get_url_for_path.assert_called_once_with("local/hero.png")


@pytest.mark.parametrize(
    "url_path",
    [
        "http://example.com/image.png",
        "https://cdn.example.com/image.png",
        "/theaters/th_123/image.png",
    ],
)
def test_payload_bypasses_theater_lookup_for_external_or_theater_urls(url_path: str) -> None:
    state = VisualState()
    theater = Mock()
    state.show_image(url_path)

    p = state.payload(theater)
    assert p["latest"] == url_path
    theater.get_url_for_path.assert_not_called()


def test_payload_includes_triframe_animation() -> None:
    state = VisualState()
    theater = Mock()
    theater.get_url_for_path.side_effect = lambda p: f"/url/{p}"

    frames = ["f1.png", "https://remote.com/f2.png", "/theaters/f3.png"]
    state.show_triframe(frames, prompt="Three frames")
    p = state.payload(theater)

    anim = p["animation"]
    assert anim["type"] == "triframe"
    assert anim["frame_paths"] == frames
    assert anim["frames"] == ["/url/f1.png", "https://remote.com/f2.png", "/theaters/f3.png"]
    assert anim["frame_duration_ms"] == 1400
    assert anim["crossfade_duration_ms"] == 500
    assert anim["scene_prompt"] == "Three frames"


def test_payload_includes_layered_animation() -> None:
    state = VisualState()
    theater = Mock()
    state.show_layered_animation({
        "id": "layered_scene",
        "scene_prompt": "Layered Forest",
        "layers": [{"path": "l1.png"}, {"path": "l2.png"}],
    })
    p = state.payload(theater)
    assert p["animation"]["type"] == "layered"
    assert p["animation"]["id"] == "layered_scene"


def test_payload_includes_video_animation() -> None:
    state = VisualState()
    theater = Mock()
    state.show_video_animation({
        "id": "video_scene",
        "video_path": "clip.mp4",
        "video_duration_seconds": 10,
    })
    p = state.payload(theater)
    assert p["animation"]["type"] == "video"
    assert p["animation"]["id"] == "video_scene"
    assert p["animation"]["video_duration_seconds"] == 10


# ---------------------------------------------------------------------------
# 8. Static Reference & Generation Checks
# ---------------------------------------------------------------------------

def test_has_generated_image(tmp_path: Path) -> None:
    theater = Mock()

    # Theater without image_artifacts_dir
    theater_no_dir = Mock(spec=[])
    assert VisualState._has_generated_image(theater_no_dir) is False

    # Directory does not exist
    theater.image_artifacts_dir.return_value = tmp_path / "non_existent"
    assert VisualState._has_generated_image(theater) is False

    # Directory exists but only non-image files
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "notes.txt").write_text("hello", encoding="utf-8")
    theater.image_artifacts_dir.return_value = image_dir
    assert VisualState._has_generated_image(theater) is False

    # Directory contains an image file in a subdirectory
    nested = image_dir / "generated"
    nested.mkdir()
    (nested / "render.PNG").write_text("fake_png", encoding="utf-8")
    assert VisualState._has_generated_image(theater) is True


def test_find_starting_reference(tmp_path: Path) -> None:
    theater = Mock()

    # Theater without references_dir
    theater_no_ref = Mock(spec=[])
    assert VisualState._find_starting_reference(theater_no_ref, "cover") is None

    # Directory does not exist
    theater.references_dir.return_value = tmp_path / "non_existent"
    assert VisualState._find_starting_reference(theater, "cover") is None

    # Directory populated
    ref_dir = tmp_path / "references"
    ref_dir.mkdir()
    cover_file = ref_dir / "Cover_Image.png"
    cover_file.write_text("data", encoding="utf-8")
    guard_file = ref_dir / "royal_guard_v2.webp"
    guard_file.write_text("data", encoding="utf-8")
    (ref_dir / "readme.txt").write_text("readme", encoding="utf-8")

    theater.references_dir.return_value = ref_dir

    # Exact filename match (case-insensitive)
    assert VisualState._find_starting_reference(theater, "cover_image.png") == cover_file
    # Stem match (case-insensitive)
    assert VisualState._find_starting_reference(theater, "Cover_Image") == cover_file
    # Normalized stem match (spaces/punctuation to underscores)
    assert VisualState._find_starting_reference(theater, "royal guard v2") == guard_file
    # Non-image file match rejected
    assert VisualState._find_starting_reference(theater, "readme") is None
    # No match
    assert VisualState._find_starting_reference(theater, "missing_char") is None


# ---------------------------------------------------------------------------
# 9. initialize_starting_image
# ---------------------------------------------------------------------------

def test_initialize_starting_image_skips_when_image_already_shown() -> None:
    state = VisualState()
    state.shown_image_path = "already_set.png"
    theater_mgr, theater = Mock(), Mock()

    state.initialize_starting_image("th_1", theater_mgr, theater)
    theater_mgr.assert_not_called()
    theater.references_dir.assert_not_called()


def test_initialize_starting_image_skips_when_generated_image_exists(tmp_path: Path) -> None:
    state = VisualState()
    theater_mgr, theater = Mock(), Mock()

    img_dir = tmp_path / "artifacts"
    img_dir.mkdir()
    (img_dir / "gen.jpg").write_text("fake_jpg", encoding="utf-8")
    theater.image_artifacts_dir.return_value = img_dir

    state.initialize_starting_image("th_1", theater_mgr, theater)
    assert state.shown_image_path is None


@patch("utils.config_loader.get_theater_config")
def test_initialize_starting_image_skips_when_config_raises_or_missing(mock_config: Mock) -> None:
    state = VisualState()
    theater_mgr, theater = Mock(), Mock()
    theater.image_artifacts_dir.return_value = Path("/nonexistent")

    # Exception raised by get_theater_config
    mock_config.side_effect = RuntimeError("config error")
    state.initialize_starting_image("th_1", theater_mgr, theater)
    assert state.shown_image_path is None

    # Config has no starting_image or empty string
    mock_config.side_effect = None
    mock_config.return_value = {"starting_image": "   "}
    state.initialize_starting_image("th_1", theater_mgr, theater)
    assert state.shown_image_path is None


@patch("utils.config_loader.get_theater_config")
def test_initialize_starting_image_success(mock_config: Mock, tmp_path: Path) -> None:
    state = VisualState()
    theater_mgr, theater = Mock(), Mock()
    theater.image_artifacts_dir.return_value = tmp_path / "artifacts"

    ref_dir = tmp_path / "references"
    ref_dir.mkdir()
    start_img = ref_dir / "prologue.png"
    start_img.write_text("fake_png", encoding="utf-8")
    theater.references_dir.return_value = ref_dir
    theater.get_url_for_path.return_value = "/theaters/th_1/references/prologue.png"

    mock_config.return_value = {"starting_image": "prologue"}

    state.initialize_starting_image("th_1", theater_mgr, theater)
    assert state.shown_image_path == str(start_img)
    assert state.shown_images_history[-1]["url"] == "/theaters/th_1/references/prologue.png"


# ---------------------------------------------------------------------------
# 10. Prompt and Reference Resolution Helpers
# ---------------------------------------------------------------------------

def test_resolve_prompt_for_file_with_fallback_prompt() -> None:
    state = VisualState()
    assert state._resolve_prompt_for_file("missing.png", fallback_prompt="Explicit prompt") == "Explicit prompt"


def test_resolve_prompt_for_file_missing_or_empty_path() -> None:
    state = VisualState()
    assert state._resolve_prompt_for_file(None) == ""
    assert state._resolve_prompt_for_file("") == ""
    assert state._resolve_prompt_for_file("non_existent_file_12345.png") == ""


@patch("components.canvas.visual_state.extract_image_prompt")
def test_resolve_prompt_for_file_from_image_metadata_prompt(mock_prompt: Mock, tmp_path: Path) -> None:
    img_file = tmp_path / "scene.png"
    img_file.write_text("data", encoding="utf-8")

    mock_prompt.return_value = "A neon cyberpunk cityscape"
    state = VisualState()
    assert state._resolve_prompt_for_file(str(img_file)) == "A neon cyberpunk cityscape"


@patch("components.canvas.visual_state.extract_image_prompt", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_title")
def test_resolve_prompt_for_file_from_metadata_title(mock_title: Mock, _mock_prompt: Mock, tmp_path: Path) -> None:
    img_file = tmp_path / "scene.png"
    img_file.write_text("data", encoding="utf-8")

    mock_title.return_value = "The Enchanted Woods"
    state = VisualState()
    assert state._resolve_prompt_for_file(str(img_file)) == "The Enchanted Woods"


@patch("components.canvas.visual_state.extract_image_prompt", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_title", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_description")
def test_resolve_prompt_for_file_from_metadata_description(
    mock_desc: Mock, _mock_title: Mock, _mock_prompt: Mock, tmp_path: Path
) -> None:
    img_file = tmp_path / "scene.png"
    img_file.write_text("data", encoding="utf-8")

    mock_desc.return_value = "Detailed scene description here"
    state = VisualState()
    assert state._resolve_prompt_for_file(str(img_file)) == "Detailed scene description here"


@patch("components.canvas.visual_state.extract_image_prompt", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_title", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_description", return_value="")
def test_resolve_prompt_for_file_adventure_cover(
    _d: Mock, _t: Mock, _p: Mock, tmp_path: Path
) -> None:
    img_file = tmp_path / "cover.png"
    img_file.write_text("data", encoding="utf-8")

    state = VisualState()
    theater = Mock()
    th_dir = tmp_path / "theater"
    th_dir.mkdir()
    theater.directory.return_value = th_dir
    state.theater = theater

    # Cover matches with title
    (th_dir / "metadata.json").write_text(
        json.dumps({"cover_image": "cover.png", "title": "Lost Kingdom"}),
        encoding="utf-8",
    )
    assert state._resolve_prompt_for_file(str(img_file)) == "Adventure Cover: Lost Kingdom"

    # Cover matches without title
    (th_dir / "metadata.json").write_text(
        json.dumps({"cover_image": "cover.png"}),
        encoding="utf-8",
    )
    assert state._resolve_prompt_for_file(str(img_file)) == "Adventure Cover: cover"


@patch("components.canvas.visual_state.extract_image_prompt", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_title", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_description", return_value="")
def test_resolve_prompt_for_file_adjacent_manifest(
    _d: Mock, _t: Mock, _p: Mock, tmp_path: Path
) -> None:
    img_dir = tmp_path / "anim"
    img_dir.mkdir()
    img_file = img_dir / "frame.png"
    img_file.write_text("data", encoding="utf-8")

    (img_dir / "video.json").write_text(
        json.dumps({"scene_prompt": "Storm rolling in over the ocean"}),
        encoding="utf-8",
    )
    state = VisualState()
    assert state._resolve_prompt_for_file(str(img_file)) == "Storm rolling in over the ocean"


@patch("components.canvas.visual_state.extract_image_prompt", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_title", return_value="")
@patch("components.canvas.visual_state.extract_image_metadata_description", return_value="")
def test_resolve_prompt_for_file_fallback_stem(
    _d: Mock, _t: Mock, _p: Mock, tmp_path: Path
) -> None:
    state = VisualState()

    ref_img = tmp_path / "reference_guard_captain.png"
    ref_img.write_text("data", encoding="utf-8")
    assert state._resolve_prompt_for_file(str(ref_img)) == "Reference Visual: Reference Guard Captain"

    normal_img = tmp_path / "ancient_temple_ruins.png"
    normal_img.write_text("data", encoding="utf-8")
    assert state._resolve_prompt_for_file(str(normal_img)) == "Image: Ancient Temple Ruins"


def test_resolve_reference_fallback(tmp_path: Path) -> None:
    state = VisualState()

    # theater_id not set
    state.theater_id = None
    assert state._resolve_reference_fallback() is None

    # references_dir does not exist
    state.theater_id = "th_1"
    theater = Mock()
    theater.references_dir.return_value = tmp_path / "non_existent"
    state.theater = theater
    assert state._resolve_reference_fallback() is None

    # references_dir empty
    ref_dir = tmp_path / "refs"
    ref_dir.mkdir()
    theater.references_dir.return_value = ref_dir
    assert state._resolve_reference_fallback() is None

    # Populate references dir with cover image and another image
    bg_img = ref_dir / "village_bg.png"
    bg_img.write_text("data", encoding="utf-8")
    cover_img = ref_dir / "village_cover.jpg"
    cover_img.write_text("data", encoding="utf-8")

    chosen, prompt = state._resolve_reference_fallback()
    assert chosen == cover_img
    assert "village_cover" in prompt
