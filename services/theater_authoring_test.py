"""Verify the standalone authoring kit includes its referenced resources."""

import io
import json
import re
import zipfile

import yaml

from services.theater_authoring import authoring_kit


def test_authoring_kit_is_self_contained_and_public() -> None:
    with zipfile.ZipFile(io.BytesIO(authoring_kit())) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("narratron-theater/plugin.json"))
        assert manifest["name"] == "narratron-theater"
        catalog = json.loads(archive.read(".agents/plugins/marketplace.json"))
        source = catalog["plugins"][0]["source"]["path"].removeprefix("./")
        assert f"{source}/plugin.json" in names
        skill_root = f"{source}/skills/create-theater"
        skill = archive.read(f"{skill_root}/SKILL.md").decode("utf-8")
        for relative in re.findall(r"\]\(((?:references|assets)/[^)]+)\)", skill):
            assert f"{skill_root}/{relative}" in names
        defaults = yaml.safe_load(archive.read(f"{skill_root}/assets/theater_default.yaml"))
        assert "story_planning" in defaults
        assert all(not name.startswith(("theaters/", "output/")) for name in names)
        assert "theater.yaml" not in names
