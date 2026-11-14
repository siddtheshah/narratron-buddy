"""Build the browser assets used by standalone testlab route tests."""

import pytest

from utils.frontend_build import ensure_canvas_assets_built


@pytest.fixture(scope="session", autouse=True)
def ensure_generated_canvas_assets() -> None:
    ensure_canvas_assets_built()
