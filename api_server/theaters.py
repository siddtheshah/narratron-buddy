"""Theater deployer, asset routes, baton passing, and theater management API endpoints."""

import asyncio
from copy import deepcopy
import json
import logging
import re
from typing import Any, Dict, Optional, Union
import uuid
import yaml

from fastapi import Request, Response, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from pydantic import BaseModel, Field, JsonValue

from api_server.shared import (
    app,
    db,
    theater_manager,
    theater_repository,
    canvas_states,
    get_current_user,
    get_current_user_async,
    _require_canvas_access_async,
    _safe_path_param,
    _grant_canvas_access,
    can_control_agent_websocket,
    PROJECT_ROOT
)
from google.genai import types
from api_server.dependencies import live_agent_manager, suggestion_service, adventure_service, pricing_controller
from api_server.canvas import broadcast_baton_update, _broadcast_doodle
from services.theater_image_generation import generate_theater_image
from services.theater_assets import Playlist, AnimationAsset, playlists, animations
from services.generation_billing import (
    billing_locks as _billing_locks,
    MAX_CONCURRENT_GENERATION_JOBS,
    GENERATION_SLOT_TTL_SECONDS,
)
from utils.auth_cache import auth_session_cache
from api_server.theater_access_cache import theater_access_cache
from components.theater_manager import (
    MAX_LORE_DOCUMENT_BYTES,
    TheaterMetadata,
    TheaterScene,
    TheaterCharacter,
    extract_asset_package,
    validate_asset_path,
)
from utils.config_loader import get_theater_config, get_theater_default_config

logger = logging.getLogger(__name__)


class GenerateTheaterStampRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)


def _save_generated_stamp(theater_id: str, filename: str, content: bytes) -> None:
    for root in (
        theater_manager.theater(theater_id).directory(),
        theater_repository.theater_path(theater_id),
    ):
        target = root / "stamps" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _remove_generated_stamp(theater_id: str, filename: str) -> None:
    for root in (
        theater_manager.theater(theater_id).directory(),
        theater_repository.theater_path(theater_id),
    ):
        (root / "stamps" / filename).unlink(missing_ok=True)


@app.post("/api/theaters/{theater_id}/stamps/generate")
async def generate_theater_stamp(
    request: Request, theater_id: str, body: GenerateTheaterStampRequest,
) -> dict[str, JsonValue]:
    _safe_path_param(theater_id, "theater_id")
    user = await get_current_user_async(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to generate theater stamps.")
    deployment = await asyncio.to_thread(db.get_deployment, theater_id)
    if not deployment or deployment["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can generate stamps.")
    name, prompt = body.name.strip(), body.prompt.strip()
    if not name or not prompt:
        raise HTTPException(status_code=422, detail="Enter a stamp name and image prompt.")
    owner_id = int(user["id"])
    slot_id = await asyncio.to_thread(
        db.acquire_generation_slot,
        owner_id,
        "stamp",
        theater_id,
        MAX_CONCURRENT_GENERATION_JOBS,
        GENERATION_SLOT_TTL_SECONDS,
    )
    if slot_id is None:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent generation jobs. Maximum {MAX_CONCURRENT_GENERATION_JOBS} allowed.",
        )
    try:
        async with _billing_locks.setdefault(owner_id, asyncio.Lock()):
            cost = pricing_controller.get_rates()["image_credit_rate"]
            account = await asyncio.to_thread(db.get_user_by_id, owner_id)
            if not account or account["credits"] < cost:
                raise HTTPException(status_code=402, detail=f"Generation requires {cost:g} credits. Top up on /deploy.")
            root = theater_manager.theater(theater_id).directory()
            if not (root / "theater.yaml").is_file():
                if not await asyncio.to_thread(theater_repository.reconstruct_theater, theater_id, root):
                    raise HTTPException(status_code=404, detail="Theater files not found.")
        # Image jobs can overlap; hold the billing lock only for account checks and settlement.
        try:
            image = await asyncio.to_thread(generate_theater_image, root, kind="stamp", prompt=prompt, references=[])
            if not image.image_bytes or image.mime_type != "image/png":
                raise ValueError("Stamp generation must return a PNG image.")
        except Exception as error:
            logger.exception("Theater stamp generation failed")
            raise HTTPException(status_code=502, detail="Stamp generation failed; no credits were charged.") from error

        async with _billing_locks.setdefault(owner_id, asyncio.Lock()):
            filename = ""
            try:
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_") or "stamp"
                filename = f"{safe_name}_{uuid.uuid4().hex[:12]}.png"
                await asyncio.to_thread(_save_generated_stamp, theater_id, filename, image.image_bytes)
            except Exception as error:
                if filename:
                    await asyncio.to_thread(_remove_generated_stamp, theater_id, filename)
                logger.exception("Theater stamp generation failed")
                raise HTTPException(status_code=502, detail="Stamp generation failed; no credits were charged.") from error
            try:
                updated = await asyncio.to_thread(
                    db.record_user_usage, owner_id, images_created=1, credit_cost=cost,
                    idempotency_key=f"stamp:{theater_id}:{filename}",
                )
            except Exception as error:
                await asyncio.to_thread(_remove_generated_stamp, theater_id, filename)
                logger.exception("Theater stamp billing failed")
                raise HTTPException(status_code=503, detail="Could not settle generation credits. Please try again.") from error
            auth_session_cache.invalidate_user(owner_id)
            stamps = theater_manager.theater(theater_id).stamps()
        await _broadcast_doodle(canvas_states.get(theater_id), {"type": "theater_stamps_updated", "stamps": stamps})
        return {"stamps": stamps, "credits_charged": cost, "credits": float(updated["credits"])}
    finally:
        await asyncio.to_thread(db.release_generation_slot, slot_id)


def _session_notepad(session: object) -> object | None:
    """Return the shared notepad from either live-agent tool holder."""
    for attribute in ("notepad_tool", "story_planning_tools"):
        tool = getattr(session, attribute, None)
        notepad = getattr(tool, "notepad", None)
        if notepad is not None:
            return notepad
    return None

async def _export_canvas_theater_async(theater_id: str) -> bool:
    """Export theater canvas data to repository asynchronously."""
    def _export():
        theater_dir = theater_manager.theater(theater_id).directory()
        canvas_states.get(theater_id).save_local_theater_data(theater_dir=theater_dir)
        return theater_repository.export_theater(theater_id, theater_dir)
    try:
        return await asyncio.to_thread(_export)
    except Exception:
        logger.exception("Failed to export theater '%s' to repository", theater_id)
        return False


async def _sync_agent_controller(theater_id: str, baton_state: dict) -> None:
    """Update the controller without disconnecting the live agent session."""
    active_orator = baton_state.get("active_orator") or {}
    active_orator_id = active_orator.get("id")
    if active_orator_id is not None:
        live_agent_manager.set_active_controller(theater_id, active_orator_id)


class ResolveJoinKeyRequest(BaseModel):
    join_key: str

class SaveTheaterConfigRequest(BaseModel):
    config_yaml: str

class RetitleTheaterRequest(BaseModel):
    name: str

class AddContributorRequest(BaseModel):
    target_user_id: int

class RequestBatonRequest(BaseModel):
    target_user_id: int
    timeout_seconds: Optional[int] = 30


# ========================================
# Theater Asset Dynamic Routes
# ========================================

@app.get("/api/theaters/{theater_id}/stamps")
async def list_theater_stamps(
    request: Request, theater_id: str, join_key: Optional[str] = None,
) -> list[dict[str, str]]:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    return theater_manager.theater(theater_id).stamps()


class PushSceneRequest(BaseModel):
    name: str = ""
    scene_id: Optional[str] = None


class PushAssetRequest(BaseModel):
    id: str = Field(min_length=1, max_length=2000)


async def _require_asset_orator(request: Request, theater_id: str, join_key: str | None) -> None:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    user = await get_current_user_async(request)
    if not can_control_agent_websocket(db.get_deployment(theater_id), current_user=user):
        raise HTTPException(status_code=403, detail="Only the active orator can manage theater assets.")


@app.get("/api/theaters/{theater_id}/playlists")
async def list_theater_playlists(request: Request, theater_id: str, join_key: str | None = None) -> list[Playlist]:
    await _require_asset_orator(request, theater_id, join_key)
    return await asyncio.to_thread(playlists, theater_manager.theater(theater_id))


@app.get("/api/theaters/{theater_id}/animations")
async def list_theater_animations(request: Request, theater_id: str, join_key: str | None = None) -> list[AnimationAsset]:
    await _require_asset_orator(request, theater_id, join_key)
    assets = await asyncio.to_thread(animations, theater_manager.theater(theater_id))
    return [asset.model_copy(update={"manifest": {}, "frames": []}) for asset in assets]


@app.post("/api/theaters/{theater_id}/playlists/push")
async def push_theater_track(payload: PushAssetRequest, request: Request, theater_id: str, join_key: str | None = None) -> dict[str, str]:
    await _require_asset_orator(request, theater_id, join_key)
    if payload.id == "no_music":
        state = canvas_states.get(theater_id)
        state.audio.update_music("", [], allow_pinned=True)
        state.persist()
        return {"status": "ok", "message": "Music stopped."}
    groups = await asyncio.to_thread(playlists, theater_manager.theater(theater_id))
    for group in groups:
        if group.id == payload.id:
            state = canvas_states.get(theater_id)
            state.audio.update_music(group.name, [track.url for track in group.tracks], allow_pinned=True)
            state.persist()
            return {"status": "ok", "message": f"Playing {group.name} ({len(group.tracks)} tracks)."}
        for track in group.tracks:
            if track.id == payload.id:
                state = canvas_states.get(theater_id)
                state.audio.update_music(f"{group.name} / {track.name}", [track.url], allow_pinned=True)
                state.persist()
                return {"status": "ok", "message": f"Playing {track.name}."}
    raise HTTPException(status_code=404, detail="Track or playlist not found.")


@app.post("/api/theaters/{theater_id}/animations/push")
async def push_theater_animation(payload: PushAssetRequest, request: Request, theater_id: str, join_key: str | None = None) -> dict[str, str]:
    await _require_asset_orator(request, theater_id, join_key)
    theater = theater_manager.theater(theater_id)
    assets = await asyncio.to_thread(animations, theater)
    for asset in assets:
        if asset.id == payload.id:
            state = canvas_states.get(theater_id)
            result = state.visual.update_animation(asset.type, asset.frames if asset.type == "triframe" else asset.manifest,
                id=asset.id, prompt=asset.description, force_immediate=True, source="play_animation", url_for_path=theater.get_url_for_path)
            if result["status"] == "blocked":
                raise HTTPException(status_code=409, detail=result["message"])
            state.persist()
            return {"status": "ok", "message": f"Playing {asset.description or asset.name}."}
    raise HTTPException(status_code=404, detail="Animation not found.")


@app.get("/api/theaters/{theater_id}/scenes")
async def list_theater_scenes(
    request: Request, theater_id: str, join_key: Optional[str] = None,
) -> list[TheaterScene]:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    deployment = db.get_deployment(theater_id)
    current_user = await get_current_user_async(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can access theater scenes.")
    return theater_manager.theater(theater_id).scenes()


@app.get("/api/theaters/{theater_id}/characters")
async def list_theater_characters(
    request: Request, theater_id: str, join_key: Optional[str] = None,
) -> list[TheaterCharacter]:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    deployment = db.get_deployment(theater_id)
    current_user = await get_current_user_async(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can access theater characters.")
    return theater_manager.theater(theater_id).characters()


@app.post("/api/theaters/{theater_id}/scenes/push")
async def push_theater_scene(
    payload: PushSceneRequest,
    request: Request,
    theater_id: str,
    join_key: Optional[str] = None,
) -> dict[str, str]:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    deployment = db.get_deployment(theater_id)
    current_user = await get_current_user_async(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can push scenes to the canvas.")

    scenes = theater_manager.theater(theater_id).scenes()
    target_scene: Optional[TheaterScene] = None
    req_name = payload.name.strip().lower()
    req_id = (payload.scene_id or "").strip()
    for s in scenes:
        if req_id and s["id"] == req_id:
            target_scene = s
            break
        if req_name and (s["name"].strip().lower() == req_name or s["name"].replace("_", " ").strip().lower() == req_name):
            target_scene = s
            break

    if target_scene is None:
        raise HTTPException(status_code=404, detail=f"Scene '{payload.name}' not found.")
    if not target_scene["path"]:
        raise HTTPException(status_code=400, detail=f"Scene '{target_scene['name']}' has no image to display.")

    state = canvas_states.get(theater_id)
    update_res = state.visual.update_image(
        path=target_scene["path"],
        display_path=None,
        allow_pinned=True,
        transition="crossfade",
        effect="gleam3",
        prompt=target_scene["description"] or target_scene["name"],
        source="show_image",
        url_for_path=theater_manager.theater(theater_id).get_url_for_path,
    )
    state.persist()

    session = live_agent_manager.get_session(theater_id)
    if session is not None and session.is_alive:
        session.send_content(types.Content(role="system", parts=[types.Part(text=(
            f"[Orator Action] The active orator changed the scene to '{target_scene['name']}'."
        ))]))

    return {
        "status": "ok",
        "scene_name": target_scene["name"],
        "scene_id": target_scene["id"],
        "url": target_scene["url"],
        "message": update_res["message"],
    }


@app.get("/theaters/{theater_id}/stamps/{filename:path}")
async def serve_theater_stamp(
    request: Request, theater_id: str, filename: str, join_key: Optional[str] = None,
) -> Response:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    root = theater_manager.theater(theater_id).stamps_dir().resolve()
    image = (root / filename).resolve()
    if root not in image.parents:
        raise HTTPException(status_code=400, detail="Invalid stamp path")
    if not image.is_file() or image.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=404, detail="Theater stamp not found")
    response = FileResponse(
        image,
        stat_result=image.stat(),
        headers={"Cache-Control": "private, max-age=3600", "Vary": "Cookie"},
    )
    if StaticFiles().is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response

@app.get("/theaters/{theater_id}/references/{filename:path}")
async def serve_theater_reference(
    request: Request,
    theater_id: str,
    filename: str,
    join_key: Optional[str] = None,
) -> Response:
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    ref_dir = theater_manager.theater(theater_id).references_dir()
    file_path = (ref_dir / filename).resolve()
    if ref_dir.resolve() not in file_path.parents and file_path != ref_dir:
        raise HTTPException(status_code=400, detail="Invalid reference path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Theater reference file not found")
    from components.reference_images import reference_image_type

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "private, max-age=3600",
        "Vary": "Cookie",
    }
    stat_result = file_path.stat()
    try:
        media_type = await asyncio.to_thread(reference_image_type, file_path.name, file_path.read_bytes())
    except ValueError:
        # Existing deployments may contain documents accepted before validation.
        fallback_resp = FileResponse(file_path, media_type="application/octet-stream", filename=file_path.name, headers=headers, stat_result=stat_result)
        if StaticFiles().is_not_modified(fallback_resp.headers, request.headers):
            return NotModifiedResponse(fallback_resp.headers)
        return fallback_resp
    response = FileResponse(file_path, media_type=media_type, headers=headers, stat_result=stat_result)
    if StaticFiles().is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response

@app.get("/theaters/{theater_id}/playlists/{playlist_name}/{filename:path}")
async def serve_theater_playlist_track(
    request: Request,
    theater_id: str,
    playlist_name: str,
    filename: str,
    join_key: Optional[str] = None,
):
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
    _safe_path_param(theater_id, "theater_id")
    playlists_root = theater_manager.theater(theater_id).playlists_dir().resolve()
    playlist_root = (playlists_root / playlist_name).resolve()
    file_path = (playlist_root / filename).resolve()
    if playlists_root not in playlist_root.parents or playlist_root not in file_path.parents:
        raise HTTPException(status_code=400, detail="Invalid playlist path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Theater playlist track not found")
    return FileResponse(file_path)

@app.get("/theaters/{theater_id}/output/{filename:path}")
async def serve_theater_output(
    request: Request,
    theater_id: str,
    filename: str,
    join_key: Optional[str] = None,
):
    await _require_canvas_access_async(request, theater_id, join_key=join_key)
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
    output_headers = {
        "Cache-Control": "private, max-age=3600",
        "Vary": "Cookie",
    }
    response = FileResponse(file_path, headers=output_headers, stat_result=file_path.stat())
    if StaticFiles().is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response

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

class TheaterSummary(BaseModel):
    theater_id: str
    name: str = ""
    status: str = "created"
    join_key: str = ""
    created_at: str = ""
    last_used_at: str = ""
    is_owner: bool = True
    mounted_references: list[str] = Field(default_factory=list)
    mounted_playlists: dict[str, list[str]] = Field(default_factory=dict)

    def __getitem__(self, item: str) -> str | bool | list[str] | dict[str, list[str]]:
        if item == "theater_id":
            return self.theater_id
        if item == "name":
            return self.name
        if item == "status":
            return self.status
        if item == "join_key":
            return self.join_key
        if item == "created_at":
            return self.created_at
        if item == "last_used_at":
            return self.last_used_at
        if item == "is_owner":
            return self.is_owner
        if item == "mounted_references":
            return self.mounted_references
        if item == "mounted_playlists":
            return self.mounted_playlists
        raise KeyError(item)

    def get(
        self,
        item: str,
        default: str | bool | list[str] | dict[str, list[str]] | None = None,
    ) -> str | bool | list[str] | dict[str, list[str]] | None:
        if item in {
            "theater_id",
            "name",
            "status",
            "join_key",
            "created_at",
            "last_used_at",
            "is_owner",
            "mounted_references",
            "mounted_playlists",
        }:
            return self[item]
        return default


@app.get("/api/theaters", response_model=list[TheaterSummary])
def list_theaters(request: Request) -> list[TheaterSummary]:
    """List deployed theaters for the authenticated user without leaking private canvas state or config."""
    current_user = get_current_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required to view theaters.")

    current_user_id = current_user["id"]
    result: list[TheaterSummary] = []
    for record in db.get_user_theater_records(current_user_id):
        theater_id = record["theater_id"]
        disk_metadata = theater_manager.get_theater(theater_id)
        raw_meta = disk_metadata.model_dump() if disk_metadata else record.get("metadata", {})
        last_used = record.get("last_used_at") or raw_meta.get("created_at") or ""
        summary = TheaterSummary(
            theater_id=theater_id,
            name=raw_meta.get("name") or theater_id,
            status=raw_meta.get("status", "created"),
            join_key=raw_meta.get("join_key", ""),
            created_at=raw_meta.get("created_at", ""),
            last_used_at=last_used,
            is_owner=True,
            mounted_references=raw_meta.get("mounted_references", []),
            mounted_playlists=raw_meta.get("mounted_playlists", {}),
        )
        result.append(summary)

    result.sort(key=lambda theater: theater.last_used_at or theater.created_at, reverse=True)
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
        theater_repository.reconstruct_theater(theater_id, theater_dir)

    meta = theater_manager.get_theater(theater_id)
    if not meta and theater_dir.exists() and (theater_dir / "theater.yaml").is_file():
        theater_config = theater_manager.get_theater_config(theater_id)
        name_val = str(deployment.get("name") or theater_id)
        join_key_val = str(deployment.get("join_key") or "")
        meta = TheaterMetadata(theater_id=theater_id, name=name_val, join_key=join_key_val or "KEY-test", config=theater_config, status="deployed")
        theater_manager._save_metadata(meta)
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
    raw_allowed = deployment.get("contributors") or "[]"
    try:
        allowed_ids = json.loads(raw_allowed) if isinstance(raw_allowed, str) else list(raw_allowed)
    except Exception:
        allowed_ids = []
    is_contributor = current_user is not None and current_user["id"] in allowed_ids

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
    meta_dict["is_contributor"] = is_contributor
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

    theater_dir = theater_manager.theater(theater_id).directory()
    yaml_path = theater_dir / "theater.yaml"

    if not yaml_path.exists():
        get_theater_config(theater_id, theater_manager=theater_manager)

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

    return {
        "status": "ok",
        "message": "Saved directly to current theater. Restart to apply configs, and ensure you save the theater state to keep these settings.",
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


def configure_with_assets(
    config: Dict[str, Any],
    *,
    special_instructions: str = "",
    style: str = "",
    enable_image_generation: bool = True,
    use_generated_music: bool = False,
    enable_scene_animations: bool = False,
    enable_interactive_canvas: bool = False,
    enable_adventure_mode: bool = False,
    story_planning_style: str = "",
) -> Dict[str, Any]:
    """Applies asset/form configuration options onto a base theater configuration."""
    agent_config = config.setdefault("live_agent", {})
    if not isinstance(agent_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: agent must be a mapping.")
    if special_instructions:
        agent_config["special_instructions"] = special_instructions

    image_config = config.setdefault("image_generation", {})
    if not isinstance(image_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: image_generation must be a mapping.")
    if style:
        image_config["style"] = style
    image_config["enabled"] = enable_image_generation

    music_config = config.setdefault("music", {})
    if not isinstance(music_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: music must be a mapping.")
    music_config["use_generated_music"] = use_generated_music

    animation_config = config.setdefault("animation", {})
    if not isinstance(animation_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: animation must be a mapping.")
    animation_config["enabled"] = enable_scene_animations

    interactive_canvas_config = config.setdefault("interactive_canvas", {})
    if not isinstance(interactive_canvas_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: interactive_canvas must be a mapping.")
    interactive_canvas_config["enabled"] = enable_interactive_canvas

    story_planning_config = config.setdefault("story_planning", {})
    if not isinstance(story_planning_config, dict):
        raise HTTPException(status_code=400, detail="Invalid theater configuration: story_planning must be a mapping.")
    story_planning_config["adventure_mode"] = enable_adventure_mode
    if story_planning_style:
        story_planning_config["style"] = story_planning_style

    return config


def build_theater_config(
    *,
    creation_mode: str = "blank",
    folder_config_yaml: Optional[Union[str, dict]] = None,
    adv_config: Optional[dict] = None,
    special_instructions: str = "",
    style: str = "",
    enable_image_generation: bool = True,
    use_generated_music: bool = False,
    enable_scene_animations: bool = False,
    enable_interactive_canvas: bool = False,
    enable_adventure_mode: bool = False,
    story_planning_style: str = "",
) -> Dict[str, Any]:
    """Build theater configuration according to source/mode.

    - If uploaded from a folder or sourced from an adventure, don't bother with theater_default.yaml.
    - Otherwise use theater_default.yaml and apply updates from configure_with_assets.
    """
    if creation_mode == "folder":
        if not folder_config_yaml:
            raise HTTPException(status_code=400, detail="Folder uploads must include a theater.yaml file.")
        try:
            theater_config = yaml.safe_load(folder_config_yaml) if isinstance(folder_config_yaml, str) else folder_config_yaml
            if not isinstance(theater_config, dict):
                raise ValueError("Theater configuration must be a YAML mapping.")
        except (yaml.YAMLError, ValueError) as error:
            raise HTTPException(status_code=400, detail=f"Invalid theater configuration: {error}")

    elif adv_config:
        theater_config = deepcopy(adv_config)
        if enable_adventure_mode or creation_mode == "adventure":
            theater_config.setdefault("story_planning", {})["adventure_mode"] = True

    else:
        theater_config = get_theater_default_config()
        configure_with_assets(
            theater_config,
            special_instructions=special_instructions,
            style=style,
            enable_image_generation=enable_image_generation,
            use_generated_music=use_generated_music,
            enable_scene_animations=enable_scene_animations,
            enable_interactive_canvas=enable_interactive_canvas,
            enable_adventure_mode=enable_adventure_mode,
            story_planning_style=story_planning_style,
        )

    return theater_config


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
    creation_mode = str(form.get("creation_mode", "blank"))
    folder_config_yaml = form.get("folder_theater_config_yaml")
    advanced_config = form.get("advanced_config")
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
    # otherwise. Adventures launched from adventures/ or deploy/ should not attach the default playlist.
    if creation_mode == "blank":
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
                    try:
                        parts = validate_asset_path(filename)
                    except ValueError as error:
                        raise HTTPException(status_code=400, detail=str(error)) from error
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

    # Build theater configuration
    if advanced_config:
        try:
            theater_config = yaml.safe_load(advanced_config) if isinstance(advanced_config, str) else advanced_config
            if not isinstance(theater_config, dict):
                raise ValueError("Theater configuration must be a YAML mapping.")
        except (yaml.YAMLError, ValueError) as error:
            raise HTTPException(status_code=400, detail=f"Invalid theater configuration: {error}")
    else:
        theater_config = build_theater_config(
            creation_mode=creation_mode,
            folder_config_yaml=folder_config_yaml,
            adv_config=adv_config,
            special_instructions=special_instructions,
            style=style,
            enable_image_generation=enable_image_generation,
            use_generated_music=use_generated_music,
            enable_scene_animations=enable_scene_animations,
            enable_interactive_canvas=enable_interactive_canvas,
            enable_adventure_mode=enable_adventure_mode,
            story_planning_style=story_planning_style,
        )

    theater_id = f"theater_{uuid.uuid4().hex[:8]}"
    try:
        metadata = theater_manager.create_theater(
            name=name,
            theater_id=theater_id,
            reference_files=reference_files,
            playlists_data=playlists_data,
            lore_files=lore_files,
            theater_config=theater_config,
            metadata_json=adventure_metadata,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    deployed_meta = theater_manager.deploy_theater(metadata.theater_id)

    # Record deployment & deduct credits (0.0 cost)
    db.record_deployment(
        deployed_meta.theater_id,
        user["id"],
        deployed_meta.join_key,
        cost=0.0,
        name=name or deployed_meta.name,
    )
    auth_session_cache.invalidate_user(user["id"])
    theater_access_cache.invalidate_theater(deployed_meta.theater_id)

    res_dict = deployed_meta.model_dump()
    res_dict["is_owner"] = True
    asyncio.create_task(_export_canvas_theater_async(deployed_meta.theater_id))
    return {"status": "ok", "theater_id": deployed_meta.theater_id, "theater": res_dict}

@app.post("/api/theaters/{theater_id}/deploy")
def deploy_existing_theater(theater_id: str, request: Request):
    """Deploy an existing created theater."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    theater_dir = theater_manager.theater(theater_id).directory()
    if not theater_dir.exists() or not (theater_dir / "theater.json").exists():
        theater_repository.reconstruct_theater(theater_id, theater_dir)
    
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
    if not dep:
        db.record_deployment(
            theater_id,
            user["id"],
            meta.join_key,
            cost=0.0,
            name=meta.name if meta else theater_id,
        )
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
    
    connections = canvas_states.get(theater_id).connections
    viewers = {
        user["id"]: {"id": user["id"], "username": user.get("username", "")}
        for user in connections.active_user_connections.values()
        if user and "id" in user
    }
    state["active_viewers"] = list(viewers.values())
    return state


@app.post("/api/theaters/{theater_id}/baton/contributors")
async def add_contributor(theater_id: str, req: AddContributorRequest, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.add_contributor_async(theater_id, owner_id=user["id"], target_user_id=req.target_user_id)
        theater_access_cache.invalidate_theater(theater_id)
        await broadcast_baton_update(theater_id, updated_state)
        return updated_state
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/theaters/{theater_id}/baton/contributors/{target_user_id}")
async def remove_contributor(theater_id: str, target_user_id: int, request: Request):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    try:
        updated_state = await db.remove_contributor_async(theater_id, owner_id=user["id"], target_user_id=target_user_id)
        theater_access_cache.invalidate_theater(theater_id)
        await _sync_agent_controller(theater_id, updated_state)
        await broadcast_baton_update(theater_id, updated_state)
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
        await broadcast_baton_update(theater_id, updated_state)
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
        await broadcast_baton_update(theater_id, updated_state)
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
        await broadcast_baton_update(theater_id, updated_state)
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
        await broadcast_baton_update(theater_id, updated_state)
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

    asyncio.create_task(_export_canvas_theater_async(theater_id))
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
        theater_repository.reconstruct_theater(theater_id, theater_dir)
    # Ensure current displayed image is saved into the theater directory
    cs = canvas_states.get(theater_id)
    cs.save_local_theater_data(theater_dir=theater_dir)

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
    """Generate structured scene suggestions from the current notepad entries."""
    await _require_canvas_access_async(request, theater_id)
    _safe_path_param(theater_id, "theater_id")

    session = live_agent_manager.get_session(theater_id)
    named_elements = []
    notepad = _session_notepad(session)
    if notepad and hasattr(notepad, "get_present_elements"):
        named_elements = notepad.get_present_elements()
    if not named_elements and canvas_states:
        try:
            named_elements = canvas_states.get(theater_id).story.sticky_notes()
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

    session = live_agent_manager.get_session(theater_id)
    sticky_notes = []
    notepad = _session_notepad(session)
    if notepad and hasattr(notepad, "get_present_sticky_notes"):
        sticky_notes = notepad.get_present_sticky_notes()
    elif notepad and hasattr(notepad, "get_present_elements"):
        sticky_notes = notepad.get_present_elements()

    if not sticky_notes and canvas_states:
        try:
            sticky_notes = canvas_states.get(theater_id).story.sticky_notes()
        except Exception:
            pass

    hidden_stickies = []
    try:
        th_cfg = theater_manager.get_theater_config(theater_id)
        sp_cfg = th_cfg.get("story_planning", {}) if isinstance(th_cfg.get("story_planning"), dict) else {}
        raw_hidden = sp_cfg.get("hidden_stickies", th_cfg.get("hidden_stickies", []))
        if isinstance(raw_hidden, (list, tuple, set)):
            hidden_stickies = [str(x.get("topic", x.get("name", x)) if isinstance(x, dict) else x).strip() for x in raw_hidden if x]
        elif isinstance(raw_hidden, str):
            hidden_stickies = [s.strip() for s in raw_hidden.split(",") if s.strip()]
        elif isinstance(raw_hidden, dict):
            hidden_stickies = [str(k).strip() for k in raw_hidden.keys() if str(k).strip()]
        structured = sp_cfg.get("stickies", {})
        if isinstance(structured, dict):
            hidden_stickies.extend(
                str(topic).strip()
                for topic, definition in structured.items()
                if isinstance(definition, dict) and definition.get("hidden")
            )
            hidden_stickies = list(dict.fromkeys(hidden_stickies))
    except Exception:
        hidden_stickies = []

    return {
        "sticky_notes": sticky_notes,
        "hidden_stickies": hidden_stickies,
        "count": len(sticky_notes),
    }



