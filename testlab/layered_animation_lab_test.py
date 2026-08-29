from fastapi.testclient import TestClient

from testlab.layered_animation_lab import get_layered_animation_catalog, list_piece_images
from testlab.server import app


def test_list_piece_images_discovers_samples():
    pieces = list_piece_images()
    assert isinstance(pieces, list)
    filenames = {p["filename"] for p in pieces}
    assert "bg_mountain_sky.png" in filenames
    assert "subject_crystal.png" in filenames
    assert ("subject_palm_tree.png" in filenames or "palm_tree.png" in filenames)
    for piece in pieces:
        assert "url" in piece
        assert "has_alpha" in piece
        assert piece["url"].startswith("/test-images/pieces/")


def test_get_layered_animation_catalog_returns_effects_and_presets():
    catalog = get_layered_animation_catalog()
    assert "pieces" in catalog
    assert "effects" in catalog
    assert "image_effects" in catalog
    assert "presets" in catalog
    effect_ids = {e["id"] for e in catalog["effects"]}
    assert {"none", "sway", "gentle_rocking", "vibrate", "pulse", "twist", "bend", "light_halo", "dark_halo", "ghostly", "reflective", "mirage", "energy_blast"}.issubset(effect_ids)
    sway_entry = next(e for e in catalog["effects"] if e["id"] == "sway")
    assert sway_entry["name"] == "Sway (Foliage Bending)"
    assert "Bottom-anchored" in sway_entry["description"]
    rocking_entry = next(e for e in catalog["effects"] if e["id"] == "gentle_rocking")
    assert rocking_entry["name"] == "Gentle Rocking"
    light_halo_entry = next(e for e in catalog["effects"] if e["id"] == "light_halo")
    assert light_halo_entry["name"] == "Light Halo"
    dark_halo_entry = next(e for e in catalog["effects"] if e["id"] == "dark_halo")
    assert dark_halo_entry["name"] == "Dark Halo"
    ghostly_entry = next(e for e in catalog["effects"] if e["id"] == "ghostly")
    assert ghostly_entry["name"] == "Ghostly"
    reflective_entry = next(e for e in catalog["effects"] if e["id"] == "reflective")
    assert reflective_entry["name"] == "Reflective (Light Sheen)"
    mirage_entry = next(e for e in catalog["effects"] if e["id"] == "mirage")
    assert mirage_entry["name"] == "Mirage (Heat & Smoke Distortion)"
    eb_entry = next(e for e in catalog["effects"] if e["id"] == "energy_blast")
    assert eb_entry["name"] == "Energy Blast"
    assert "Jagged traveling wave fronts" in eb_entry["description"]


def test_layered_animation_lab_routes():
    client = TestClient(app)
    
    # HTML endpoint
    page_res = client.get("/layered-animation")
    assert page_res.status_code == 200
    assert "Layered Animation Lab" in page_res.text
    
    # Catalog API endpoint
    catalog_res = client.get("/api/layered-animation/catalog")
    assert catalog_res.status_code == 200
    catalog_data = catalog_res.json()
    assert "pieces" in catalog_data
    assert "effects" in catalog_data

    # Pieces API endpoint
    pieces_res = client.get("/api/layered-animation/pieces")
    assert pieces_res.status_code == 200
    pieces_data = pieces_res.json()
    assert "pieces" in pieces_data
