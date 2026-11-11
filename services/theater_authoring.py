"""Package public, standalone theater authoring instructions for external AI apps."""

import io
from pathlib import Path
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILES = ("plugin.json", "AGENTS.md", "README.md", "skills/create-theater/SKILL.md")
REFERENCE_FILES = ("writing_adventures.md", "game_packages.md", "game_html_panels.md", "game_canvas_interface.md")


def authoring_kit() -> bytes:
    """Build a kit from an explicit public allowlist, independent of user theaters."""
    buffer = io.BytesIO()
    plugin_root = PROJECT_ROOT / "plugins" / "narratron-theater"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in PLUGIN_FILES:
            archive.write(plugin_root / relative, f"narratron-theater/{relative}")
        for relative in REFERENCE_FILES:
            archive.write(PROJECT_ROOT / "site_docs" / relative,
                          f"narratron-theater/skills/create-theater/references/{relative}")
        archive.write(PROJECT_ROOT / "theater_default.yaml",
                      "narratron-theater/skills/create-theater/assets/theater_default.yaml")
        archive.writestr(".agents/plugins/marketplace.json", '''{
  "name": "narratron-authoring",
  "plugins": [{
    "name": "narratron-theater",
    "source": {"source": "local", "path": "./narratron-theater"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "interface": {"displayName": "Narratron Theater Authoring"}
  }]
}
''')
    return buffer.getvalue()
