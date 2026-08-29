"""Backend helpers and catalog definitions for Layered Animation Lab in testlab."""

from pathlib import Path
from typing import Any
from PIL import Image

ROOT = Path(__file__).resolve().parent
PIECES_DIR = ROOT / "images" / "pieces"

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg"}


def list_piece_images() -> list[dict[str, Any]]:
    """Scan testlab/images/pieces for image assets and return their metadata."""
    PIECES_DIR.mkdir(parents=True, exist_ok=True)
    pieces = []

    for filepath in sorted(PIECES_DIR.iterdir()):
        if filepath.is_file() and filepath.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            width, height, has_alpha = 0, 0, False
            try:
                with Image.open(filepath) as img:
                    width, height = img.size
                    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                        img.mode == "P" and "transparency" in img.info
                    )
            except Exception:
                pass

            pieces.append({
                "id": filepath.stem,
                "filename": filepath.name,
                "url": f"/test-images/pieces/{filepath.name}",
                "width": width,
                "height": height,
                "has_alpha": has_alpha,
                "size_bytes": filepath.stat().st_size,
            })

    return pieces


def get_layered_animation_catalog() -> dict[str, Any]:
    """Return catalog of available pieces, supported motion effects, and default presets."""
    pieces = list_piece_images()

    effects = [
        {"id": "none", "name": "None (Static)", "description": "No motion transform"},
        {"id": "sway", "name": "Sway (Foliage Bending)", "description": "Bottom-anchored bending curvature distorting around bottom of bounding box"},
        {"id": "gentle_rocking", "name": "Gentle Rocking", "description": "Gentle rotational oscillation wave"},
        {"id": "vibrate", "name": "Vibrate", "description": "Rapid high-frequency jitter motion"},
        {"id": "pulse", "name": "Pulse", "description": "Subtle expanding and contracting scale pulse"},
        {"id": "twist", "name": "Twist (S-Curve)", "description": "Radially proportional torque distorting around centroid (opposite ends curve opposite)"},
        {"id": "bend", "name": "Bend (C-Curve)", "description": "Synchronous same-direction curvature distorting around centroid"},
    ]

    image_effects = [
        {"id": "none", "name": "None"},
        {"id": "gleam3", "name": "Gleam 3"},
        {"id": "haze", "name": "Drifting haze"},
        {"id": "trace", "name": "Light trace"},
        {"id": "sparkle", "name": "Starlight twinkle"},
        {"id": "bendy", "name": "Bendy"},
        {"id": "creeping", "name": "Creeping darkness"},
        {"id": "dream", "name": "Cloudy dreams"},
    ]

    # Sample presets that use discovered pieces if present
    piece_map = {p["filename"]: p for p in pieces}
    presets = []

    if "bg_mountain_sky.png" in piece_map and "subject_crystal.png" in piece_map:
        presets.append({
            "id": "mystic-crystal",
            "name": "Mystic Crystal & Floating Orbs",
            "description": "3-layer stack featuring static background, pulsing crystal subject, and floating ambient orbs.",
            "layers": [
                {
                    "name": "Background Sky",
                    "piece_id": "bg_mountain_sky",
                    "filename": "bg_mountain_sky.png",
                    "url": piece_map["bg_mountain_sky.png"]["url"],
                    "effect": "none",
                    "speed": 1.0,
                    "amplitude": 1.0,
                    "opacity": 1.0,
                    "order": 0,
                },
                {
                    "name": "Glowing Crystal",
                    "piece_id": "subject_crystal",
                    "filename": "subject_crystal.png",
                    "url": piece_map["subject_crystal.png"]["url"],
                    "effect": "pulse",
                    "speed": 1.2,
                    "amplitude": 1.5,
                    "opacity": 1.0,
                    "order": 1,
                },
                {
                    "name": "Ambient Orbs",
                    "piece_id": "fg_floating_orbs",
                    "filename": "fg_floating_orbs.png",
                    "url": piece_map.get("fg_floating_orbs.png", {}).get("url", ""),
                    "effect": "gentle_rocking",
                    "speed": 0.8,
                    "amplitude": 1.2,
                    "opacity": 0.9,
                    "order": 2,
                },
            ]
        })

    if "subject_stick_figure_sword.png" in piece_map:
        presets.append({
            "id": "sword-hero-twist",
            "name": "Jumping Sword Hero (Twist & Bend)",
            "description": "Dynamic stick figure hero leaping into battle with cyclical torque distortion.",
            "layers": [
                {
                    "name": "Background Sky",
                    "piece_id": "bg_mountain_sky",
                    "filename": "bg_mountain_sky.png",
                    "url": piece_map.get("bg_mountain_sky.png", {}).get("url", ""),
                    "effect": "none",
                    "speed": 1.0,
                    "amplitude": 1.0,
                    "opacity": 1.0,
                    "order": 0,
                },
                {
                    "name": "Stick Figure Hero",
                    "piece_id": "subject_stick_figure_sword",
                    "filename": "subject_stick_figure_sword.png",
                    "url": piece_map["subject_stick_figure_sword.png"]["url"],
                    "effect": "twist",
                    "speed": 1.2,
                    "amplitude": 1.4,
                    "opacity": 1.0,
                    "order": 1,
                },
            ]
        })

    if "subject_palm_tree.png" in piece_map or "palm_tree.png" in piece_map:
        target_fn = "subject_palm_tree.png" if "subject_palm_tree.png" in piece_map else "palm_tree.png"
        presets.append({
            "id": "tropical-breeze-sway",
            "name": "Swaying Palm Tree (Tropical Breeze)",
            "description": "Palm tree swaying gently in a tropical breeze.",
            "layers": [
                {
                    "name": "Background Sky",
                    "piece_id": "bg_mountain_sky",
                    "filename": "bg_mountain_sky.png",
                    "url": piece_map.get("bg_mountain_sky.png", {}).get("url", ""),
                    "effect": "none",
                    "speed": 1.0,
                    "amplitude": 1.0,
                    "opacity": 1.0,
                    "order": 0,
                },
                {
                    "name": "Palm Tree",
                    "piece_id": piece_map[target_fn]["id"],
                    "filename": target_fn,
                    "url": piece_map[target_fn]["url"],
                    "effect": "sway",
                    "speed": 0.9,
                    "amplitude": 1.3,
                    "opacity": 1.0,
                    "order": 1,
                },
            ]
        })

    return {
        "pieces": pieces,
        "effects": effects,
        "image_effects": image_effects,
        "presets": presets,
    }
