"""Standalone development server for the browser-based Narratron test labs."""

import json
import mimetypes
from pathlib import Path
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Any

from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from providers import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageReference,
    MusicGenerationRequest,
    MusicProviderError,
    SpeechProviderError,
    SpeechSynthesisRequest,
    TextResponseRequest,
    TextResponseProviderError,
    get_image_provider,
    get_music_provider,
    get_text_response_provider,
    get_speech_provider,
    list_image_provider_specs,
    list_music_provider_specs,
    list_music_adapter_specs,
    list_text_response_provider_specs,
    list_speech_provider_specs,
)
from testlab.image_benchmark import ROOT as BENCHMARK_ROOT, BenchmarkPrompt, get_prompt, prompt_catalog
from testlab.music_benchmark import BenchmarkMusicPrompt, get_music_prompt, music_prompt_catalog
from testlab.text_response_benchmark import BenchmarkTextPrompt, get_text_prompt, text_prompt_catalog
from testlab.speech_benchmark import BenchmarkSpeechPrompt, get_speech_prompt, speech_prompt_catalog
from testlab.a2ui_canvas_lab import (
    A2UICanvasTestConfig,
    default_canvas_config,
    run_canvas_test,
)
from testlab.adventure_runner import (
    AdventureSession,
    list_available_adventures,
)
from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from tools.story_planning_tool import StoryPlanningTools

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
load_dotenv(PROJECT_ROOT / ".env")

app = FastAPI(title="Narratron Test Lab")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/effects-static", StaticFiles(directory=PROJECT_ROOT / "static"), name="effects-static")
app.mount("/carousel", StaticFiles(directory=PROJECT_ROOT / "templates" / "carousel"), name="carousel")
app.mount("/test-images", StaticFiles(directory=ROOT / "images"), name="test-images")
BENCHMARK_OUTPUT = ROOT / "benchmark_output"
BENCHMARK_OUTPUT.mkdir(exist_ok=True)
app.mount("/benchmark-images", StaticFiles(directory=BENCHMARK_OUTPUT), name="benchmark-images")

BENCHMARK_MUSIC_OUTPUT = ROOT / "benchmark_music_output"
BENCHMARK_MUSIC_OUTPUT.mkdir(exist_ok=True)
app.mount("/benchmark-audio", StaticFiles(directory=BENCHMARK_MUSIC_OUTPUT), name="benchmark-audio")

BENCHMARK_SPEECH_OUTPUT = ROOT / "benchmark_speech_output"
BENCHMARK_SPEECH_OUTPUT.mkdir(exist_ok=True)
app.mount("/benchmark-speech", StaticFiles(directory=BENCHMARK_SPEECH_OUTPUT), name="benchmark-speech")

_runs: dict[str, dict[str, Any]] = {}
_music_runs: dict[str, dict[str, Any]] = {}
_text_runs: dict[str, dict[str, Any]] = {}
_speech_runs: dict[str, dict[str, Any]] = {}
_story_planner_runs: dict[str, dict[str, Any]] = {}
_a2ui_canvas_runs: dict[str, dict[str, Any]] = {}
_adventure_runner_sessions: dict[str, AdventureSession] = {}
_runs_lock = threading.Lock()

MAX_IN_FLIGHT_PER_PROVIDER = 5
CANVAS_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
CANVAS_IMAGE_ROOTS = (ROOT / "images", PROJECT_ROOT / "theaters")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/vad", include_in_schema=False)
def vad_lab():
    return FileResponse(ROOT / "audio_vad_lab.html", media_type="text/html")


@app.get("/effects", include_in_schema=False)
def effects_lab():
    return FileResponse(ROOT / "effects_lab.html", media_type="text/html")


@app.get("/image-benchmark", include_in_schema=False)
def image_benchmark_lab():
    return FileResponse(ROOT / "image_benchmark.html", media_type="text/html")


@app.get("/music-benchmark", include_in_schema=False)
def music_benchmark_lab():
    return FileResponse(ROOT / "music_benchmark.html", media_type="text/html")


@app.get("/text-benchmark", include_in_schema=False)
def text_benchmark_lab():
    return FileResponse(ROOT / "text_response_benchmark.html", media_type="text/html")


@app.get("/speech-benchmark", include_in_schema=False)
def speech_benchmark_lab():
    return FileResponse(ROOT / "speech_benchmark.html", media_type="text/html")


@app.get("/story-planner", include_in_schema=False)
def story_planner_lab():
    return FileResponse(ROOT / "story_planner_lab.html", media_type="text/html")


@app.get("/a2ui-canvas", include_in_schema=False)
def a2ui_canvas_lab():
    return FileResponse(ROOT / "a2ui_canvas_lab.html", media_type="text/html")


@app.get("/adventure-runner", include_in_schema=False)
def adventure_runner_lab():
    return FileResponse(ROOT / "adventure_runner.html", media_type="text/html")


@app.get("/api/a2ui-canvas/default-config")
def a2ui_canvas_default_config():
    return default_canvas_config().as_dict()


@app.get("/api/a2ui-canvas/images")
def a2ui_canvas_images():
    """List curated local canvas images without exposing arbitrary files."""
    images: list[dict[str, str]] = []
    for image_root in CANVAS_IMAGE_ROOTS:
        if not image_root.is_dir():
            continue
        for image_path in image_root.rglob("*"):
            if not image_path.is_file() or image_path.suffix.lower() not in CANVAS_IMAGE_EXTENSIONS:
                continue
            relative_path = image_path.resolve().relative_to(PROJECT_ROOT.resolve())
            images.append({"path": relative_path.as_posix(), "label": relative_path.as_posix()})
    return {"images": sorted(images, key=lambda image: image["label"].lower())}


@app.get("/api/a2ui-canvas/image")
def a2ui_canvas_image(path: str):
    """Serve an image selected from the canvas preview catalog."""
    try:
        image_path = (PROJECT_ROOT / path).resolve()
        allowed = any(image_path.is_relative_to(root.resolve()) for root in CANVAS_IMAGE_ROOTS)
    except (OSError, ValueError):
        allowed = False
    if not allowed or not image_path.is_file() or image_path.suffix.lower() not in CANVAS_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Canvas image not found.")
    return FileResponse(image_path, media_type=mimetypes.guess_type(image_path.name)[0] or "image/png")


@app.post("/api/a2ui-canvas/runs")
def create_a2ui_canvas_run(body: dict[str, Any]):
    try:
        config = _a2ui_canvas_config_from_body(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_id = uuid.uuid4().hex
    run = {
        "id": run_id,
        "status": "running",
        "created_at": time.time(),
        "config": config.as_dict(),
    }
    with _runs_lock:
        _a2ui_canvas_runs[run_id] = run
    threading.Thread(target=_run_a2ui_canvas_test, args=(run_id, config), daemon=True).start()
    return run


@app.get("/api/a2ui-canvas/runs/{run_id}")
def get_a2ui_canvas_run(run_id: str):
    with _runs_lock:
        run = _a2ui_canvas_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="A2UI Canvas test run not found.")
        return run


@app.post("/api/story-planner/sessions")
def create_story_planner_session(body: dict[str, Any]):
    """Create an isolated in-memory planner session for interactive diagnosis."""
    model = str(body.get("planner_model") or "gemini-3.7-flash").strip()
    if not model:
        raise HTTPException(status_code=400, detail="A planner model is required.")
    nodes_ahead = body.get("nodes_ahead", 3)
    if not isinstance(nodes_ahead, int) or not 1 <= nodes_ahead <= 8:
        raise HTTPException(status_code=400, detail="nodes_ahead must be an integer between 1 and 8.")

    run_id = uuid.uuid4().hex
    run: dict[str, Any] = {"id": run_id, "events": [], "created_at": time.time()}

    def on_scene_reaction(result: dict[str, Any]) -> None:
        with _runs_lock:
            active = _story_planner_runs.get(run_id)
            if active:
                active["events"].append({"time": time.time(), "result": result})

    theater_manager = TheaterManager()
    canvas_state_service = CanvasStateService(theater_manager)
    tools = StoryPlanningTools(
        config={
            "adventure_mode": True,
            "nodes_ahead": nodes_ahead,
            "planner_model": model,
            "on_scene_reaction": on_scene_reaction,
        },
        theater_id=f"testlab_{run_id}",
        canvas_state_service=canvas_state_service,
        theater_manager=theater_manager,
        text_response_provider=get_text_response_provider("gemini-3", options={"model": model}),
    )
    run["tools"] = tools
    with _runs_lock:
        _story_planner_runs[run_id] = run
    return _story_planner_payload(run)


@app.post("/api/story-planner/sessions/{run_id}/actions")
def submit_story_planner_action(run_id: str, body: dict[str, Any]):
    action = str(body.get("action") or "").strip()
    nudge = str(body.get("nudge") or "").strip()
    if not action:
        raise HTTPException(status_code=400, detail="An action is required.")
    with _runs_lock:
        run = _story_planner_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Story planner session not found.")
    acknowledgement = run["tools"].process_user_action(action, nudge=nudge)
    return {"acknowledgement": acknowledgement, "session": _story_planner_payload(run)}


@app.get("/api/story-planner/sessions/{run_id}")
def get_story_planner_session(run_id: str):
    with _runs_lock:
        run = _story_planner_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Story planner session not found.")
        return _story_planner_payload(run)


def _story_planner_payload(run: dict[str, Any]) -> dict[str, Any]:
    tools = run["tools"]
    return {
        "id": run["id"],
        "created_at": run["created_at"],
        "events": list(run["events"]),
        "state": {
            "characters": tools.get_present_characters(),
            "plot_beats": tools.get_plot_beats(),
            "last_scene_reaction": dict(getattr(tools, "_last_scene_reaction", {})),
        },
    }


@app.get("/api/adventure-runner/adventures")
def api_list_adventures():
    """List all available adventure packages found in adventures/ directory."""
    return {"adventures": list_available_adventures()}


@app.post("/api/adventure-runner/sessions")
def api_create_adventure_session(body: dict[str, Any]):
    """Create a new local adventure runner session."""
    adv_id = str(body.get("adventure_id") or "space-funk-odyssey").strip()
    agent_model = body.get("agent_model")
    planner_model = body.get("planner_model")
    nodes_ahead = body.get("nodes_ahead")
    try:
        session = AdventureSession(
            adventure_id_or_path=adv_id,
            agent_model=agent_model,
            planner_model=planner_model,
            nodes_ahead=nodes_ahead,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    with _runs_lock:
        _adventure_runner_sessions[session.session_id] = session

    return {
        "id": session.session_id,
        "adventure_id": session.adventure_id,
        "state": session.get_state(),
        "mock_canvas": session.mock_canvas.as_dict(),
        "tool_logs": list(session.mock_canvas.tool_logs),
        "history": list(session.history),
    }


@app.get("/api/adventure-runner/sessions/{session_id}")
def api_get_adventure_session(session_id: str):
    """Retrieve state, history, and tool activity logs for an active session."""
    with _runs_lock:
        session = _adventure_runner_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Adventure session not found.")
    return {
        "id": session.session_id,
        "adventure_id": session.adventure_id,
        "state": session.get_state(),
        "mock_canvas": session.mock_canvas.as_dict(),
        "tool_logs": list(session.mock_canvas.tool_logs),
        "history": list(session.history),
    }


@app.post("/api/adventure-runner/sessions/{session_id}/messages")
def api_send_adventure_message(session_id: str, body: dict[str, Any]):
    """Submit a player action or message to advance the story in the session."""
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    with _runs_lock:
        session = _adventure_runner_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Adventure session not found.")
    res = session.send_message(message)
    if "error" in res:
        raise HTTPException(status_code=500, detail=res["error"])
    return res


@app.post("/api/adventure-runner/sessions/{session_id}/reset")
def api_reset_adventure_session(session_id: str):
    """Reset the adventure session to its initial state."""
    with _runs_lock:
        session = _adventure_runner_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Adventure session not found.")
    session.reset()
    return {
        "id": session.session_id,
        "adventure_id": session.adventure_id,
        "state": session.get_state(),
        "mock_canvas": session.mock_canvas.as_dict(),
        "tool_logs": list(session.mock_canvas.tool_logs),
        "history": list(session.history),
    }


@app.get("/api/adventure-runner/sessions/{session_id}/lore")
def api_list_session_lore(session_id: str):
    """List all lore documents for the active adventure session."""
    with _runs_lock:
        session = _adventure_runner_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Adventure session not found.")
    documents = session.theater_manager.get_lore_documents(session.session_id)
    return {
        "session_id": session_id,
        "adventure_id": session.adventure_id,
        "documents": documents,
    }


@app.get("/api/adventure-runner/sessions/{session_id}/lore/{doc_path:path}")
def api_get_session_lore_document(session_id: str, doc_path: str):
    """Retrieve full text content of a lore document for the active adventure session."""
    with _runs_lock:
        session = _adventure_runner_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Adventure session not found.")
    try:
        content = session.theater_manager.read_lore_document(session.session_id, doc_path)
        return {
            "document": doc_path,
            "session_id": session_id,
            "adventure_id": session.adventure_id,
            "content": content,
        }
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Lore document '{doc_path}' not found: {exc}")


def _a2ui_canvas_config_from_body(body: dict[str, Any]) -> A2UICanvasTestConfig:
    defaults = default_canvas_config()
    request = str(body.get("request") or defaults.request).strip()
    model = str(body.get("model") or defaults.model).strip()
    if not request or len(request) > 4_000:
        raise ValueError("request must contain 1-4000 characters.")
    if not model or len(model) > 100:
        raise ValueError("model must contain 1-100 characters.")

    image_path = None
    raw_image_path = body.get("image_path")
    if raw_image_path:
        candidate = Path(str(raw_image_path))
        candidate = candidate.resolve() if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
        try:
            candidate.relative_to(PROJECT_ROOT.resolve())
        except ValueError as exc:
            raise ValueError("image_path must stay within the workspace.") from exc
        if not candidate.is_file():
            raise ValueError(f"image_path does not exist: {candidate}")
        image_path = candidate

    expected_surface_count = body.get("expected_surface_count", defaults.expected_surface_count)
    if not isinstance(expected_surface_count, int) or not 1 <= expected_surface_count <= 8:
        raise ValueError("expected_surface_count must be an integer from 1-8.")

    persistent = body.get("expected_persistent", defaults.expected_persistent)
    if persistent is not None and not isinstance(persistent, bool):
        raise ValueError("expected_persistent must be true, false, or null.")
    return A2UICanvasTestConfig(
        name=str(body.get("name") or defaults.name)[:100],
        request=request,
        model=model,
        image_path=image_path,
        expected_surface_count=expected_surface_count,
        expected_persistent=persistent,
    )


def _run_a2ui_canvas_test(run_id: str, config: A2UICanvasTestConfig) -> None:
    try:
        provider = get_text_response_provider("gemini-3", options={"model": config.model})
        result = run_canvas_test(provider, config=config)
        with _runs_lock:
            _a2ui_canvas_runs[run_id].update({
                "status": "completed" if not result["errors"] else "failed",
                "completed_at": time.time(),
                "result": result,
            })
    except Exception as exc:
        with _runs_lock:
            _a2ui_canvas_runs[run_id].update({
                "status": "failed",
                "completed_at": time.time(),
                "error": str(exc),
            })


@app.get("/api/image-benchmark/catalog")
def benchmark_catalog():
    return {"prompts": prompt_catalog(), "providers": list_image_provider_specs()}


@app.get("/api/music-benchmark/catalog")
def music_benchmark_catalog():
    return {
        "prompts": music_prompt_catalog(),
        "providers": list_music_provider_specs(),
        "adapters": list_music_adapter_specs(),
    }


@app.get("/api/text-benchmark/catalog")
def text_benchmark_catalog():
    return {"prompts": text_prompt_catalog(), "providers": list_text_response_provider_specs()}


@app.get("/api/speech-benchmark/catalog")
def speech_benchmark_catalog():
    return {"prompts": speech_prompt_catalog(), "providers": list_speech_provider_specs()}



@app.post("/api/image-benchmark/runs")
def start_benchmark_run(body: dict[str, Any]):
    provider_ids = body.get("provider_ids") or []
    prompt_ids = body.get("prompt_ids") or []
    repetitions = body.get("repetitions", 1)
    provider_options = body.get("provider_options") or {}
    custom_prompts_input = body.get("custom_prompts") or ([] if body.get("custom_prompt") is None else [body.get("custom_prompt")])

    custom_prompts = []
    for idx, cp in enumerate(custom_prompts_input):
        if isinstance(cp, str) and cp.strip():
            custom_prompts.append(BenchmarkPrompt(
                id=f"custom-image-{idx + 1}" if len(custom_prompts_input) > 1 else "custom",
                title="Custom Prompt" if len(custom_prompts_input) == 1 else f"Custom Prompt {idx + 1}",
                dimension="Manual Prompt",
                prompt=cp.strip(),
            ))
        elif isinstance(cp, dict) and str(cp.get("prompt") or "").strip():
            custom_prompts.append(BenchmarkPrompt(
                id=str(cp.get("id") or (f"custom-image-{idx + 1}" if len(custom_prompts_input) > 1 else "custom")),
                title=str(cp.get("title") or ("Custom Prompt" if len(custom_prompts_input) == 1 else f"Custom Prompt {idx + 1}")),
                dimension=str(cp.get("dimension") or "Manual Prompt"),
                prompt=str(cp["prompt"]).strip(),
                reference_files=tuple(cp.get("reference_files") or ()),
            ))

    if not provider_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider.")
    if not prompt_ids and not custom_prompts:
        raise HTTPException(status_code=400, detail="Select at least one benchmark prompt or enter a custom prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_prompt(prompt_id) for prompt_id in prompt_ids] + custom_prompts
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown benchmark prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    prompts_meta = [
        {"id": p.id, "title": p.title, "dimension": p.dimension, "prompt": p.prompt, "reference_count": len(p.reference_files)}
        for p in prompts
    ]
    run = {
        "id": run_id,
        "status": "running",
        "started_at": time.time(),
        "completed_at": None,
        "items": [],
        "events": [],
        "prompts": prompts_meta,
        "total": len(prompts) * len(provider_ids) * repetitions,
        "repetitions": repetitions,
    }
    with _runs_lock:
        _runs[run_id] = run
    threading.Thread(target=_run_benchmark, args=(run_id, provider_ids, prompts, repetitions, provider_options), daemon=True).start()
    return run


@app.post("/api/music-benchmark/runs")
def start_music_benchmark_run(body: dict[str, Any]):
    provider_ids = body.get("provider_ids") or []
    prompt_ids = body.get("prompt_ids") or []
    repetitions = body.get("repetitions", 1)
    provider_options = body.get("provider_options") or {}
    custom_prompts_input = body.get("custom_prompts") or ([] if body.get("custom_prompt") is None else [body.get("custom_prompt")])

    custom_prompts = []
    for idx, cp in enumerate(custom_prompts_input):
        if isinstance(cp, str) and cp.strip():
            custom_prompts.append(BenchmarkMusicPrompt(
                id=f"custom-music-{idx + 1}" if len(custom_prompts_input) > 1 else "custom",
                title="Custom Music" if len(custom_prompts_input) == 1 else f"Custom Music {idx + 1}",
                dimension="Manual Music",
                prompt=cp.strip(),
                duration_seconds=30.0,
            ))
        elif isinstance(cp, dict) and str(cp.get("prompt") or "").strip():
            custom_prompts.append(BenchmarkMusicPrompt(
                id=str(cp.get("id") or (f"custom-music-{idx + 1}" if len(custom_prompts_input) > 1 else "custom")),
                title=str(cp.get("title") or ("Custom Music" if len(custom_prompts_input) == 1 else f"Custom Music {idx + 1}")),
                dimension=str(cp.get("dimension") or "Manual Music"),
                prompt=str(cp["prompt"]).strip(),
                duration_seconds=float(cp.get("duration_seconds", 30.0)),
                tempo=str(cp["tempo"]) if cp.get("tempo") else None,
                genre=str(cp["genre"]) if cp.get("genre") else None,
            ))

    if not provider_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider.")
    if not prompt_ids and not custom_prompts:
        raise HTTPException(status_code=400, detail="Select at least one benchmark music prompt or enter a custom prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_music_prompt(prompt_id) for prompt_id in prompt_ids] + custom_prompts
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown music prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    prompts_meta = [
        {
            "id": p.id,
            "title": p.title,
            "dimension": p.dimension,
            "prompt": p.prompt,
            "duration_seconds": p.duration_seconds,
            "tempo": p.tempo,
            "genre": p.genre,
        }
        for p in prompts
    ]
    run = {
        "id": run_id,
        "status": "running",
        "started_at": time.time(),
        "completed_at": None,
        "items": [],
        "events": [],
        "prompts": prompts_meta,
        "total": len(prompts) * len(provider_ids) * repetitions,
        "repetitions": repetitions,
    }
    with _runs_lock:
        _music_runs[run_id] = run
    threading.Thread(target=_run_music_benchmark, args=(run_id, provider_ids, prompts, repetitions, provider_options), daemon=True).start()
    return run


@app.post("/api/text-benchmark/runs")
def start_text_benchmark_run(body: dict[str, Any]):
    provider_ids = body.get("provider_ids") or []
    prompt_ids = body.get("prompt_ids") or []
    repetitions = body.get("repetitions", 1)
    provider_options = body.get("provider_options") or {}
    custom_prompts_input = body.get("custom_prompts") or ([] if body.get("custom_prompt") is None else [body.get("custom_prompt")])

    custom_prompts = []
    for idx, cp in enumerate(custom_prompts_input):
        if isinstance(cp, str) and cp.strip():
            custom_prompts.append(BenchmarkTextPrompt(
                id=f"custom-text-{idx + 1}" if len(custom_prompts_input) > 1 else "custom",
                title="Custom Text Prompt" if len(custom_prompts_input) == 1 else f"Custom Text Prompt {idx + 1}",
                dimension="Manual Prompt",
                prompt=cp.strip(),
                temperature=0.7,
                max_output_tokens=1000,
            ))
        elif isinstance(cp, dict) and str(cp.get("prompt") or "").strip():
            custom_prompts.append(BenchmarkTextPrompt(
                id=str(cp.get("id") or (f"custom-text-{idx + 1}" if len(custom_prompts_input) > 1 else "custom")),
                title=str(cp.get("title") or ("Custom Text Prompt" if len(custom_prompts_input) == 1 else f"Custom Text Prompt {idx + 1}")),
                dimension=str(cp.get("dimension") or "Manual Prompt"),
                prompt=str(cp["prompt"]).strip(),
                system_instruction=str(cp["system_instruction"]) if cp.get("system_instruction") else None,
                temperature=float(cp.get("temperature", 0.7)) if cp.get("temperature") is not None else 0.7,
                max_output_tokens=int(cp.get("max_output_tokens", 1000)) if cp.get("max_output_tokens") is not None else 1000,
            ))

    if not provider_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider.")
    if not prompt_ids and not custom_prompts:
        raise HTTPException(status_code=400, detail="Select at least one benchmark text prompt or enter a custom prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_text_prompt(prompt_id) for prompt_id in prompt_ids] + custom_prompts
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown text prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    prompts_meta = [
        {
            "id": p.id,
            "title": p.title,
            "dimension": p.dimension,
            "prompt": p.prompt,
            "system_instruction": p.system_instruction,
            "temperature": p.temperature,
            "max_output_tokens": p.max_output_tokens,
        }
        for p in prompts
    ]
    run = {
        "id": run_id,
        "status": "running",
        "started_at": time.time(),
        "completed_at": None,
        "items": [],
        "prompts": prompts_meta,
        "total": len(prompts) * len(provider_ids) * repetitions,
        "repetitions": repetitions,
    }
    with _runs_lock:
        _text_runs[run_id] = run
    threading.Thread(target=_run_text_benchmark, args=(run_id, provider_ids, prompts, repetitions, provider_options), daemon=True).start()
    return run


@app.post("/api/speech-benchmark/runs")
def start_speech_benchmark_run(body: dict[str, Any]):
    provider_ids = body.get("provider_ids") or []
    prompt_ids = body.get("prompt_ids") or []
    repetitions = body.get("repetitions", 1)
    provider_options = body.get("provider_options") or {}
    custom_prompts_input = body.get("custom_prompts") or ([] if body.get("custom_prompt") is None else [body.get("custom_prompt")])

    custom_prompts = []
    for idx, cp in enumerate(custom_prompts_input):
        if isinstance(cp, str) and cp.strip():
            custom_prompts.append(BenchmarkSpeechPrompt(
                id=f"custom-speech-{idx + 1}" if len(custom_prompts_input) > 1 else "custom",
                title="Custom Dialogue" if len(custom_prompts_input) == 1 else f"Custom Dialogue {idx + 1}",
                dimension="Manual Dialogue",
                text=cp.strip(),
                voice_instruction="",
            ))
        elif isinstance(cp, dict) and (str(cp.get("text") or cp.get("prompt") or "").strip()):
            text_val = str(cp.get("text") or cp.get("prompt")).strip()
            custom_prompts.append(BenchmarkSpeechPrompt(
                id=str(cp.get("id") or (f"custom-speech-{idx + 1}" if len(custom_prompts_input) > 1 else "custom")),
                title=str(cp.get("title") or ("Custom Dialogue" if len(custom_prompts_input) == 1 else f"Custom Dialogue {idx + 1}")),
                dimension=str(cp.get("dimension") or "Manual Dialogue"),
                text=text_val,
                voice_instruction=str(cp.get("voice_instruction") or ""),
            ))

    if not provider_ids:
        raise HTTPException(status_code=400, detail="Select at least one speech provider.")
    if not prompt_ids and not custom_prompts:
        raise HTTPException(status_code=400, detail="Select at least one dialogue line or enter a custom dialogue prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_speech_prompt(prompt_id) for prompt_id in prompt_ids] + custom_prompts
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown speech prompt: {exc.args[0]}") from exc
    run_id = uuid.uuid4().hex
    prompts_meta = [
        {
            "id": p.id,
            "title": p.title,
            "dimension": p.dimension,
            "text": p.text,
            "voice_instruction": p.voice_instruction,
        }
        for p in prompts
    ]
    run = {
        "id": run_id,
        "status": "running",
        "started_at": time.time(),
        "completed_at": None,
        "items": [],
        "prompts": prompts_meta,
        "total": len(prompts) * len(provider_ids) * repetitions,
        "repetitions": repetitions,
    }
    with _runs_lock:
        _speech_runs[run_id] = run
    threading.Thread(target=_run_speech_benchmark, args=(run_id, provider_ids, prompts, repetitions, provider_options), daemon=True).start()
    return run


@app.get("/api/image-benchmark/runs/{run_id}")
def benchmark_run(run_id: str):
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        return dict(run)


@app.get("/api/music-benchmark/runs/{run_id}")
def music_benchmark_run(run_id: str):
    with _runs_lock:
        run = _music_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Music benchmark run not found.")
        return dict(run)


@app.get("/api/music-benchmark/runs/{run_id}/events")
def music_benchmark_events(run_id: str):
    """Server-sent live progress for long base-plus-adapter benchmark runs."""
    def event_stream():
        event_index = 0
        while True:
            with _runs_lock:
                run = _music_runs.get(run_id)
                if not run:
                    yield 'event: error\ndata: {"detail":"Music benchmark run not found."}\n\n'
                    return
                events = list(run.get("events", []))
                completed = run["status"] == "completed"
            while event_index < len(events):
                yield f"data: {json.dumps(events[event_index])}\n\n"
                event_index += 1
            if completed:
                yield 'event: complete\ndata: {}\n\n'
                return
            time.sleep(0.2)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/text-benchmark/runs/{run_id}")
def text_benchmark_run(run_id: str):
    with _runs_lock:
        run = _text_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Text benchmark run not found.")
        return dict(run)


@app.get("/api/speech-benchmark/runs/{run_id}")
def speech_benchmark_run(run_id: str):
    with _runs_lock:
        run = _speech_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Speech benchmark run not found.")
        return dict(run)


@app.put("/api/image-benchmark/runs/{run_id}/items/{item_id}/note")
def update_benchmark_note(run_id: str, item_id: str, body: dict[str, Any]):
    note = body.get("note")
    if not isinstance(note, str):
        raise HTTPException(status_code=400, detail="Note must be text.")
    if len(note) > 2_000:
        raise HTTPException(status_code=400, detail="Notes are limited to 2,000 characters.")
    with _runs_lock:
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Benchmark run not found.")
        item = next((candidate for candidate in run["items"] if candidate["id"] == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Benchmark generation not found.")
        item["note"] = note.strip()
        return {"id": item_id, "note": item["note"]}


@app.put("/api/music-benchmark/runs/{run_id}/items/{item_id}/note")
def update_music_benchmark_note(run_id: str, item_id: str, body: dict[str, Any]):
    note = body.get("note")
    if not isinstance(note, str):
        raise HTTPException(status_code=400, detail="Note must be text.")
    if len(note) > 2_000:
        raise HTTPException(status_code=400, detail="Notes are limited to 2,000 characters.")
    with _runs_lock:
        run = _music_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Music benchmark run not found.")
        item = next((candidate for candidate in run["items"] if candidate["id"] == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Music benchmark generation not found.")
        item["note"] = note.strip()
        return {"id": item_id, "note": item["note"]}


@app.put("/api/text-benchmark/runs/{run_id}/items/{item_id}/note")
def update_text_benchmark_note(run_id: str, item_id: str, body: dict[str, Any]):
    note = body.get("note")
    if not isinstance(note, str):
        raise HTTPException(status_code=400, detail="Note must be text.")
    if len(note) > 2_000:
        raise HTTPException(status_code=400, detail="Notes are limited to 2,000 characters.")
    with _runs_lock:
        run = _text_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Text benchmark run not found.")
        item = next((candidate for candidate in run["items"] if candidate["id"] == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="Text benchmark generation not found.")
        item["note"] = note.strip()
        return {"id": item_id, "note": item["note"]}



def _run_benchmark(run_id: str, provider_ids: list[str], prompts: list[Any], repetitions: int, provider_options: dict[str, Any]) -> None:
    # Keep providers parallel, but avoid flooding any one provider API. As each
    # future completes, append it so the polling frontend can render immediately.
    task_count = len(provider_ids) * len(prompts) * repetitions
    # A provider gets no more than five workers; providers still run beside one
    # another, giving a maximum of five in-flight requests per selected provider.
    worker_count = min(task_count, MAX_IN_FLIGHT_PER_PROVIDER * len(provider_ids))
    provider_semaphores = {provider_id: threading.BoundedSemaphore(MAX_IN_FLIGHT_PER_PROVIDER) for provider_id in provider_ids}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="image-benchmark") as executor:
        futures = [
            executor.submit(
                _benchmark_one_limited,
                provider_id,
                prompt,
                repetition,
                provider_options.get(provider_id) or {},
                provider_semaphores[provider_id],
            )
            for repetition in range(1, repetitions + 1)
            for prompt in prompts
            for provider_id in provider_ids
        ]
        for future in as_completed(futures):
            item = future.result()
            with _runs_lock:
                _runs[run_id]["items"].append(item)
    with _runs_lock:
        _runs[run_id]["status"] = "completed"
        _runs[run_id]["completed_at"] = time.time()


def _run_music_benchmark(run_id: str, provider_ids: list[str], prompts: list[Any], repetitions: int, provider_options: dict[str, Any]) -> None:
    task_count = len(provider_ids) * len(prompts) * repetitions
    worker_count = min(task_count, MAX_IN_FLIGHT_PER_PROVIDER * len(provider_ids))
    provider_semaphores = {provider_id: threading.BoundedSemaphore(MAX_IN_FLIGHT_PER_PROVIDER) for provider_id in provider_ids}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="music-benchmark") as executor:
        futures = [
            executor.submit(
                _benchmark_one_music_limited,
                provider_id,
                prompt,
                repetition,
                provider_options.get(provider_id) or {},
                provider_semaphores[provider_id],
                lambda stage, details, provider_id=provider_id, prompt=prompt, repetition=repetition: _record_music_progress(run_id, provider_id, prompt.id, repetition, stage, details),
            )
            for repetition in range(1, repetitions + 1)
            for prompt in prompts
            for provider_id in provider_ids
        ]
        for future in as_completed(futures):
            item = future.result()
            with _runs_lock:
                _music_runs[run_id]["items"].append(item)
    with _runs_lock:
        _music_runs[run_id]["status"] = "completed"
        _music_runs[run_id]["completed_at"] = time.time()


def _record_music_progress(run_id: str, provider_id: str, prompt_id: str, repetition: int, stage: str, details: dict[str, Any]) -> None:
    with _runs_lock:
        run = _music_runs.get(run_id)
        if run:
            run["events"].append({"time": time.time(), "provider_id": provider_id, "prompt_id": prompt_id, "repetition": repetition, "stage": stage, "details": details})


def _benchmark_one_limited(
    provider_id: str,
    prompt: Any,
    repetition: int,
    provider_options: dict[str, Any],
    semaphore: threading.BoundedSemaphore,
) -> dict[str, Any]:
    with semaphore:
        return _benchmark_one(provider_id, prompt, repetition, provider_options)


def _benchmark_one_music_limited(
    provider_id: str,
    prompt: Any,
    repetition: int,
    provider_options: dict[str, Any],
    semaphore: threading.BoundedSemaphore,
    progress_callback: Any = None,
) -> dict[str, Any]:
    with semaphore:
        return _benchmark_one_music(provider_id, prompt, repetition, provider_options, progress_callback)


def _benchmark_one(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        references = []
        for relative_path in prompt.reference_files:
            path = BENCHMARK_ROOT / relative_path
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            references.append(ImageReference(name=path.name, data=path.read_bytes(), mime_type=mime_type))
        result = get_image_provider(provider_id, provider_options).generate(
            ImageGenerationRequest(prompt=prompt.prompt, references=references, aspect_ratio="16:9")
        )
        extension = mimetypes.guess_extension(result.mime_type) or ".png"
        filename = f"{item['id']}{extension}"
        (BENCHMARK_OUTPUT / filename).write_bytes(result.image_bytes)
        output_megapixels = _image_megapixels(result.image_bytes)
        item.update(
            {
                "status": "completed",
                "image_url": f"/benchmark-images/{filename}",
                "model": result.model,
                "request_id": result.request_id,
                "usage": dict(result.usage),
                "output_megapixels": output_megapixels,
                "estimated_output_cost_usd": _estimated_output_cost(provider_id, output_megapixels),
            }
        )
    except (ImageProviderError, OSError, ValueError) as exc:
        item["error"] = str(exc)
    except Exception as exc:  # Diagnostics must retain unexpected provider errors too.
        item["error"] = f"Unexpected error: {exc}"
    finally:
        item["completed_at"] = time.time()
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return item


def _benchmark_one_music(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None, progress_callback: Any = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        if progress_callback:
            progress_callback("submitted", {})
        req = MusicGenerationRequest(
            prompt=prompt.prompt,
            duration_seconds=prompt.duration_seconds,
            tempo=prompt.tempo,
            genre=prompt.genre,
            on_progress=progress_callback,
        )
        result = get_music_provider(provider_id, provider_options).generate(req)
        extension = mimetypes.guess_extension(result.mime_type) or ".mp3"
        filename = f"{item['id']}{extension}"
        (BENCHMARK_MUSIC_OUTPUT / filename).write_bytes(result.audio_bytes)
        base_artifact = result.artifacts.get("base")
        base_audio_url = None
        base_model = None
        if base_artifact:
            base_extension = mimetypes.guess_extension(base_artifact.mime_type) or ".mp3"
            base_filename = f"{item['id']}_base{base_extension}"
            (BENCHMARK_MUSIC_OUTPUT / base_filename).write_bytes(base_artifact.audio_bytes)
            base_audio_url = f"/benchmark-audio/{base_filename}"
            base_model = base_artifact.model
        item.update(
            {
                "status": "completed",
                "audio_url": f"/benchmark-audio/{filename}",
                "base_audio_url": base_audio_url,
                "base_model": base_model,
                "base_latency_ms": result.usage.get("timings", {}).get("base_latency_ms"),
                "adapter_latency_ms": result.usage.get("timings", {}).get("adapter_latency_ms"),
                "model": result.model,
                "request_id": result.request_id,
                "usage": dict(result.usage),
                "estimated_output_cost_usd": _estimated_music_output_cost(provider_id, prompt.duration_seconds, provider_options),
            }
        )
    except (MusicProviderError, OSError, ValueError) as exc:
        item["error"] = str(exc)
    except Exception as exc:
        item["error"] = f"Unexpected error: {exc}"
    finally:
        item["completed_at"] = time.time()
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        if progress_callback:
            progress_callback("completed" if item["status"] == "completed" else "failed", {"latency_ms": item["latency_ms"], "error": item.get("error")})
    return item


def _image_megapixels(image_bytes: bytes) -> float | None:
    """Return generated-image size for fair per-megapixel cost estimates."""
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
        return round(width * height / 1_000_000, 4)
    except (OSError, ValueError):
        return None


def _estimated_output_cost(provider_id: str, megapixels: float | None) -> float | None:
    if megapixels is None:
        return None
    spec = next((spec for spec in list_image_provider_specs() if spec["id"] == provider_id), None)
    rate = spec.get("estimated_cost_usd_1mp") if spec else None
    return round(megapixels * rate, 6) if rate is not None else None


def _estimated_music_output_cost(
    provider_id: str,
    duration_seconds: float,
    provider_options: dict[str, Any] | None = None,
) -> float | None:
    provider_options = provider_options or {}
    if provider_id == "test-base-plus-adapter":
        base_cost = _estimated_music_output_cost(
            str(provider_options.get("base_provider") or "lyria"),
            duration_seconds,
            dict(provider_options.get("base_options") or {}),
        )
        adapter_id = str(provider_options.get("adapter") or "fal-stable-audio-3-base-a2a")
        adapter = next((item for item in list_music_adapter_specs() if item["id"] == adapter_id), None)
        adapter_cost = adapter.get("estimated_cost_usd_per_generation") if adapter else None
        if base_cost is None or adapter_cost is None:
            return None
        return round(base_cost + adapter_cost, 6)
    spec = next((spec for spec in list_music_provider_specs() if spec["id"] == provider_id), None)
    if not spec:
        return None
    if "estimated_cost_usd_per_generation" in spec:
        return spec["estimated_cost_usd_per_generation"]
    rate_30s = spec.get("estimated_cost_usd_30s")
    return round((duration_seconds / 30.0) * rate_30s, 6) if rate_30s is not None else None


def _run_speech_benchmark(run_id: str, provider_ids: list[str], prompts: list[Any], repetitions: int, provider_options: dict[str, Any]) -> None:
    task_count = len(provider_ids) * len(prompts) * repetitions
    worker_count = min(task_count, MAX_IN_FLIGHT_PER_PROVIDER * len(provider_ids))
    provider_semaphores = {provider_id: threading.BoundedSemaphore(MAX_IN_FLIGHT_PER_PROVIDER) for provider_id in provider_ids}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="speech-benchmark") as executor:
        futures = [
            executor.submit(_benchmark_one_speech_limited, provider_id, prompt, repetition, provider_options.get(provider_id) or {}, provider_semaphores[provider_id])
            for repetition in range(1, repetitions + 1)
            for prompt in prompts
            for provider_id in provider_ids
        ]
        for future in as_completed(futures):
            item = future.result()
            with _runs_lock:
                _speech_runs[run_id]["items"].append(item)
    with _runs_lock:
        _speech_runs[run_id]["status"] = "completed"
        _speech_runs[run_id]["completed_at"] = time.time()


def _benchmark_one_speech_limited(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any], semaphore: threading.BoundedSemaphore) -> dict[str, Any]:
    with semaphore:
        return _benchmark_one_speech(provider_id, prompt, repetition, provider_options)


def _benchmark_one_speech(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        options = provider_options or {}
        result = get_speech_provider(provider_id, options).synthesize(SpeechSynthesisRequest(
            text=prompt.text,
            voice=str(options["voice"]) if options.get("voice") else None,
            voice_instruction=str(options.get("voice_instruction") or prompt.voice_instruction),
            speed=float(options["speed"]) if options.get("speed") is not None else None,
            sample_rate_hz=int(options["sample_rate_hz"]) if options.get("sample_rate_hz") else None,
        ))
        extension = mimetypes.guess_extension(result.mime_type) or ".mp3"
        filename = f"{item['id']}{extension}"
        (BENCHMARK_SPEECH_OUTPUT / filename).write_bytes(result.audio_bytes)
        item.update({
            "status": "completed", "audio_url": f"/benchmark-speech/{filename}", "model": result.model,
            "request_id": result.request_id, "usage": dict(result.usage),
            "estimated_output_cost_usd": _estimated_speech_output_cost(provider_id, prompt.text),
        })
    except (SpeechProviderError, OSError, ValueError) as exc:
        item["error"] = str(exc)
    except Exception as exc:
        item["error"] = f"Unexpected error: {exc}"
    finally:
        item["completed_at"] = time.time()
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return item


def _estimated_speech_output_cost(provider_id: str, text: str) -> float | None:
    spec = next((item for item in list_speech_provider_specs() if item["id"] == provider_id), None)
    rate = spec.get("estimated_cost_usd_1k_chars") if spec else None
    return round(len(text) / 1_000 * rate, 6) if rate is not None else None


def _run_text_benchmark(run_id: str, provider_ids: list[str], prompts: list[Any], repetitions: int, provider_options: dict[str, Any]) -> None:
    task_count = len(provider_ids) * len(prompts) * repetitions
    worker_count = min(task_count, MAX_IN_FLIGHT_PER_PROVIDER * len(provider_ids))
    provider_semaphores = {provider_id: threading.BoundedSemaphore(MAX_IN_FLIGHT_PER_PROVIDER) for provider_id in provider_ids}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="text-benchmark") as executor:
        futures = [
            executor.submit(
                _benchmark_one_text_limited,
                provider_id,
                prompt,
                repetition,
                provider_options.get(provider_id) or {},
                provider_semaphores[provider_id],
            )
            for repetition in range(1, repetitions + 1)
            for prompt in prompts
            for provider_id in provider_ids
        ]
        for future in as_completed(futures):
            item = future.result()
            with _runs_lock:
                _text_runs[run_id]["items"].append(item)
    with _runs_lock:
        _text_runs[run_id]["status"] = "completed"
        _text_runs[run_id]["completed_at"] = time.time()


def _benchmark_one_text_limited(
    provider_id: str,
    prompt: Any,
    repetition: int,
    provider_options: dict[str, Any],
    semaphore: threading.BoundedSemaphore,
) -> dict[str, Any]:
    with semaphore:
        return _benchmark_one_text(provider_id, prompt, repetition, provider_options)


def _benchmark_one_text(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        req = TextResponseRequest(
            prompt=prompt.prompt,
            system_instruction=prompt.system_instruction,
            temperature=prompt.temperature,
            max_output_tokens=prompt.max_output_tokens,
        )
        result = get_text_response_provider(provider_id, provider_options).generate(req)
        item.update(
            {
                "status": "completed",
                "text": result.text,
                "model": result.model,
                "request_id": result.request_id,
                "finish_reason": result.finish_reason,
                "usage": dict(result.usage),
            }
        )
    except (TextResponseProviderError, OSError, ValueError) as exc:
        item["error"] = str(exc)
    except Exception as exc:
        item["error"] = f"Unexpected error: {exc}"
    finally:
        item["completed_at"] = time.time()
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return item


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Narratron Test Lab Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    print(f"Starting Narratron Test Lab on http://{args.host}:{args.port}")
    uvicorn.run("testlab.server:app", host=args.host, port=args.port, reload=args.reload)



