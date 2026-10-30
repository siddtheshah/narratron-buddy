"""Authenticated editor for the theater checkout shared with the canvas."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import io
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Iterator
import uuid
import zipfile

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
import yaml

from api_server.dependencies import canvas_states, live_agent_manager, pricing_controller
from api_server.canvas import broadcast_baton_update
from api_server.pages import SeoMetadata, render_page_template
from api_server.shared import app, db, get_current_user_async, theater_manager, theater_repository
from api_server.theater_access_cache import theater_access_cache
from providers.image_provider import ImageReference
from providers.music_provider import MusicGenerationRequest
from providers.registry import get_music_provider
from services.theater_image_generation import generate_theater_image
from services.theater_media_generation import generate_theater_media
from services.theater_checkout import checkout_locks as _draft_locks, refresh_checkout_metadata, save_theater_checkout
from services.generation_billing import (
    billing_locks as _billing_locks,
    MAX_CONCURRENT_GENERATION_JOBS,
    GENERATION_SLOT_TTL_SECONDS,
)
from services.google_asset_importer import find_google_urls, import_google_link
from services.theater_builder import (
    BuilderFile, BuilderProposal, ChatMessage, DraftInfo, FileMove, FileWrite, GenerationRequest, IneligibleAssetError,
    IMAGE_EXTENSIONS, MAX_DRAFT_BYTES, MAX_FILE_BYTES, MAX_FILES, TheaterBuilderStore,
    asset_mime, encode_text_writes, safe_asset_path, upload_path, validate_text,
)
from utils.auth_cache import auth_session_cache
from utils.config_loader import get_app_config, get_theater_default_config

logger = logging.getLogger(__name__)


class CreateDraftRequest(BaseModel):
    name: str = Field(default="My Theater", min_length=1, max_length=120)
    populate_default: bool = True


class SaveDraftRequest(BaseModel):
    revision: int
    name: str = Field(min_length=1, max_length=120)
    writes: list[FileWrite] = Field(default_factory=list, max_length=20)
    persist: bool = False


class AssistantRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)


class ApplyProposalRequest(BaseModel):
    revision: int
    proposal: BuilderProposal


class RevisionRequest(BaseModel):
    revision: int


class ResetTheaterRequest(BaseModel):
    revision: int | None = None


ClearOutputRequest = ResetTheaterRequest


class DeleteDraftFileRequest(BaseModel):
    revision: int
    path: str = Field(min_length=1, max_length=500)


class GenerateDraftRequest(GenerationRequest):
    revision: int


class GoogleLinkRequest(BaseModel):
    revision: int
    url: str = Field(min_length=5, max_length=2000)
    target_name: str | None = Field(default=None, max_length=100)
    harvest: bool = False
    harvest_prompt: str | None = Field(default=None, max_length=4000)


class DraftResponse(BaseModel):
    draft: DraftInfo
    files: list[BuilderFile]
    rates: dict[str, float]


class ImportDraftResponse(DraftResponse):
    imported_files: int
    discarded_files: list[str]


def store() -> TheaterBuilderStore:
    return TheaterBuilderStore(Path(theater_manager.base_dir), theater_manager.notify_storage_changed)


def write_checkout(info: DraftInfo, files: dict[str, bytes], moves: list[FileMove] | None = None, deletions: list[str] | None = None) -> None:
    """Apply edits to the working theater and refresh cached runtime configuration."""
    live_agent_manager.stop_session(info.theater_id)
    store().write_files(info, files, moves, deletions)
    refresh_checkout_metadata(info, theater_manager)
    canvas_states.reload_package(info.theater_id)
    theater_access_cache.invalidate_theater(info.theater_id)


def initialize_theater(info: DraftInfo) -> None:
    refresh_checkout_metadata(info, theater_manager)
    metadata = theater_manager.get_theater(info.theater_id)
    try:
        if not save_theater_checkout(info.theater_id, theater_manager, theater_repository):
            raise HTTPException(status_code=503, detail="Could not save the new theater. Please try again.")
        if not db.record_deployment(info.theater_id, info.owner_id, metadata.join_key, cost=0.0, name=info.name):
            raise HTTPException(status_code=503, detail="Could not register the new theater. Please try again.")
    except Exception:
        theater_manager.destroy_theater(info.theater_id)
        theater_repository.delete_theater(info.theater_id)
        raise
    info.saved_revision = info.revision
    theater_access_cache.invalidate_theater(info.theater_id)


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
    deployment = await asyncio.to_thread(db.get_deployment, theater_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Theater not found.")
    if deployment["user_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only the theater owner can edit this theater.")
    if (deployment.get("active_orator_id") or owner_id) != owner_id:
        raise HTTPException(status_code=409, detail="The baton was passed to another user. Reopen the theater editor to take it back before editing.")
    with invalid_input():
        draft = await asyncio.to_thread(store().load, theater_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Theater draft not found.")
    if draft.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Only the theater owner can edit this theater.")
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
        await asyncio.to_thread(initialize_theater, info)
    return await asyncio.to_thread(response, info)


async def read_uploads(files: list[UploadFile], folder: bool, root: Path, existing: set[str]) -> tuple[dict[str, bytes], list[str]]:
    if len(files) > MAX_FILES:
        raise ValueError("Upload supports at most 500 files.")
    uploaded: dict[str, bytes] = {}
    discarded: list[str] = []
    total = 0
    for upload in files:
        filename = upload.filename or ""
        try:
            relative = upload_path(filename, folder)
            safe_asset_path(root, relative)
        except IneligibleAssetError:
            discarded.append(filename)
            continue
        if relative in uploaded:
            raise ValueError(f"Duplicate upload destination: {relative}")
        if relative in existing and not folder:
            raise ValueError(f"{relative} already exists. Rename the asset before uploading.")
        content = await upload.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("Each asset must be at most 10MB.")
        total += len(content)
        if total > MAX_DRAFT_BYTES:
            raise ValueError("Upload must be at most 100MB.")
        uploaded[relative] = content
    if not uploaded:
        raise ValueError("No eligible theater files were found to import.")
    return uploaded, discarded


@app.post("/api/theater-editor/drafts/import")
async def import_draft(request: Request, files: list[UploadFile] = File(...), name: str = Form("My Theater", min_length=1, max_length=120), folder: bool = Form(False)) -> ImportDraftResponse:
    owner_id = await require_user(request)
    builder = store()
    with invalid_input():
        uploaded, discarded = await read_uploads(files, folder, builder.root / "_import", set())
        info = await asyncio.to_thread(builder.create, owner_id, name, "live_agent: {}\n", initial_files=uploaded, populate_default=False)
        await asyncio.to_thread(initialize_theater, info)
    result = await asyncio.to_thread(response, info)
    return ImportDraftResponse(**result.model_dump(), imported_files=len(uploaded), discarded_files=discarded)


@app.get("/api/theater-editor/{theater_id}")
async def open_draft(theater_id: str, request: Request) -> DraftResponse:
    owner_id = await require_user(request)
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        deployment = await asyncio.to_thread(db.get_deployment, theater_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Theater not found.")
        if deployment["user_id"] != owner_id:
            raise HTTPException(status_code=403, detail="Only the theater owner can edit this theater.")
        with invalid_input():
            root = store().directory(theater_id)
            if not (root / "theater.yaml").is_file():
                if not await asyncio.to_thread(theater_repository.reconstruct_theater, theater_id, root):
                    raise HTTPException(status_code=404, detail="Theater package not found.")
            info = await asyncio.to_thread(store().load, theater_id)
        if info is None:
            metadata = await asyncio.to_thread(theater_manager.get_theater, theater_id)
            info = DraftInfo(theater_id=theater_id, owner_id=owner_id, name=metadata.name)
            await asyncio.to_thread(store().save_info, info)
        # Entering the editor returns control to the owner and cancels pending
        # handoffs, so another user cannot accept an old request afterward.
        if (deployment.get("active_orator_id") or owner_id) != owner_id or deployment.get("baton_request"):
            with invalid_input():
                baton_state = await db.take_back_baton_async(theater_id, owner_id=owner_id)
            theater_access_cache.invalidate_theater(theater_id)
            live_agent_manager.set_active_controller(theater_id, owner_id)
            await broadcast_baton_update(theater_id, baton_state)
        info = await require_draft(request, theater_id)
        with invalid_input():
            await asyncio.to_thread(store().import_session_references, info)
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
            if body.writes or info.name != theater_manager.get_theater(theater_id).name:
                await asyncio.to_thread(write_checkout, info, encode_text_writes(body.writes))
            if body.persist:
                canvas_states.persist(theater_id)
                if not await asyncio.to_thread(save_theater_checkout, theater_id, theater_manager, theater_repository):
                    raise HTTPException(status_code=503, detail="Could not save the theater. Your edits are still in the working copy.")
                info.saved_revision = info.revision
                await asyncio.to_thread(db.update_theater_name, theater_id, info.name, user_id=info.owner_id)
        return await asyncio.to_thread(response, info)


@app.post("/api/theater-editor/{theater_id}/upload")
async def upload_assets(theater_id: str, request: Request, files: list[UploadFile] = File(...), revision: int = Form(...), folder: bool = Form(False)) -> ImportDraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        with invalid_input():
            check_revision(info, revision)
            existing = {item.path for item in await asyncio.to_thread(store().files, theater_id)}
            uploaded, discarded = await read_uploads(files, folder, store().directory(theater_id), existing)
            await asyncio.to_thread(write_checkout, info, uploaded)
        result = await asyncio.to_thread(response, info)
        return ImportDraftResponse(**result.model_dump(), imported_files=len(uploaded), discarded_files=discarded)


@app.post("/api/theater-editor/{theater_id}/google-link")
async def import_google_resource(theater_id: str, body: GoogleLinkRequest, request: Request) -> dict[str, JsonValue]:
    owner_id = await require_user(request)
    async with _billing_locks.setdefault(owner_id, asyncio.Lock()), _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        with invalid_input():
            result = await import_google_link(body.url, custom_name=body.target_name)

        if result.kind == "doc" and body.harvest:
            cost = pricing_controller.get_rates()["theater_editor_assistant_credit_rate"]
            user = await asyncio.to_thread(db.get_user_by_id, owner_id)
            if not user or user["credits"] < cost:
                raise HTTPException(status_code=402, detail=f"Harvesting a Google Doc requires {cost:g} credits. Buy credits using the balance at the top of this page.")
            try:
                proposal = await asyncio.to_thread(store().harvest_doc, info, result.title, result.text_content, body.harvest_prompt or "", get_app_config())
            except Exception as error:
                logger.exception("Google Doc harvesting failed")
                raise HTTPException(status_code=502, detail="The assistant could not harvest the Google Doc into a valid proposal. No credits were charged. Please try again.") from error
            try:
                updated = await asyncio.to_thread(db.record_user_usage, owner_id,
                    credit_cost=cost, idempotency_key=f"builder:harvest:{theater_id}:{uuid.uuid4().hex}")
            except Exception as error:
                logger.exception("Google Doc harvest billing failed")
                raise HTTPException(status_code=503, detail="Could not settle assistant credits. Please try again.") from error
            auth_session_cache.invalidate_user(owner_id)
            return {
                "kind": "doc",
                "harvested": True,
                "proposal": proposal.model_dump(mode="json"),
                "revision": info.revision,
                "credits_charged": cost,
                "credits": float(updated["credits"]),
                "state": (await asyncio.to_thread(response, info)).model_dump(mode="json"),
                "message": f"Harvested '{result.title}' into theater proposal."
            }

        files_to_write: dict[str, bytes] = {}
        if result.kind == "doc":
            files_to_write[result.suggested_path] = result.text_content.encode("utf-8")
        else:
            files_to_write[result.suggested_path] = result.content_bytes

        with invalid_input():
            await asyncio.to_thread(write_checkout, info, files_to_write)

        return {
            "kind": result.kind,
            "harvested": False,
            "path": result.suggested_path,
            "revision": info.revision,
            "state": (await asyncio.to_thread(response, info)).model_dump(mode="json"),
            "message": f"Imported {result.suggested_path} from Google."
        }


@app.post("/api/theater-editor/{theater_id}/assistant")
async def ask_assistant(theater_id: str, body: AssistantRequest, request: Request) -> dict[str, JsonValue]:
    owner_id = await require_user(request)
    async with _billing_locks.setdefault(owner_id, asyncio.Lock()), _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        cost = pricing_controller.get_rates()["theater_editor_assistant_credit_rate"]
        user = await asyncio.to_thread(db.get_user_by_id, owner_id)
        if not user or user["credits"] < cost:
            raise HTTPException(status_code=402, detail=f"Each assistant turn requires {cost:g} credits. Buy credits using the balance at the top of this page.")

        # Detect any Google links in the user prompt and resolve them before asking the assistant
        harvest_docs: list[dict[str, str]] = []
        downloaded_assets: list[str] = []
        imported_files: dict[str, bytes] = {}
        for link in find_google_urls(body.prompt):
            try:
                with invalid_input():
                    imported = await import_google_link(link.original_url)
                if imported.kind == "doc":
                    harvest_docs.append({"title": imported.title, "content": imported.text_content[:50_000]})
                elif imported.kind in {"image", "audio", "text"}:
                    content = imported.content_bytes if imported.kind != "text" else imported.text_content.encode("utf-8")
                    imported_files[imported.suggested_path] = content
                    downloaded_assets.append(imported.suggested_path)
            except HTTPException:
                raise
            except Exception as import_err:
                logger.exception("Could not auto-import Google link %s", link.original_url)
                raise HTTPException(status_code=502, detail="Could not import the Google link. No assistant credits were charged. Check the link and its sharing permissions.") from import_err

        if imported_files:
            with invalid_input():
                await asyncio.to_thread(write_checkout, info, imported_files)

        augmented_prompt = body.prompt
        if downloaded_assets:
            augmented_prompt += f"\n[System note: Downloaded Google Drive assets to draft: {', '.join(downloaded_assets)}]"

        try:
            if harvest_docs:
                proposal = await asyncio.to_thread(store().propose, info, augmented_prompt, body.history, get_app_config(), harvest_docs=harvest_docs)
            else:
                proposal = await asyncio.to_thread(store().propose, info, augmented_prompt, body.history, get_app_config())
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
                "credits_charged": cost, "credits": float(updated["credits"]),
                "state": (await asyncio.to_thread(response, info)).model_dump(mode="json") if downloaded_assets else None}


@app.post("/api/theater-editor/{theater_id}/apply")
async def apply_proposal(theater_id: str, body: ApplyProposalRequest, request: Request) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        with invalid_input():
            await asyncio.to_thread(write_checkout, info, encode_text_writes(body.proposal.writes), body.proposal.moves, body.proposal.deletions)
        return await asyncio.to_thread(response, info)


@app.post("/api/theater-editor/{theater_id}/delete")
async def delete_draft_file(theater_id: str, body: DeleteDraftFileRequest, request: Request) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, body.revision)
        with invalid_input():
            await asyncio.to_thread(write_checkout, info, {}, deletions=[body.path])
        return await asyncio.to_thread(response, info)


@app.delete("/api/theater-editor/{theater_id}/file")
async def remove_draft_file(theater_id: str, path: str, revision: int, request: Request) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        check_revision(info, revision)
        with invalid_input():
            await asyncio.to_thread(write_checkout, info, {}, deletions=[path])
        return await asyncio.to_thread(response, info)


def generate_asset(info: DraftInfo, body: GenerationRequest) -> tuple[str, bytes]:
    root = store().directory(info.theater_id)
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", body.name).strip("_") or "asset"
    identifier = uuid.uuid4().hex[:12]
    if body.kind in ("reference", "stamp", "character", "scene"):
        references: list[ImageReference] = []
        for relative in body.references:
            path = safe_asset_path(root, relative)
            if not relative.startswith("references/") or not path.is_file():
                raise ValueError("Generation references must be existing reference, scene, or character images.")
            references.append(ImageReference(name=path.name, data=path.read_bytes(), mime_type=asset_mime(path)))
        result = generate_theater_image(root, kind=body.kind, prompt=body.prompt, references=references)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(result.mime_type)
        if not result.image_bytes or extension is None:
            raise ValueError("Image provider returned no supported image.")
        if body.kind == "character":
            char_folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", body.name.strip()).strip(". ") or name
            char_dir = root / "references" / "characters" / char_folder
            iteration = 1
            if char_dir.is_dir():
                nums = [
                    int(p.stem) for p in char_dir.iterdir()
                    if p.is_file() and p.stem.isdigit()
                ]
                if nums:
                    iteration = max(nums) + 1
            return f"references/characters/{char_folder}/{iteration}{extension}", result.image_bytes
        if body.kind == "scene":
            scene_folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", body.name.strip()).strip(". ") or name
            scene_dir = root / "references" / "scenes" / scene_folder
            iteration = 1
            if scene_dir.is_dir():
                nums = [
                    int(p.stem) for p in scene_dir.iterdir()
                    if p.is_file() and p.stem.isdigit()
                ]
                if nums:
                    iteration = max(nums) + 1
            return f"references/scenes/{scene_folder}/{iteration}{extension}", result.image_bytes
        target_dir = "stamps" if body.kind == "stamp" else "references"
        return f"{target_dir}/{name}_{identifier}{extension}", result.image_bytes
    playlist = re.sub(r"[^a-zA-Z0-9_-]", "_", body.playlist).strip("_") or "ambient"
    settings = get_app_config().get("music", {})
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


def generate_assets(info: DraftInfo, body: GenerationRequest) -> tuple[str, dict[str, bytes]]:
    """Stage every file for one generation before persistence and billing."""
    if body.kind == "animation":
        name = re.sub(r"[^a-zA-Z0-9_-]", "_", body.name).strip("_") or "animation"
        identifier = f"{name}_{uuid.uuid4().hex[:12]}"
        files = generate_theater_media(
            store().directory(info.theater_id), kind="animation", prompt=body.prompt,
            identifier=identifier, animation_type=body.animation_type,
        )
        return f"animations/{identifier}/{body.animation_type}.json", files
    path, content = generate_asset(info, body)
    return path, {path: content}


@app.post("/api/theater-editor/{theater_id}/generate")
async def generate_draft_asset(theater_id: str, body: GenerateDraftRequest, request: Request) -> dict[str, JsonValue]:
    owner_id = await require_user(request)
    slot_id = await asyncio.to_thread(
        db.acquire_generation_slot,
        owner_id,
        "editor",
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
        async with _billing_locks.setdefault(owner_id, asyncio.Lock()), _draft_locks.setdefault(theater_id, asyncio.Lock()):
            info = await require_draft(request, theater_id)
            check_revision(info, body.revision)
            rates = pricing_controller.get_rates()
            cost = rates["layered_animation_credit_rate" if body.kind == "animation" else "music_credit_rate" if body.kind == "playlist" else "image_credit_rate"]
            user = await asyncio.to_thread(db.get_user_by_id, owner_id)
            if not user or user["credits"] < cost:
                raise HTTPException(status_code=402, detail=f"This generation requires {cost:g} credits. Top up on /deploy.")
        try:
            path, files = await asyncio.to_thread(generate_assets, info, body)
        except Exception as error:
            logger.exception("Theater builder asset generation failed")
            raise HTTPException(status_code=502, detail="Generation failed; no credits were charged.") from error

        async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
            try:
                info = await require_draft(request, theater_id)
                # Validate capacity and persist before charging, just like successful live generation.
                await asyncio.to_thread(write_checkout, info, files)
            except HTTPException:
                raise
            except Exception as error:
                logger.exception("Theater builder asset write failed")
                raise HTTPException(status_code=502, detail="Generation failed; no credits were charged.") from error

        async with _billing_locks.setdefault(owner_id, asyncio.Lock()):
            try:
                updated = await asyncio.to_thread(db.record_user_usage, owner_id,
                    images_created=1 if body.kind in ("reference", "stamp", "character", "scene") else 0,
                    music_created=1 if body.kind == "playlist" else 0,
                    layered_animations_created=1 if body.kind == "animation" else 0, credit_cost=cost,
                    idempotency_key=f"builder:{theater_id}:{path}")
            except Exception as error:
                # Do not leave an unbilled asset available if settlement failed.
                for filename in files:
                    safe_asset_path(store().directory(theater_id), filename).unlink(missing_ok=True)
                await asyncio.to_thread(theater_manager.notify_storage_changed, theater_id)
                logger.exception("Theater builder billing failed")
                raise HTTPException(status_code=503, detail="Could not settle generation credits. Please reload the draft.") from error
            auth_session_cache.invalidate_user(owner_id)
            return {"path": path, "credits_charged": cost, "credits": float(updated["credits"]), "state": (await asyncio.to_thread(response, info)).model_dump(mode="json")}
    finally:
        await asyncio.to_thread(db.release_generation_slot, slot_id)


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
        images = [item for item in builder.files(info.theater_id) if item.kind == "image" and item.path.startswith("references/")]
        matching_alias = any(Path(item.path).stem == str(starting_image) for item in images)
        if (image is None or not image.is_file()) and not matching_alias:
            raise ValueError("starting_image must point to an existing reference image.")
    live_agent_manager.stop_session(info.theater_id)
    refresh_checkout_metadata(info, theater_manager)
    theater_manager.deploy_theater(info.theater_id)
    canvas_states.reload_package(info.theater_id)
    canvas_states.get(info.theater_id).persist()
    if not save_theater_checkout(info.theater_id, theater_manager, theater_repository):
        raise HTTPException(status_code=503, detail="Could not save the theater. Your edits are still in the working copy.")
    db.update_theater_name(info.theater_id, info.name, user_id=info.owner_id)
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


def _clear_directory_contents(target: Path) -> int:
    if not target.is_dir():
        return 0
    cleared = 0
    for item in list(target.iterdir()):
        if item.is_dir():
            shutil.rmtree(item)
            cleared += 1
        elif item.is_file():
            item.unlink()
            cleared += 1
    return cleared


def _remove_reference_updates(root: Path) -> None:
    for image in root.rglob("*"):
        if (image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS
                and image.stem.isdecimal() and int(image.stem) > 1):
            image.unlink()


@app.post("/api/theater-editor/{theater_id}/reset")
async def reset_theater_draft(theater_id: str, request: Request, body: ResetTheaterRequest | None = None) -> DraftResponse:
    async with _draft_locks.setdefault(theater_id, asyncio.Lock()):
        info = await require_draft(request, theater_id)
        if body is not None and body.revision is not None:
            check_revision(info, body.revision)

        live_agent_manager.stop_session(theater_id)
        await asyncio.to_thread(theater_manager.notify_storage_changed, theater_id)
        roots = {store().directory(theater_id).resolve(), theater_repository.theater_path(theater_id).resolve()}
        for root in roots:
            await asyncio.to_thread(_remove_reference_updates, root / "references")

        for root in roots:
            await asyncio.to_thread(_clear_directory_contents, root / "output")

        for root in roots:
            (root / "theater_state.json").unlink(missing_ok=True)
            metadata_path = root / "theater.json"
            if metadata_path.is_file():
                document = TypeAdapter(dict[str, JsonValue]).validate_json(metadata_path.read_text(encoding="utf-8"))
                document["canvas_state"] = {}
                metadata_path.write_text(json.dumps(document), encoding="utf-8")
        canvas_states.reset(theater_id)
        await asyncio.to_thread(theater_manager.notify_storage_changed, theater_id)
        theater_access_cache.invalidate_theater(info.theater_id)

        return await asyncio.to_thread(response, info)


@app.get("/api/theater-editor/{theater_id}/download")
@app.get("/api/theater-editor/{theater_id}/export")
async def download_draft(theater_id: str, request: Request) -> Response:
    info = await require_draft(request, theater_id)
    builder = store()
    root = builder.directory(info.theater_id)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_info in builder.files(info.theater_id):
            with invalid_input():
                file_path = safe_asset_path(root, file_info.path)
            if file_path.is_file():
                archive.write(file_path, arcname=file_info.path)
    zip_buffer.seek(0)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", info.name).strip("_") or info.theater_id
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
        "Cache-Control": "no-store",
    }
    return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers=headers)
