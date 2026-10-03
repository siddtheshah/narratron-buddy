"""Authenticated theater-builder API, with isolated drafts and shared live pricing."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import re
from typing import Iterator
import uuid

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
import yaml

from api_server.dependencies import live_agent_manager, pricing_controller
from api_server.pages import SeoMetadata, render_page_template
from api_server.shared import app, config, db, get_current_user_async, theater_manager, theater_repository
from api_server.theater_access_cache import theater_access_cache
from providers.image_provider import ImageGenerationRequest, ImageReference
from providers.music_provider import MusicGenerationRequest
from providers.registry import get_image_provider, get_music_provider
from services.theater_builder import (
    BuilderFile, BuilderProposal, ChatMessage, DraftInfo, FileWrite, GenerationRequest,
    MAX_DRAFT_BYTES, MAX_FILE_BYTES, MAX_FILES, TheaterBuilderStore,
    asset_mime, encode_text_writes, safe_asset_path, upload_path, validate_text,
)
from utils.auth_cache import auth_session_cache
from utils.config_loader import get_theater_default_config

logger = logging.getLogger(__name__)
# A draft has one writer at a time. Paid actions also serialize requests per owner.
_draft_locks: dict[str, asyncio.Lock] = {}
_billing_locks: dict[int, asyncio.Lock] = {}


class CreateDraftRequest(BaseModel):
    name: str = Field(default="My Theater", min_length=1, max_length=120)
    populate_default: bool = True


class SaveDraftRequest(BaseModel):
    revision: int
    name: str = Field(min_length=1, max_length=120)
    writes: list[FileWrite] = Field(default_factory=list, max_length=20)


class AssistantRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)


class ApplyProposalRequest(BaseModel):
    revision: int
    proposal: BuilderProposal


class RevisionRequest(BaseModel):
    revision: int


class GenerateDraftRequest(GenerationRequest):
    revision: int


class DraftResponse(BaseModel):
    draft: DraftInfo
    files: list[BuilderFile]
    rates: dict[str, float]


def store() -> TheaterBuilderStore:
    return TheaterBuilderStore(Path(theater_repository.base_dir))


@contextmanager
def invalid_input() -> Iterator[None]:
    try:
        yield
    except (ValueError, yaml.YAMLError, UnicodeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def response(info: DraftInfo) -> DraftResponse:
    return DraftResponse(draft=info, files=store().files(info.theater_id), rates=pricing_controller.get_rates())


async def require_user(request: Request) -> int:
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Log in to use the theater builder.")
    return int(user["id"])


async def require_draft(request: Request, theater_id: str) -> DraftInfo:
    owner_id = await require_user(request)
    with invalid_input():
        draft = await asyncio.to_thread(store().load, theater_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Theater draft not found.")
    if draft.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Only the theater owner can edit this theater.")
    if draft.source_id:
        deployment = await asyncio.to_thread(db.get_deployment, draft.source_id)
        if not deployment or deployment["user_id"] != owner_id:
            raise HTTPException(status_code=403, detail="The deployed theater is no longer owned by this account.")
    return draft


def check_revision(info: DraftInfo, revision: int) -> None:
    if info.revision != revision:
        raise HTTPException(status_code=409, detail="This draft changed in another tab. Reload it before applying changes.")


@app.get("/theater-editor", response_class=HTMLResponse)
def theater_editor_page() -> str:
    # The shell shows the shared login dialog; all draft data and actions require auth.
    return render_page_template("theater_editor.html", active_page="deploy",
        seo_metadata=SeoMetadata(title="Narratron Theater Builder", description="", path="/theater-editor", indexable=False))


@app.post("/api/theater-editor/drafts")
async def create_draft(body: CreateDraftRequest, request: Request) -> DraftResponse:
    owner_id = await require_user(request)
    default_config = get_theater_default_config()
    if not body.populate_default:
        default_config.pop("starting_image", None)
    with invalid_input():
        info = await asyncio.to_thread(store().create, owner_id, body.name, yaml.safe_dump(default_config, sort_keys=False), populate_default=body.populate_default)
    return await asyncio.to_thread(response, info)


@app.get("/api/theater-editor/{theater_id}")
async def open_draft(theater_id: str, request: Request) -> DraftResponse:
    owner_id = await require_user(request)
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        with invalid_input():
            info = await asyncio.to_thread(store().load, theater_id)
        if info is None:
            deployment = await asyncio.to_thread(db.get_deployment, theater_id)
            if not deployment:
                raise HTTPException(status_code=404, detail="Theater not found.")
            if deployment["user_id"] != owner_id:
                raise HTTPException(status_code=403, detail="Only the theater owner can edit this theater.")
            source = theater_manager.theater(theater_id).directory()
            if not (source / "theater.json").is_file():
                restored = await asyncio.to_thread(theater_repository.reconstruct_theater, theater_id, source)
                if not restored:
                    raise HTTPException(status_code=404, detail="Theater package not found.")
            metadata = await asyncio.to_thread(theater_manager.get_theater, theater_id)
            with invalid_input():
                await asyncio.to_thread(store().create, owner_id, metadata.name, yaml.safe_dump(get_theater_default_config()), theater_id, source)
        info = await require_draft(request, theater_id)
        return await asyncio.to_thread(response, info)


@app.get("/api/theater-editor/{theater_id}/file")
async def read_draft_file(theater_id: str, request: Request, path: str) -> FileResponse:
    await require_draft(request, theater_id)
    with invalid_input():
        target = safe_asset_path(store().directory(theater_id), path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    # Text is edited as source; never render uploaded HTML or Markdown as a page.
    return FileResponse(target, media_type=asset_mime(target), headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@app.post("/api/theater-editor/{theater_id}/save")
async def save_draft(theater_id: str, body: SaveDraftRequest, request: Request) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        info.name = body.name
        with invalid_input():
            await asyncio.to_thread(store().write_files, info, encode_text_writes(body.writes))
        return await asyncio.to_thread(response, info)


@app.post("/api/theater-editor/{theater_id}/upload")
async def upload_assets(theater_id: str, request: Request, files: list[UploadFile] = File(...), revision: int = Form(...), folder: bool = Form(False)) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        with invalid_input():
            check_revision(info, revision)
            if len(files) > MAX_FILES:
                raise ValueError("Upload supports at most 500 files.")
            uploaded: dict[str, bytes] = {}
            total = 0
            existing = {item.path for item in await asyncio.to_thread(store().files, theater_id)}
            for upload in files:
                filename = upload.filename or ""
                relative = upload_path(filename, folder)
                safe_asset_path(store().directory(theater_id), relative)
                if relative in uploaded:
                    raise ValueError(f"Duplicate upload destination: {relative}")
                # Folder import can replace configuration; individual uploads never silently overwrite.
                if relative in existing and not folder:
                    raise ValueError(f"{relative} already exists. Rename the asset before uploading.")
                content = await upload.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("Each asset must be at most 20MB.")
                total += len(content)
                if total > MAX_DRAFT_BYTES:
                    raise ValueError("Upload must be at most 100MB.")
                uploaded[relative] = content
            if not uploaded:
                raise ValueError("Choose a folder or assets to upload.")
            await asyncio.to_thread(store().write_files, info, uploaded)
        return await asyncio.to_thread(response, info)


@app.post("/api/theater-editor/{theater_id}/assistant")
async def ask_assistant(theater_id: str, body: AssistantRequest, request: Request) -> dict[str, JsonValue]:
    owner_id = await require_user(request)
    async with _billing_locks.setdefault(owner_id, asyncio.Lock()), _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        cost = pricing_controller.get_rates()["theater_editor_assistant_credit_rate"]
        user = await asyncio.to_thread(db.get_user_by_id, owner_id)
        if not user or user["credits"] < cost:
            raise HTTPException(status_code=402, detail=f"Each assistant turn requires {cost:g} credits. Buy credits using the balance at the top of this page.")
        try:
            proposal = await asyncio.to_thread(store().propose, info, body.prompt, body.history, config)
        except Exception as error:
            logger.exception("Theater builder assistant failed")
            raise HTTPException(status_code=502, detail="The assistant could not prepare a valid proposal. No credits were charged. Please try again.") from error
        try:
            updated = await asyncio.to_thread(db.record_user_usage, owner_id,
                credit_cost=cost, idempotency_key=f"builder:assistant:{theater_id}:{uuid.uuid4().hex}")
        except Exception as error:
            logger.exception("Theater builder assistant billing failed")
            raise HTTPException(status_code=503, detail="Could not settle assistant credits. Please try again.") from error
        auth_session_cache.invalidate_user(owner_id)
        return {"revision": info.revision, "proposal": proposal.model_dump(mode="json"),
                "credits_charged": cost, "credits": float(updated["credits"])}


@app.post("/api/theater-editor/{theater_id}/apply")
async def apply_proposal(theater_id: str, body: ApplyProposalRequest, request: Request) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        with invalid_input():
            await asyncio.to_thread(store().write_files, info, encode_text_writes(body.proposal.writes), body.proposal.moves)
        return await asyncio.to_thread(response, info)


def generate_asset(info: DraftInfo, body: GenerationRequest) -> tuple[str, bytes]:
    root = store().directory(info.theater_id)
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", body.name).strip("_") or "asset"
    identifier = uuid.uuid4().hex[:12]
    if body.kind == "reference":
        settings = config.get("visuals", {})
        references: list[ImageReference] = []
        for relative in body.references:
            path = safe_asset_path(root, relative)
            if not relative.startswith("references/") or not path.is_file():
                raise ValueError("Generation references must be existing reference images.")
            references.append(ImageReference(name=path.name, data=path.read_bytes(), mime_type=asset_mime(path)))
        provider = get_image_provider(str(settings.get("model") or ""), settings.get("model_options") or {})
        theater_settings = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load((root / "theater.yaml").read_text(encoding="utf-8")))
        visual_settings = TypeAdapter(dict[str, JsonValue]).validate_python(theater_settings.get("visuals", {}))
        style = str(visual_settings.get("style") or "").strip()
        prompt = f"{body.prompt}\nStyle: {style}" if style else body.prompt
        result = provider.generate(ImageGenerationRequest(prompt=prompt, references=references))
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(result.mime_type)
        if not result.image_bytes or extension is None:
            raise ValueError("Image provider returned no supported image.")
        return f"references/{name}_{identifier}{extension}", result.image_bytes
    playlist = re.sub(r"[^a-zA-Z0-9_-]", "_", body.playlist).strip("_") or "ambient"
    settings = config.get("music", {})
    provider = get_music_provider(str(settings.get("provider") or "lyria"), settings.get("provider_options") or {})
    theater_settings = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load((root / "theater.yaml").read_text(encoding="utf-8")))
    music_settings = TypeAdapter(dict[str, JsonValue]).validate_python(theater_settings.get("music", {}))
    style = str(music_settings.get("style") or "").strip()
    prompt = f"{body.prompt}\nStyle: {style}" if style else body.prompt
    music = provider.generate(MusicGenerationRequest(prompt=prompt))
    extension = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg", "audio/flac": ".flac"}.get(music.mime_type)
    if not music.audio_bytes or extension is None:
        raise ValueError("Music provider returned no supported audio.")
    return f"playlists/{playlist}/{name}_{identifier}{extension}", music.audio_bytes


@app.post("/api/theater-editor/{theater_id}/generate")
async def generate_draft_asset(theater_id: str, body: GenerateDraftRequest, request: Request) -> dict[str, JsonValue]:
    owner_id = await require_user(request)
    async with _billing_locks.setdefault(owner_id, asyncio.Lock()), _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        rates = pricing_controller.get_rates()
        cost = rates["image_credit_rate" if body.kind == "reference" else "music_credit_rate"]
        user = await asyncio.to_thread(db.get_user_by_id, owner_id)
        if not user or user["credits"] < cost:
            raise HTTPException(status_code=402, detail=f"This generation requires {cost:g} credits. Top up on /deploy.")
        try:
            path, content = await asyncio.to_thread(generate_asset, info, body)
            # Validate capacity and persist before charging, just like successful live generation.
            await asyncio.to_thread(store().write_files, info, {path: content})
        except Exception as error:
            logger.exception("Theater builder asset generation failed")
            raise HTTPException(status_code=502, detail="Generation failed; no credits were charged.") from error
        try:
            updated = await asyncio.to_thread(db.record_user_usage, owner_id,
                images_created=1 if body.kind == "reference" else 0,
                music_created=1 if body.kind == "playlist" else 0, credit_cost=cost,
                idempotency_key=f"builder:{theater_id}:{path}")
        except Exception as error:
            # Do not leave an unbilled asset available if settlement failed.
            safe_asset_path(store().directory(theater_id), path).unlink(missing_ok=True)
            logger.exception("Theater builder billing failed")
            raise HTTPException(status_code=503, detail="Could not settle generation credits. Please reload the draft.") from error
        auth_session_cache.invalidate_user(owner_id)
        return {"path": path, "credits_charged": cost, "credits": float(updated["credits"]), "state": (await asyncio.to_thread(response, info)).model_dump(mode="json")}


def publish_draft(info: DraftInfo) -> str:
    builder = store()
    root = builder.directory(info.theater_id)
    config_text = safe_asset_path(root, "theater.yaml").read_text(encoding="utf-8")
    validate_text("theater.yaml", config_text)
    theater_config = TypeAdapter(dict[str, JsonValue]).validate_python(yaml.safe_load(config_text))
    starting_image = theater_config.get("starting_image")
    if starting_image:
        relative = str(starting_image)
        if not relative.startswith("references/"):
            relative = f"references/{relative}"
        image = safe_asset_path(root, relative) if Path(relative).suffix else None
        images = [item for item in builder.files(info.theater_id) if item.kind == "image"]
        matching_alias = any(Path(item.path).stem == str(starting_image) for item in images)
        if (image is None or not image.is_file()) and not matching_alias:
            raise ValueError("starting_image must point to an existing reference image.")
    target = theater_manager.theater(info.theater_id).directory()
    if info.source_id is None:
        theater_manager.create_theater(name=info.name, theater_id=info.theater_id, theater_config=theater_config)
    live_agent_manager.stop_session(info.theater_id)
    builder.copy_to(info, target)
    metadata = theater_manager.get_theater(info.theater_id)
    metadata.name = info.name
    metadata.config = theater_config
    metadata.mounted_references = [item.path.removeprefix("references/") for item in builder.files(info.theater_id) if item.kind == "image"]
    metadata.mounted_playlists = {}
    for item in builder.files(info.theater_id):
        if item.kind == "audio":
            playlist, filename = item.path.removeprefix("playlists/").split("/", 1)
            metadata.mounted_playlists.setdefault(playlist, []).append(filename)
    # Metadata belongs to the theater manager; retain its join key and collaboration state.
    theater_manager._save_metadata(metadata)
    deployed = theater_manager.deploy_theater(info.theater_id)
    if info.source_id is None:
        db.record_deployment(info.theater_id, info.owner_id, deployed.join_key, cost=0.0, name=info.name)
        info.source_id = info.theater_id
        builder.save_info(info)
    else:
        db.update_theater_name(info.theater_id, info.name, user_id=info.owner_id)
    # Mirror removals into the durable package; export otherwise merges stale assets.
    repository_target = theater_repository.theater_path(info.theater_id)
    repository_target.mkdir(parents=True, exist_ok=True)
    if repository_target.resolve() != target.resolve():
        builder.copy_to(info, repository_target)
    if not theater_repository.export_theater(info.theater_id, target):
        raise RuntimeError("Could not persist the deployed theater.")
    theater_access_cache.invalidate_theater(info.theater_id)
    return f"/canvas?theater_id={info.theater_id}&role=orator"


@app.post("/api/theater-editor/{theater_id}/deploy")
async def deploy_draft(theater_id: str, body: RevisionRequest, request: Request) -> dict[str, str]:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        with invalid_input():
            canvas_url = await asyncio.to_thread(publish_draft, info)
        return {"theater_id": info.theater_id, "canvas_url": canvas_url}
