"""Load canvas markup and its extracted assets for focused source/snippet tests.

These tests execute selected production functions in small browser fixtures.
Expanding only the canvas entry assets preserves their existing snippet boundaries;
full-page browser tests should serve the HTML and linked assets directly.
"""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANVAS_ASSETS = {
    "/static/css/canvas.css",
    "/static/css/canvas-action-wheel.css",
    "/static/generated/canvas/canvas.js",
}


def read_ui_source(path: Path) -> str:
    """Read a UI template, expanding the canvas's page-specific source files."""
    source = path.read_text(encoding="utf-8")

    def expand(match: re.Match[str]) -> str:
        asset_path = match.group("css") or match.group("js")
        if asset_path not in CANVAS_ASSETS:
            return match.group(0)
        asset = (PROJECT_ROOT / asset_path.lstrip("/")).read_text(encoding="utf-8")
        if match.group("css"):
            return f"<style>{asset}</style>"
        return f'<script type="module">{asset}</script>'

    return re.sub(
        r'<link rel="stylesheet" href="(?P<css>[^"]+)">'
        r'|<script type="module" src="(?P<js>[^"]+)"></script>',
        expand,
        source,
    )
