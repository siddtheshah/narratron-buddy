"""Theater deployer, asset routes, baton passing, and theater management API endpoints."""

import asyncio
from copy import deepcopy
import json
import logging
from typing import Optional
import uuid
import yaml

from fastapi import Request, Response, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api_server.shared import (
    app,
    db,
    theater_manager,
    canvas_states,
    get_current_user,
    get_current_user_async,
    _require_canvas_access_async,
    _safe_path_param,
    _grant_canvas_access,
    PROJECT_ROOT
)
from api_server.dependencies import agent_manager, suggestion_service
from utils.auth_cache import auth_session_cache
from api_server.theater_access_cache import theater_access_cache
from components.theater_manager import MAX_LORE_DOCUMENT_BYTES, TheaterMetadata, extract_asset_package
from utils.config_loader import get_theater_config, get_theater_default_config
from services.adventure_service import adventure_service


logger = logging.getLogger(__name__)



async def _sync_agent_controller(theater_id: str, baton_state: dict) -> None:
    """Update the controller without disconnecting the live agent session."""
    active_orator = baton_state.get("active_orator") or {}
    active_orator_id = active_orator.get("id")
    if active_orator_id is not None:
        agent_manager.set_active_controller(theater_id, active_orator_id)


class ResolveJoinKeyRequest(BaseModel):
    join_key: str

class SaveTheaterConfigRequest(BaseModel):
    config_yaml: str

class RetitleTheaterRequest(BaseModel):
    name: str

class AddAllowedOratorRequest(BaseModel):
    target_user_id: int

class RequestBatonRequest(BaseModel):
    target_user_id: int
    timeout_seconds: Optional[int] = 30


# ========================================
# Theater Asset Dynamic Routes
# ========================================

@app.get("/theaters/{theater_id}/references/{filename:path}")
async def serve_theater_reference(request: Request, theater_id: str, filename: str):
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    ref_dir = theater_manager.theater(theater_id).references_dir()
    file_path = (ref_dir / filename).resolve()
    if ref_dir.resolve() not in file_path.parents and file_path != ref_dir:
        raise HTTPException(status_code=400, detail="Invalid reference path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Theater reference file not found")
    return FileResponse(file_path)

@app.get("/theaters/{theater_id}/playlists/{playlist_name}/{filename}")
async def serve_theater_playlist_track(request: Request, theater_id: str, playlist_name: str, filename: str):
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    _safe_path_param(playlist_name, "playlist_name")
    _safe_path_param(filename, "filename")
    file_path = theater_manager.theater(theater_id).playlists_dir() / playlist_name / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Theater playlist track not found")
    return FileResponse(file_path)

@app.get("/theaters/{theater_id}/output/{filename:path}")
async def serve_theater_output(request: Request, theater_id: str, filename: str):
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")
    output_dir = theater_manager.theater(theater_id).output_dir()
    file_path = (output_dir / filename).resolve()
    if output_dir.resolve() not in file_path.parents:
        raise HTTPException(status_code=400, detail="Invalid output path")
    if not file_path.exists():
        # Check subdirectories of output directory (e.g. output/images/filename)
        sub_path = output_dir / "images" / filename
        if sub_path.exists():
            file_path = sub_path
        else:
            found = list(output_dir.rglob(filename))
            if found:
                file_path = found[0]
            else:
                raise HTTPException(status_code=404, detail="Theater output file not found")
    return FileResponse(file_path)

# ========================================
# Deployer & Theater API Endpoints
# ========================================

@app.post("/api/theaters/resolve-join-key")
def resolve_join_key(req: ResolveJoinKeyRequest, request: Request, response: Response):
    dep = db.get_theater_by_join_key(req.join_key)
    if not dep:
        raise HTTPException(status_code=404, detail="Invalid Join Key. No matching active theater found.")

    meta = theater_manager.get_theater(dep["theater_id"])
    if not meta:
        db_meta = db.get_theater_metadata_from_db(dep["theater_id"])
        if not db_meta:
            raise HTTPException(status_code=404, detail="Theater files no longer exist.")
        meta = TheaterMetadata(**db_meta)

    _grant_canvas_access(response, request, meta.theater_id, dep["join_key"])
    return {"status": "ok", "theater_id": meta.theater_id, "name": meta.name, "user_id": dep.get("user_id")}

@app.get("/api/theaters")
def list_theaters(request: Request):
    """List all deployed theaters from disk and database without eagerly reconstructing files."""
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    if current_user_id is not None:
        # The deploy UI only shows personal sessions for authenticated users.  Do
        # not load every user's disk and database metadata merely to filter it in
        # the browser afterwards.
        result = []
        for record in db.get_user_theater_records(current_user_id):
            theater_id = record["theater_id"]
            disk_metadata = theater_manager.get_theater(theater_id)
            theater = disk_metadata.model_dump() if disk_metadata else record["metadata"]
            theater["is_owner"] = True
            theater["last_used_at"] = record["last_used_at"] or theater.get("created_at") or ""
            result.append(theater)
        result.sort(key=lambda theater: theater.get("last_used_at", "") or theater.get("created_at", ""), reverse=True)
        return result

    # Get theaters currently on disk
    disk_theaters = theater_manager.list_theaters()
    all_theaters_dict = {s.theater_id: s.model_dump() for s in disk_theaters}

    # Add DB theaters that are not on disk yet (without writing files to disk!)
    for sid in db.get_all_exported_theater_ids():
        if sid not in all_theaters_dict:
            db_meta = db.get_theater_metadata_from_db(sid)
            if db_meta:
                all_theaters_dict[sid] = db_meta

    # Fetch last_used timestamps map from DB
    activity_map = db.get_theaters_last_used()
    deployments_by_theater = db.get_deployments(list(all_theaters_dict))

    result = []
    for sid, s_dict in all_theaters_dict.items():
        dep = deployments_by_theater.get(sid)
        owner_id = dep["user_id"] if dep else None
        is_owner = (current_user_id is not None and owner_id == current_user_id)

        s_dict["is_owner"] = is_owner
        last_used = activity_map.get(sid) or s_dict.get("created_at") or ""
        s_dict["last_used_at"] = last_used

        # Hide join_key if not owner
        if not is_owner:
            s_dict["join_key"] = "🔒 Owner Only"
            
        result.append(s_dict)

    # Sort: owned theaters first, then by last_used_at desc, then created_at desc
    result.sort(key=lambda x: (x["is_owner"], x.get("last_used_at", "") or x.get("created_at", "")), reverse=True)
    return result

@app.get("/api/adventures")
def list_adventures_endpoint(refresh: bool = False):
    """List premade adventures stored in GCS/shared storage sorted newest first."""
    return adventure_service.list_adventures(force_refresh=refresh)


@app.get("/api/adventures/{adventure_id}")
def get_adventure_endpoint(adventure_id: str):
    """Get metadata for a specific premade adventure."""
    _safe_path_param(adventure_id, "adventure_id")
    adv = adventure_service.get_adventure(adventure_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Adventure not found")
    return adv


@app.get("/api/adventures/{adventure_id}/cover")
def get_adventure_cover_endpoint(adventure_id: str):
    """Stream or return the cover image of a premade adventure."""
    _safe_path_param(adventure_id, "adventure_id")
    res = adventure_service.get_adventure_cover(adventure_id)
    if not res:
        raise HTTPException(status_code=404, detail="Cover image not found")
    content, ctype = res
    return Response(content=content, media_type=ctype)


@app.get("/api/theaters/default-config")
async def get_default_theater_config():
    """Return the complete creation-editor baseline from theater_default.yaml."""
    config = deepcopy(get_theater_default_config())
    return {"config_yaml": yaml.safe_dump(config, default_flow_style=False, sort_keys=False)}

@app.get("/api/theaters/{theater_id}")
async def get_theater(theater_id: str, request: Request):
    """Retrieve metadata and mounted assets for a specific theater."""
    # Access validation already resolves the authenticated principal and the
    # deployment. Reuse both request-scoped results below rather than issuing
    # another deployment lookup for this same theater.
    deployment = await _require_canvas_access_async(request, theater_id)
    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists() or not (theater_dir / "theater.json").exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)

    meta = theater_manager.get_theater(theater_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Theater not found")
    
    current_user = await get_current_user_async(request, record_activity=False)
    owner_id = deployment.get("user_id")
    is_owner = (current_user is not None and owner_id == current_user["id"])
    active_orator_id = deployment.get("active_orator_id")
    is_active_orator = (
        current_user is not None
        and (
            active_orator_id == current_user["id"]
            or (active_orator_id is None and is_owner)
        )
    )
    raw_allowed = deployment.get("allowed_orators") or "[]"
    try:
        allowed_ids = json.loads(raw_allowed) if isinstance(raw_allowed, str) else list(raw_allowed)
    except Exception:
        allowed_ids = []
    is_allowed_orator = current_user is not None and current_user["id"] in allowed_ids

    # Analytics must not hold up the canvas reload, especially with a remote DB.
    client_ip = request.client.host if request.client else None
    asyncio.create_task(
        db.record_theater_view_async(
            theater_id,
            current_user["id"] if current_user else None,
            client_ip,
        )
    )

    meta_dict = meta.model_dump()
    meta_dict["is_owner"] = is_owner
    meta_dict["is_active_orator"] = is_active_orator
    meta_dict["is_allowed_orator"] = is_allowed_orator
    meta_dict["is_adventure_mode"] = bool(
        meta_dict.get("config", {}).get("story_planning", {}).get("adventure_mode", False)
    )
    if deployment.get("join_key"):
        meta_dict["join_key"] = deployment["join_key"]
    elif not is_owner:
        meta_dict["join_key"] = "🔒 Owner Only"

    return {
        "metadata": meta_dict,
        "references": theater_manager.get_theater_references(theater_id),
        "playlists": theater_manager.get_theater_playlists(theater_id),
    }

@app.get("/api/theaters/{theater_id}/config")
async def get_theater_config_endpoint(theater_id: str, request: Request):
    """Get raw theater.yaml configuration for a theater session."""
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    base_dir = theater_manager.base_dir
    theater_dir = theater_manager.theater(theater_id).directory()
    yaml_path = theater_dir / "theater.yaml"

    if not yaml_path.exists():
        get_theater_config(theater_id, base_dir=base_dir, db=db)

    if yaml_path.exists():
        try:
            content = yaml_path.read_text(encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to read theater.yaml: {e}")
    else:
        default_config = get_theater_default_config()
        content = yaml.safe_dump(default_config, default_flow_style=False)

    return {"theater_id": theater_id, "config_yaml": content}

@app.post("/api/theaters/{theater_id}/config")
async def save_theater_config_endpoint(theater_id: str, req: SaveTheaterConfigRequest, request: Request):
    """Save raw theater.yaml configuration directly to local theater directory and DB."""
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    try:
        config_data = yaml.safe_load(req.config_yaml)
        if config_data is None:
            config_data = {}
        if not isinstance(config_data, dict):
            raise HTTPException(status_code=400, detail="Invalid YAML: Root structure must be a mapping/object.")
    except yaml.YAMLError as err:
        raise HTTPException(status_code=400, detail=f"YAML Syntax Error: {err}")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Failed to parse YAML: {err}")

    # Save to local theater directory
    theater_dir = theater_manager.theater(theater_id).directory()
    theater_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = theater_dir / "theater.yaml"

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(req.config_yaml)

    # Save to DB
    if db is not None and hasattr(db, "save_theater_config"):
        try:
            db.save_theater_config(theater_id, config_data)
        except Exception as e:
            logger.warning(f"[theaters] Warning: Failed to save DB config for {theater_id}: {e}")

    return {
        "status": "ok",
        "message": "theater.yaml saved directly to DB and theater directory. Restart your agent to apply changes.",
        "theater_id": theater_id
    }

@app.post("/api/theaters/format-yaml")
async def format_yaml_endpoint(req: SaveTheaterConfigRequest):
    """Validate and format a YAML string, returning pretty-printed YAML or syntax error details."""
    try:
        data = yaml.safe_load(req.config_yaml)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid YAML: Root structure must be a mapping/object.")
        formatted = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
        return {"status": "ok", "formatted_yaml": formatted}
    except yaml.YAMLError as err:
        raise HTTPException(status_code=400, detail=f"YAML Syntax Error: {err}")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=f"Failed to format YAML: {err}")

@app.post("/api/theaters/create-and-deploy")
async def create_and_deploy_theater(request: Request):
    """API endpoint to handle multi-file asset upload and deploy a theater."""
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to deploy theaters.")

    form = await request.form()
    name = str(form.get("name", "Narratron Theater"))
    style = str(form.get("agent_style", "")).strip()
    special_instructions = str(form.get("agent_special_instructions", "")).strip()
    advanced_config_canonical = str(form.get("advanced_config_canonical", "")).lower() == "true"
    creation_mode = str(form.get("creation_mode", "blank"))
    folder_config_yaml = form.get("folder_theater_config_yaml")
    use_generated_music = str(form.get("use_generated_music", "false")).lower() == "true"
    # Default to enabled so older clients and existing integrations retain
    # their current behavior when they do not send the new field.
    enable_image_generation = str(form.get("enable_image_generation", "true")).lower() == "true"
    enable_scene_animations = str(form.get("enable_scene_animations", "false")).lower() == "true"
    enable_interactive_canvas = str(form.get("enable_interactive_canvas", "false")).lower() == "true"
    enable_adventure_mode = (
        creation_mode == "adventure"
        or str(form.get("enable_adventure_mode", "false")).lower() == "true"
    )
    story_planning_style = str(form.get("story_planning_style", "")).strip()
    if len(story_planning_style) > 500:
        raise HTTPException(status_code=400, detail="Story planning style must be 500 characters or fewer.")

    preset_adventure_id = str(form.get("preset_adventure_id", "")).strip()
    reference_files = []
    playlists_data = {}
    lore_files = []
    adventure_metadata = None

    # If an adventure preset is chosen, load its assets first
    adv_config: dict = {}
    if preset_adventure_id:
        adv_refs, adv_playlists, adv_lore, adv_config = adventure_service.load_adventure_assets(preset_adventure_id)
        reference_files.extend(adv_refs)
        lore_files.extend(adv_lore)
        for pl_name, tracks in adv_playlists.items():
            if pl_name not in playlists_data:
                playlists_data[pl_name] = []
            playlists_data[pl_name].extend(tracks)
        adventure_metadata = adventure_service.get_adventure(preset_adventure_id)

    # Important to provide music for first time experience. Blank theater will not have any music tracks by default
    # otherwise.
    if creation_mode in ("blank", "adventure") and not playlists_data:
        quick_deploy_track = PROJECT_ROOT / "playlists" / "default" / "new story.mp3"
        if quick_deploy_track.is_file():
            playlists_data["default"] = [("new_story.mp3", quick_deploy_track.read_bytes())]

    for key, value in form.multi_items():
        filename = getattr(value, "filename", None)
        if filename:
            content = await value.read()
            if content:
                # Check for uploaded ZIP package
                if key in ("asset_zip", "asset_package") or filename.lower().endswith(".zip"):
                    try:
                        zip_refs, zip_playlists, zip_lore, zip_config_yaml = extract_asset_package(content)
                    except ValueError as ve:
                        raise HTTPException(status_code=400, detail=str(ve))
                    reference_files.extend(zip_refs)
                    lore_files.extend(zip_lore)
                    for pl_name, tracks in zip_playlists.items():
                        if pl_name not in playlists_data:
                            playlists_data[pl_name] = []
                        playlists_data[pl_name].extend(tracks)
                    if creation_mode == "folder" and zip_config_yaml:
                        folder_config_yaml = zip_config_yaml
                elif key in ("asset_folder_files", "asset_files"):
                    # Folder upload with relative path info
                    rel_path = filename.replace("\\", "/")
                    parts = [p for p in rel_path.split("/") if p]
                    clean_name = parts[-1] if parts else filename

                    if clean_name.lower() == "metadata.json" or filename.lower() == "metadata.json":
                        try:
                            adventure_metadata = json.loads(content.decode("utf-8"))
                        except Exception:
                            pass
                        reference_files.append(("metadata.json", content))
                    elif "references" in parts or (
                        clean_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                        and "playlists" not in parts
                    ):
                        reference_files.append((rel_path, content))
                    elif "playlists" in parts:
                        idx = parts.index("playlists")
                        pl_name = parts[idx + 1] if idx + 1 < len(parts) - 1 else "default"
                        if clean_name.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")):
                            if pl_name not in playlists_data:
                                playlists_data[pl_name] = []
                            playlists_data[pl_name].append((clean_name, content))
                    elif "lore" in parts:
                        if not clean_name.lower().endswith(".txt"):
                            raise HTTPException(status_code=400, detail="Lore documents must be .txt files.")
                        if len(content) > MAX_LORE_DOCUMENT_BYTES:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Lore documents must be at most {MAX_LORE_DOCUMENT_BYTES // 1024}KB.",
                            )
                        try:
                            content.decode("utf-8")
                        except UnicodeDecodeError:
                            raise HTTPException(
                                status_code=400,
                                detail="Lore documents must be UTF-8 encoded text.",
                            )
                        lore_files.append((rel_path, content))
                elif key == "reference_files":
                    reference_files.append((filename, content))
                elif key.startswith("playlist_"):
                    pl_name = key[len("playlist_"):]
                    if pl_name not in playlists_data:
                        playlists_data[pl_name] = []
                    playlists_data[pl_name].append((filename, content))

    raw_config_param = (
        folder_config_yaml
        if creation_mode == "folder"
        else form.get("theater_config_yaml") or form.get("theater_config")
    )
    if creation_mode == "folder" and not raw_config_param:
        raise HTTPException(status_code=400, detail="Folder uploads must include a theater.yaml file.")
    theater_config = None
    if raw_config_param:
        try:
            theater_config = yaml.safe_load(raw_config_param) if isinstance(raw_config_param, str) else raw_config_param
            if theater_config is not None and not isinstance(theater_config, dict):
                raise ValueError("Theater configuration must be a YAML mapping.")
        except (yaml.YAMLError, ValueError) as error:
            raise HTTPException(status_code=400, detail=f"Invalid theater configuration: {error}")

    theater_config = theater_config or {}
    if adv_config:
        for k, v in adv_config.items():
            if k not in theater_config:
                theater_config[k] = deepcopy(v)
            elif isinstance(v, dict) and isinstance(theater_config[k], dict):
                for sub_k, sub_v in v.items():
                    theater_config[k].setdefault(sub_k, deepcopy(sub_v))
    if creation_mode != "folder" and not advanced_config_canonical:
        agent_config = theater_config.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: agent must be a mapping.")
        if special_instructions:
            agent_config["special_instructions"] = special_instructions
        image_config = theater_config.setdefault("image_generation", {})
        if not isinstance(image_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: image_generation must be a mapping.")
        if style:
            image_config["style"] = style
        image_config["enabled"] = enable_image_generation
        music_config = theater_config.setdefault("music", {})
        if not isinstance(music_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: music must be a mapping.")
        music_config["use_generated_music"] = use_generated_music
        animation_config = theater_config.setdefault("animation", {})
        if not isinstance(animation_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: animation must be a mapping.")
        animation_config["enabled"] = enable_scene_animations
        interactive_canvas_config = theater_config.setdefault("interactive_canvas", {})
        if not isinstance(interactive_canvas_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: interactive_canvas must be a mapping.")
        interactive_canvas_config["enabled"] = enable_interactive_canvas
        story_planning_config = theater_config.setdefault("story_planning", {})
        if not isinstance(story_planning_config, dict):
            raise HTTPException(status_code=400, detail="Invalid theater configuration: story_planning must be a mapping.")
        story_planning_config["adventure_mode"] = enable_adventure_mode
        if story_planning_style:
            story_planning_config["style"] = story_planning_style

    theater_id = f"theater_{uuid.uuid4().hex[:8]}"
    metadata = theater_manager.create_theater(
        name=name,
        theater_id=theater_id,
        reference_files=reference_files,
        playlists_data=playlists_data,
        lore_files=lore_files,
        theater_config=theater_config,
        metadata_json=adventure_metadata,
    )
    deployed_meta = theater_manager.deploy_theater(metadata.theater_id)

    # Record deployment & deduct credits (0.0 cost)
    db.record_deployment(deployed_meta.theater_id, user["id"], deployed_meta.join_key, cost=0.0, theater_config=theater_config)
    auth_session_cache.invalidate_user(user["id"])
    theater_access_cache.invalidate_theater(deployed_meta.theater_id)

    res_dict = deployed_meta.model_dump()
    res_dict["is_owner"] = True
    asyncio.create_task(
        db.persist_canvas_theater_async(
            canvas_states,
            theater_manager,
            deployed_meta.theater_id,
            user["id"],
            deployed_meta.name,
        )
    )
    return {"status": "ok", "theater_id": deployed_meta.theater_id, "theater": res_dict}

@app.post("/api/theaters/{theater_id}/deploy")
def deploy_existing_theater(theater_id: str, request: Request):
    """Deploy an existing created theater."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists() or not (theater_dir / "theater.json").exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)
    
    dep = db.get_deployment(theater_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can deploy this theater.")

    # Stop any other currently deployed theaters
    existing_theaters = theater_manager.list_theaters()
    for s in existing_theaters:
        if s.theater_id != theater_id and s.status == "deployed":
            try:
                theater_manager.stop_theater(s.theater_id)
            except Exception:
                pass

    meta = theater_manager.deploy_theater(theater_id)
    theater_access_cache.invalidate_theater(theater_id)
    return {"status": "ok", "theater": meta}

@app.delete("/api/theaters/{theater_id}")
def destroy_theater(theater_id: str, request: Request):
    """Remove and clean up a local theater instance. Requires owner login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to delete theaters.")

    dep = db.get_deployment(theater_id)
    if dep and dep.get("user_id") is not None and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can delete this theater.")

    disk_removed = theater_manager.destroy_theater(theater_id)
    db_deleted = db.delete_deployment(theater_id)
    theater_access_cache.invalidate_theater(theater_id)
    canvas_states.states.pop(theater_id, None)

    if not (disk_removed or db_deleted):
        raise HTTPException(status_code=404, detail="Theater not found or could not be removed")

    return {"status": "ok", "theater_id": theater_id}

@app.patch("/api/theaters/{theater_id}")
@app.post("/api/theaters/{theater_id}/retitle")
async def retitle_theater(theater_id: str, req: RetitleTheaterRequest, request: Request):
    """Update the title of an existing theater. Requires owner authentication."""
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to retitle theater.")

    dep = db.get_deployment(theater_id)
    if dep and dep.get("user_id") is not None and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can retitle this theater.")

    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Theater title cannot be empty.")

    meta = theater_manager.get_theater(theater_id)
    if meta:
        meta.name = new_name
        theater_manager._save_metadata(meta)

    db.update_theater_name(theater_id, new_name)
    theater_access_cache.invalidate_theater(theater_id)

    return {"status": "ok", "theater_id": theater_id, "name": new_name}

# ========================================
# Baton Passing API Endpoints
# ========================================

@app.get("/api/theaters/{theater_id}/baton")
async def get_theater_baton_state(theater_id: str, request: Request):
    await _require_canvas_access_async(request, theater_id)
    state = await db.get_theater_baton_state_async(theater_id)
    if not state:
        raise HTTPException(status_code=404, detail="Theater baton state not found.")
    
    cs = canvas_states.get(theater_id)
    state["active_viewers"] = cs.get_active_viewers()
    return state


@app.post("/api/theaters/{theater_id}/baton/allowed_orators")
async def add_allowed_orator(theater_id: str, req: AddAllowedOratorRequest, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.add_allowed_orator_async(theater_id, owner_id=user["id"], target_user_id=req.target_user_id)
        theater_access_cache.invalidate_theater(theater_id)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/theaters/{theater_id}/baton/allowed_orators/{target_user_id}")
async def remove_allowed_orator(theater_id: str, target_user_id: int, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.remove_allowed_orator_async(theater_id, owner_id=user["id"], target_user_id=target_user_id)
        theater_access_cache.invalidate_theater(theater_id)
        await _sync_agent_controller(theater_id, updated_state)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/request")
async def request_baton_pass(theater_id: str, req: RequestBatonRequest, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.request_baton_async(
            theater_id,
            owner_id=user["id"],
            target_user_id=req.target_user_id,
            timeout_seconds=req.timeout_seconds or 30
        )
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/accept")
async def accept_baton_pass(theater_id: str, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.accept_baton_async(theater_id, target_user_id=user["id"])
        theater_access_cache.invalidate_theater(theater_id)
        await _sync_agent_controller(theater_id, updated_state)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/decline")
async def decline_baton_pass(theater_id: str, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.decline_baton_async(theater_id, target_user_id=user["id"])
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/baton/takeback")
async def take_back_baton(theater_id: str, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.take_back_baton_async(theater_id, owner_id=user["id"])
        theater_access_cache.invalidate_theater(theater_id)
        await _sync_agent_controller(theater_id, updated_state)
        await canvas_states.broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/theaters/{theater_id}/save", status_code=202)
async def save_theater_to_db(theater_id: str, request: Request):
    """Save canvas theater state and image assets to SQLite database on user demand."""
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    dep = db.get_deployment(theater_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Active theater not found.")
    if dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can save this theater.")

    meta = theater_manager.get_theater(theater_id)
    user_id = dep["user_id"] if dep else None
    name = meta.name if meta else theater_id

    asyncio.create_task(
        db.persist_canvas_theater_async(
            canvas_states,
            theater_manager,
            theater_id,
            user_id,
            name,
        )
    )
    return {"status": "queued", "theater_id": theater_id}

@app.get("/api/theaters/{theater_id}/export-assets")
def export_theater_assets(theater_id: str, request: Request):
    """Package and export all theater assets into a ZIP file."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    dep = db.get_deployment(theater_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Active theater not found.")
    if dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can export this theater.")

    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists():
        db.reconstruct_theater_from_db(theater_id, theater_dir)

    # Ensure current displayed image is saved into the theater directory
    cs = canvas_states.get(theater_id)
    cs.export_theater_data(theater_dir=theater_dir)

    import io
    import zipfile
    from fastapi.responses import Response

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        if theater_dir.exists():
            for file_path in theater_dir.rglob("*"):
                if file_path.is_file():
                    arc_name = file_path.relative_to(theater_dir)
                    zip_file.write(file_path, arcname=str(arc_name).replace("\\", "/"))

    zip_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="{theater_id}_assets.zip"'
    }
    return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers=headers)


@app.get("/theaters/{theater_id}/suggestions")
@app.get("/api/theaters/{theater_id}/suggestions")
async def get_theater_suggestions(theater_id: str, request: Request):
    """Generate structured scene suggestions based on present named elements from NamedElementTool."""
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    session = agent_manager.get_session(theater_id)
    named_elements = []
    session_tools = getattr(session, "story_planning_tools", None) or getattr(session, "named_element_tools", None) if session else None
    if session_tools and hasattr(session_tools, "get_present_elements"):
        named_elements = session_tools.get_present_elements()
    if not named_elements and canvas_states:
        try:
            mgr = canvas_states.get(theater_id)
            if hasattr(mgr, "get_named_elements"):
                named_elements = mgr.get_named_elements()
        except Exception:
            pass

    force_refresh = request.query_params.get("refresh") in ("true", "1")
    res, fingerprint = suggestion_service.generate_suggestions(
        named_elements=named_elements,
        theater_id=theater_id,
        force_refresh=force_refresh,
    )
    return {
        "suggestions": [item.model_dump() for item in res.suggestions],
        "elements_fingerprint": fingerprint,
    }


@app.get("/theaters/{theater_id}/sticky-notes")
@app.get("/api/theaters/{theater_id}/sticky-notes")
async def get_theater_sticky_notes(theater_id: str, request: Request):
    """Retrieve active sticky notes held by the story planning tool or canvas state."""
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    session = agent_manager.get_session(theater_id)
    sticky_notes = []
    session_tools = getattr(session, "story_planning_tools", None) or getattr(session, "named_element_tools", None) if session else None
    if session_tools and hasattr(session_tools, "get_present_sticky_notes"):
        sticky_notes = session_tools.get_present_sticky_notes()
    elif session_tools and hasattr(session_tools, "get_present_elements"):
        sticky_notes = session_tools.get_present_elements()

    if not sticky_notes and canvas_states:
        try:
            mgr = canvas_states.get(theater_id)
            if hasattr(mgr, "get_sticky_notes"):
                sticky_notes = mgr.get_sticky_notes()
            elif hasattr(mgr, "get_named_elements"):
                sticky_notes = mgr.get_named_elements()
        except Exception:
            pass

    return {
        "sticky_notes": sticky_notes,
        "count": len(sticky_notes),
    }



