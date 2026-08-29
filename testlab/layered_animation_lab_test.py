from fastapi.testclient import TestClient

from testlab.layered_animation_lab import get_layered_animation_catalog, list_piece_images
from testlab.server import app


def test_list_piece_images_discovers_samples():
    pieces = list_piece_images()
    assert isinstance(pieces, list)
    filenames = {p["filename"] for p in pieces}
    assert "bg_mountain_sky.png" in filenames
    assert "subject_crystal.png" in filenames
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
    assert {"none", "sway", "vibrate", "drift", "breathe"}.issubset(effect_ids)


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
