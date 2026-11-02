"""Stage game stamp commands on the same native page used by the renderer."""
from __future__ import annotations

from uuid import uuid4

from pydantic import JsonValue

from components.canvas.canvas_assets import CanvasAssetCatalog
from components.canvas.canvas_geometry import rounded
from components.canvas.canvas_observation import observe_page
from components.canvas.doodle_state import DoodleAction
from game_engine.canvas_contract import COMMANDS, CanvasSnapshot


def stage_stamps(
    effects: list[JsonValue], snapshot: CanvasSnapshot, actions: list[DoodleAction],
    catalog: CanvasAssetCatalog, package_id: str,
) -> tuple[list[DoodleAction], CanvasSnapshot]:
    staged = [dict(action) for action in actions]
    for command in COMMANDS.validate_python(effects, strict=True):
        if command.op not in {"put_stamp", "remove_stamp"}:
            raise ValueError(f"The panel canvas host does not support {command.op} yet.")
        page = snapshot.page
        if page is None or command.target.model_dump() != {"page_id": page.id}:
            raise ValueError("Stamp output must target the current shared page.")
        previous = next((item for item in staged if item.get("managed_by") == "game" and item.get("game_key") == command.key), None)
        if previous is not None and previous.get("game_package") != package_id:
            raise ValueError("A game cannot change another package's stamps.")
        if previous is not None:
            staged.remove(previous)
        if command.op == "put_stamp":
            asset = catalog.stamp(command.asset_id)
            height = max(1, rounded(command.width * asset.height * page.width / (asset.width * page.height)))
            stamp: DoodleAction = {
                "type": "stamp", "id": str(previous["id"]) if previous is not None else uuid4().hex,
                "managed_by": "game", "game_key": command.key, "game_package": package_id,
                "draggable": command.draggable, "canvas_asset_id": asset.asset_id,
                "canvas_asset_revision": asset.revision, "url": asset.url, "asset_path": asset.path,
                "x": command.center.x / 10000, "y": command.center.y / 10000,
                "rotation": command.rotation / 1000, "size": command.width / 10,
                "canvas_size": command.width / 10,
                "canvas_width": command.width, "canvas_height": height,
                "canvas_page_width": page.width, "canvas_page_height": page.height,
            }
            staged.insert(next((i for i, item in enumerate(staged) if item.get("type") != "stamp"), len(staged)), stamp)
    observed = observe_page(
        snapshot.page, staged, revision=snapshot.revision, regions=snapshot.regions,
        tray=snapshot.tray, overlay=snapshot.overlay,
    )
    return staged, observed
