"""Build with: uv run build.py (launches Blender's bundled Python via uv)."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import TypedDict


ROOT = Path(__file__).resolve().parent
Vec3 = tuple[float, float, float]


class Collider(TypedDict):
    id: str
    entity_id: str
    center: list[float]
    size: list[float]


def launch() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--skip-render", action="store_true")
    args = parser.parse_args()
    executable = args.blender or shutil.which("blender") or os.environ.get("BLENDER_PATH")
    if not executable:
        installations = sorted(Path("C:/Program Files/Blender Foundation").glob("Blender */blender.exe"))
        if not installations:
            parser.error("Set BLENDER_PATH or pass --blender with the Blender executable path.")
        executable = installations[-1]
    command = [str(executable), "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()), "--", "--inside-blender"]
    if args.skip_render:
        command.append("--skip-render")
    # Blender supplies bpy; uv still owns the project script invocation.
    subprocess.run(command, check=True)


def build() -> None:
    import bpy
    from mathutils import Vector

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.fps = 24
    scene.frame_start, scene.frame_end = 1, 49
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    scene.render.resolution_percentage = 100
    scene.world.color = (0.25, 0.25, 0.25)
    scene.view_settings.view_transform = "AgX"
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    def material(name: str, color: Vec3, metal: float = 0.0) -> bpy.types.Material:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*color, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes["Principled BSDF"]
        shader.inputs["Base Color"].default_value = (*color, 1)
        shader.inputs["Metallic"].default_value = metal
        shader.inputs["Roughness"].default_value = 0.38 if metal else 0.72
        return mat

    cream = material("Limestone", (0.63, 0.53, 0.36))
    tile = material("Ivory tiles", (0.83, 0.74, 0.55))
    teal = material("Archive teal", (0.045, 0.22, 0.23))
    sage = material("Warm plaster", (0.44, 0.52, 0.40))
    cedar = material("Cedar", (0.25, 0.095, 0.043))
    dark = material("Dark walnut", (0.085, 0.038, 0.025))
    gold = material("Brass", (0.72, 0.43, 0.095), 0.65)
    paper = material("Parchment", (0.92, 0.84, 0.63))
    rug = material("Terracotta", (0.43, 0.105, 0.055))
    blue = material("Blue linen", (0.08, 0.29, 0.37))
    ink = material("Ink", (0.018, 0.038, 0.044))
    book_colors = [rug, teal, paper, blue, sage]
    colliders: list[Collider] = []

    def xyz(position: Vec3) -> list[float]:
        return [round(position[0], 4), round(position[2], 4), round(-position[1], 4)]

    def empty(name: str, position: Vec3, kind: str) -> bpy.types.Object:
        item = bpy.data.objects.new(name, None)
        scene.collection.objects.link(item)
        item.location = position
        item.empty_display_type = "ARROWS"
        item.empty_display_size = 0.3
        item["entity_id"] = name
        item["kind"] = kind
        return item

    environment = empty("environment", (0, 0, 0), "environment")

    def box(name: str, position: Vec3, size: Vec3, mat: bpy.types.Material, parent: bpy.types.Object = environment, bevel: float = 0.02) -> bpy.types.Object:
        bpy.ops.mesh.primitive_cube_add(size=1)
        item = bpy.context.object
        item.name = name
        item.parent = parent
        item.location = position
        item.dimensions = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        item.data.materials.append(mat)
        if bevel:
            modifier = item.modifiers.new("Soft edges", "BEVEL")
            modifier.width, modifier.segments = bevel, 2
            item.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
        return item

    def collider(name: str, position: Vec3, size: Vec3, entity_id: str = "environment") -> None:
        colliders.append({"id": name, "entity_id": entity_id, "center": xyz(position), "size": [size[0], size[2], size[1]]})
        marker = empty(name, position, "collider")
        marker["shape"] = "box"
        marker["size_x"], marker["size_y"], marker["size_z"] = size
        marker["owner_entity_id"] = entity_id
        marker.hide_render = True
        marker.parent = environment if entity_id == "environment" else bpy.data.objects[entity_id]

    # Two six-metre rooms. Low front walls are an intentional dollhouse cutaway.
    box("foundation", (0, 0, -0.24), (12.5, 6.5, 0.45), dark, bevel=0.08)
    for x in range(12):
        for y in range(6):
            box(f"tile_{x}_{y}", (x-5.5, y-2.5, -0.045), (0.975, 0.975, 0.09), tile if (x+y) % 3 else cream, bevel=0.008)
    for name, pos, size, mat in [
        ("back_west", (-3, 3.1, 1.6), (6.2, 0.2, 3.2), sage),
        ("back_east", (3, 3.1, 1.6), (6.2, 0.2, 3.2), teal),
        ("west_wall", (-6.1, 0, 1.6), (0.2, 6.2, 3.2), sage),
        ("east_wall", (6.1, 0, 1.6), (0.2, 6.2, 3.2), teal),
        ("front_west", (-3, -3.1, 0.23), (6.0, 0.2, 0.46), sage),
        ("front_east", (3, -3.1, 0.23), (6.0, 0.2, 0.46), teal),
        ("divider_back", (0, 2.0, 1.6), (0.2, 2, 3.2), sage),
        ("divider_front", (0, -2.0, 1.6), (0.2, 2, 3.2), sage),
        ("door_lintel", (0, 0, 2.9), (0.22, 2.0, 0.6), sage),
    ]:
        box(name, pos, size, mat)
        collider(f"collision_{name}", pos, size)
    collider("collision_floor", (0, 0, -0.1), (12, 6, 0.2))
    for y in [-3.0, 3.0]:
        box(f"brass_edge_{y}", (0, y, 0.1), (12, 0.045, 0.12), gold)
    for x in [-6, 0, 6]:
        for y in [-3, 3]:
            box(f"pillar_{x}_{y}", (x, y, 1.65), (0.26, 0.26, 3.3), cream)
            box(f"capital_{x}_{y}", (x, y, 3.17), (0.4, 0.4, 0.13), gold)
    for y in [-1.0, 1.0]:
        box(f"door_jamb_{y}", (0.0, y, 1.32), (0.36, 0.14, 2.64), gold)
    box("door_crown", (0, 0, 2.64), (0.36, 2.15, 0.15), gold)
    box("west_rug", (-3, -0.4, 0.012), (3.8, 3.7, 0.024), rug)
    box("west_rug_inset", (-3, -0.4, 0.027), (3.5, 3.4, 0.008), cream)
    box("west_rug_field", (-3, -0.4, 0.033), (3.38, 3.28, 0.008), rug)
    box("east_runner", (3, -0.3, 0.013), (2.5, 4.2, 0.026), blue)
    for x in [2, 4]:
        box(f"runner_stripe_{x}", (x, -0.3, 0.03), (0.035, 3.95, 0.008), gold)

    # Shelving and modest, deterministic book blocks: no textures or downloads.
    for shelf_x in [-4.6, -1.65, 1.6, 4.6]:
        box(f"shelf_back_{shelf_x}", (shelf_x, 2.83, 1.4), (2.35, 0.12, 2.8), dark)
        collider(f"collision_shelf_{shelf_x}", (shelf_x, 2.57, 1.4), (2.4, 0.7, 2.8))
        for dx in [-1.16, 1.16]:
            box(f"shelf_side_{shelf_x}_{dx}", (shelf_x+dx, 2.6, 1.4), (0.1, 0.6, 2.8), cedar)
        for level in range(5):
            z = 0.12 + level*0.64
            box(f"shelf_{shelf_x}_{level}", (shelf_x, 2.6, z), (2.4, 0.64, 0.09), cedar)
            if level < 4:
                for index in range(11):
                    height = 0.32 + ((index*3+level) % 5)*0.045
                    bx = shelf_x-1.01+index*0.193
                    box(f"book_{shelf_x}_{level}_{index}", (bx, 2.55, z+0.05+height/2), (0.145, 0.36, height), book_colors[(index+level) % 5], bevel=0.008)
                    box(f"spine_band_{shelf_x}_{level}_{index}", (bx, 2.365, z+0.12), (0.12, 0.008, 0.025), gold, bevel=0)

    table = empty("desk", (-3, 0, 0), "entity")
    box("desk_top", (0, 0, 0.98), (2.25, 1.25, 0.14), cedar, table, 0.045)
    for x in [-0.92, 0.92]:
        for y in [-0.43, 0.43]:
            box(f"desk_leg_{x}_{y}", (x, y, 0.46), (0.13, 0.13, 0.92), dark, table)
    box("desk_drawer", (0, -0.1, 0.8), (1.65, 0.85, 0.25), cedar, table)
    box("drawer_handle", (0, -0.55, 0.81), (0.3, 0.06, 0.045), gold, table)
    for i in range(3):
        sheet = box(f"draft_{i}", (-0.55+i*0.11, 0.12, 1.058+i*0.008), (0.55, 0.65, 0.006), paper, table, 0)
        sheet.rotation_euler.z = 0.12*i
    box("inkwell", (-0.85, 0.4, 1.13), (0.13, 0.13, 0.14), ink, table)
    collider("collision_desk", (0, 0, 0.52), (2.25, 1.25, 1.04), "desk")

    key = empty("brass_key", (-2.47, -0.12, 1.08), "entity")
    bpy.ops.mesh.primitive_torus_add(major_radius=0.105, minor_radius=0.024, major_segments=24, minor_segments=8)
    ring = bpy.context.object
    ring.name, ring.parent, ring.location = "key_bow", key, (-0.12, 0, 0)
    ring.data.materials.append(gold)
    box("key_shaft", (0.08, 0, 0), (0.27, 0.045, 0.045), gold, key, 0.008)
    for x in [0.14, 0.21]:
        box(f"key_tooth_{x}", (x, -0.045, 0), (0.04, 0.1, 0.045), gold, key, 0.006)

    chest = empty("archive_chest", (3.2, 1.0, 0), "entity")
    box("chest_body", (0, 0, 0.36), (1.35, 0.8, 0.64), cedar, chest, 0.06)
    box("chest_lid", (0, 0, 0.76), (1.4, 0.85, 0.2), dark, chest, 0.06)
    for x in [-0.46, 0.46]:
        box(f"chest_band_{x}", (x, 0, 0.785), (0.065, 0.86, 0.18), gold, chest)
        box(f"chest_strap_{x}", (x, -0.409, 0.36), (0.065, 0.025, 0.64), gold, chest)
    box("chest_lock", (0, -0.44, 0.59), (0.17, 0.065, 0.23), gold, chest)
    box("chest_keyhole", (0, -0.478, 0.59), (0.025, 0.008, 0.065), ink, chest)
    collider("collision_chest", (0, 0, 0.45), (1.4, 0.86, 0.9), "archive_chest")

    door = empty("archive_door", (0, -0.9, 0), "entity")
    hinge = empty("door_hinge", (0, 0, 0), "animation_pivot")
    hinge.parent = door
    box("door_panel", (0, 0.9, 1.27), (0.12, 1.8, 2.54), teal, hinge, 0.035)
    for y in [0.13, 1.67]:
        box(f"door_stile_{y}", (-0.078, y, 1.27), (0.055, 0.07, 2.35), gold, hinge)
    for z in [0.18, 1.25, 2.35]:
        box(f"door_rail_{z}", (-0.078, 0.9, z), (0.055, 1.55, 0.07), gold, hinge)
    for x in [-0.13, 0.13]:
        box(f"door_handle_{x}", (x, 1.48, 1.15), (0.15, 0.12, 0.085), gold, hinge)
    collider("collision_closed_door", (0, 0.9, 1.27), (0.18, 1.8, 2.54), "archive_door")
    hinge.rotation_euler.z = 0
    hinge.keyframe_insert(data_path="rotation_euler", frame=1)
    hinge.rotation_euler.z = math.radians(-100)
    hinge.keyframe_insert(data_path="rotation_euler", frame=49)
    hinge.animation_data.action.name = "door_open"
    scene.frame_set(1)

    # Named non-rendering markers survive as GLB extras; metadata is independent.
    spawn_positions = {"entry": (-4.4, -2.0, 0), "archive_entry": (1.25, 0, 0)}
    for name, position in spawn_positions.items():
        empty(f"spawn_{name}", position, "spawn")
    targets = {"inspect_desk": (-3, -0.65, 1), "collect_key": (-2.47, -0.12, 1.08), "unlock_door": (-0.2, 0.58, 1.15), "inspect_chest": (3.2, 0.56, 0.59)}
    for name, position in targets.items():
        empty(f"target_{name}", position, "interaction_target")
    for name, center, size in [("discover_key", (-3, -1.3, 0.9), (2.8, 1.3, 1.8)), ("enter_archive", (1.15, 0, 0.9), (1.0, 1.8, 1.8))]:
        marker = empty(f"trigger_{name}", center, "trigger")
        marker["size_x"], marker["size_y"], marker["size_z"] = size

    def export(path: Path, selected: list[bpy.types.Object] | None = None) -> None:
        bpy.ops.object.select_all(action="DESELECT")
        if selected is not None:
            for item in selected:
                item.select_set(True)
        bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=selected is not None, export_extras=True, export_yup=True, export_apply=True, export_animations=True, export_animation_mode="ACTIONS", export_anim_slide_to_zero=True, export_cameras=False, export_lights=False)

    export(ROOT / "library.glb")
    export(ROOT / "environment.glb", [environment, *environment.children_recursive])
    for entity in [table, key, chest, door]:
        original = entity.location.copy()
        entity.location = (0, 0, 0)
        export(ROOT / f"{entity.name}.glb", [entity, *entity.children_recursive])
        entity.location = original
    download_bytes = (ROOT / "library.glb").stat().st_size
    assert download_bytes < 5_000_000, "Initial scene exceeds the 5 MB target"
    manifest = {
        "format": "narratron-world-prototype", "version": 1,
        "world_id": "library_of_origins", "revision": "1",
        "status": "asset prototype; spatial runtime integration is not implemented",
        "coordinates": {"unit": "metre", "up": "+Y", "east": "+X", "north": "-Z", "origin": "floor at center of connecting doorway", "blender_to_gltf": "(x, y, z) -> (x, z, -y)", "game_mm": "multiply every position and size by 1000; round to integer"},
        "scene": "library.glb", "environment": "environment.glb", "initial_download_bytes": download_bytes,
        "player": {"radius": 0.25, "height": 1.7, "eye_height": 1.6},
        "spawns": {name: {"position": xyz(position), "yaw_radians": 0} for name, position in spawn_positions.items()},
        "regions": [
            {"id": "drafting_room", "min": [-6, 0, -3], "max": [0, 3.2, 3]},
            {"id": "archive", "min": [0, 0, -3], "max": [6, 3.2, 3]},
        ],
        "entities": [
            {"id": item.name, "model": f"{item.name}.glb", "position": xyz(tuple(item.location)), "rotation_xyzw": [0, 0, 0, 1], "scale": [1, 1, 1]}
            for item in [table, key, chest, door]
        ],
        "colliders": colliders,
        "collider_space": "entity-local for entities, world for environment; boxes are axis-aligned",
        "interactions": [
            {"id": name, "entity_id": entity, "position": xyz(targets[name]), "reach": 1.8, "intent": intent}
            for name, entity, intent in [("inspect_desk", "desk", "inspect"), ("collect_key", "brass_key", "collect"), ("unlock_door", "archive_door", "unlock"), ("inspect_chest", "archive_chest", "inspect")]
        ],
        "interaction_space": "world",
        "triggers": [
            {"id": "discover_key", "center": xyz((-3, -1.3, 0.9)), "size": [2.8, 1.8, 1.3], "intent": "discover_key"},
            {"id": "enter_archive", "center": xyz((1.15, 0, 0.9)), "size": [1, 1.8, 1.8], "intent": "enter_archive"},
        ],
        "animations": [{"entity_id": "archive_door", "clip": "door_open", "duration_seconds": 2, "closed_time": 0, "open_time": 2, "loop": False, "pivot_node": "door_hinge"}],
        "behavior_notes": ["Key collection removes brass_key only after a server-validated transition.", "Unlocking plays door_open and disables collision_closed_door in the same accepted transition.", "The chest is an independent static prop; no chest opening animation or game logic is supplied.", "Front walls are cut away; colliders follow the authored low walls. A host must also enforce the room bounds.", "No geometry or metadata grants inventory, movement permissions, or progression automatically."],
    }
    (ROOT / "world.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # Presentation rig is saved in Blender but deliberately excluded from GLB.
    presentation = bpy.data.collections.new("Presentation (not exported)")
    scene.collection.children.link(presentation)

    def presentation_only(item: bpy.types.Object) -> None:
        for collection in list(item.users_collection):
            collection.objects.unlink(item)
        presentation.objects.link(item)

    ground = box("display_ground", (0, 0, -0.55), (200, 200, 0.1), material("Backdrop", (0.065, 0.10, 0.12)), bevel=0)
    ground.parent = None
    presentation_only(ground)

    def area(name: str, position: Vec3, power: float, color: Vec3, size: float) -> None:
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.color, data.shape, data.size = power, color, "DISK", size
        light = bpy.data.objects.new(name, data)
        presentation.objects.link(light)
        light.location = position
        light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat("-Z", "Y").to_euler()

    area("Warm skylight", (-4, -3, 10), 1900, (1, 0.81, 0.58), 8)
    area("Cool archive fill", (5, 1, 8), 1500, (0.65, 0.82, 1), 6)
    area("Front fill", (1, -8, 6), 900, (1, 0.94, 0.84), 7)
    camera_data = bpy.data.cameras.new("Overview camera")
    camera = bpy.data.objects.new("Overview camera", camera_data)
    presentation.objects.link(camera)
    camera.location = (12, -18, 15)
    camera.rotation_euler = (Vector((0, 0, 0.65)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type, camera_data.ortho_scale = "ORTHO", 19
    scene.camera = camera
    # Save a useful material-view camera layout on opening the source file.
    for screen in bpy.data.screens:
        for area_ui in screen.areas:
            if area_ui.type == "VIEW_3D":
                area_ui.spaces.active.region_3d.view_perspective = "CAMERA"
                area_ui.spaces.active.shading.type = "MATERIAL"
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "library.blend"))
    if "--skip-render" not in sys.argv:
        scene.render.filepath = str(ROOT / "preview.png")
        bpy.ops.render.render(write_still=True)
        scene.frame_set(49)
        camera.location = (-5.5, -6.2, 4.7)
        camera.rotation_euler = (Vector((-1.25, 0, 0.95)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.ortho_scale = 7.4
        scene.render.resolution_x, scene.render.resolution_y = 1200, 1000
        scene.render.filepath = str(ROOT / "preview_door_open.png")
        bpy.ops.render.render(write_still=True)
    print(f"WORLD_BUILD_OK: {download_bytes:,} bytes; source, GLBs, and manifest saved to {ROOT}")


if __name__ == "__main__":
    if "--inside-blender" in sys.argv:
        build()
    else:
        launch()
