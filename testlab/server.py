"""Standalone development server for the browser-based Narratron test labs."""

import mimetypes
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from providers import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageReference,
    MusicGenerationRequest,
    MusicProviderError,
    TextResponseRequest,
    TextResponseProviderError,
    get_image_provider,
    get_music_provider,
    get_text_response_provider,
    list_image_provider_specs,
    list_music_provider_specs,
    list_text_response_provider_specs,
)
from testlab.image_benchmark import ROOT as BENCHMARK_ROOT, get_prompt, prompt_catalog
from testlab.music_benchmark import get_music_prompt, music_prompt_catalog
from testlab.text_response_benchmark import get_text_prompt, text_prompt_catalog

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

_runs: dict[str, dict[str, Any]] = {}
_music_runs: dict[str, dict[str, Any]] = {}
_text_runs: dict[str, dict[str, Any]] = {}
_runs_lock = threading.Lock()

MAX_IN_FLIGHT_PER_PROVIDER = 5


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/vad")


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


@app.get("/api/image-benchmark/catalog")
def benchmark_catalog():
    return {"prompts": prompt_catalog(), "providers": list_image_provider_specs()}


@app.get("/api/music-benchmark/catalog")
def music_benchmark_catalog():
    return {"prompts": music_prompt_catalog(), "providers": list_music_provider_specs()}


@app.get("/api/text-benchmark/catalog")
def text_benchmark_catalog():
    return {"prompts": text_prompt_catalog(), "providers": list_text_response_provider_specs()}



@app.post("/api/image-benchmark/runs")
def start_benchmark_run(body: dict[str, Any]):
    provider_ids = body.get("provider_ids") or []
    prompt_ids = body.get("prompt_ids") or []
    repetitions = body.get("repetitions", 1)
    provider_options = body.get("provider_options") or {}
    if not provider_ids or not prompt_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider and one benchmark prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_prompt(prompt_id) for prompt_id in prompt_ids]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown benchmark prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    run = {"id": run_id, "status": "running", "started_at": time.time(), "completed_at": None, "items": [], "total": len(prompts) * len(provider_ids) * repetitions, "repetitions": repetitions}
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
    if not provider_ids or not prompt_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider and one benchmark music prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_music_prompt(prompt_id) for prompt_id in prompt_ids]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown music prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    run = {"id": run_id, "status": "running", "started_at": time.time(), "completed_at": None, "items": [], "total": len(prompts) * len(provider_ids) * repetitions, "repetitions": repetitions}
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
    if not provider_ids or not prompt_ids:
        raise HTTPException(status_code=400, detail="Select at least one provider and one benchmark text prompt.")
    if not isinstance(repetitions, int) or not 1 <= repetitions <= 20:
        raise HTTPException(status_code=400, detail="Repetitions must be a whole number between 1 and 20.")
    try:
        prompts = [get_text_prompt(prompt_id) for prompt_id in prompt_ids]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown text prompt: {exc.args[0]}") from exc

    run_id = uuid.uuid4().hex
    run = {"id": run_id, "status": "running", "started_at": time.time(), "completed_at": None, "items": [], "total": len(prompts) * len(provider_ids) * repetitions, "repetitions": repetitions}
    with _runs_lock:
        _text_runs[run_id] = run
    threading.Thread(target=_run_text_benchmark, args=(run_id, provider_ids, prompts, repetitions, provider_options), daemon=True).start()
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


@app.get("/api/text-benchmark/runs/{run_id}")
def text_benchmark_run(run_id: str):
    with _runs_lock:
        run = _text_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Text benchmark run not found.")
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
) -> dict[str, Any]:
    with semaphore:
        return _benchmark_one_music(provider_id, prompt, repetition, provider_options)


def _benchmark_one(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        references = []
        for relative_path in prompt.reference_files:
            path = BENCHMARK_ROOT / relative_path
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            references.append(ImageReference(name=path.name, data=path.read_bytes(), mime_type=mime_type))
        result = get_image_provider(provider_id, provider_options).generate(ImageGenerationRequest(prompt=prompt.prompt, references=references))
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


def _benchmark_one_music(provider_id: str, prompt: Any, repetition: int, provider_options: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    item: dict[str, Any] = {"id": uuid.uuid4().hex, "provider_id": provider_id, "prompt_id": prompt.id, "repetition": repetition, "started_at": time.time(), "status": "failed"}
    try:
        req = MusicGenerationRequest(
            prompt=prompt.prompt,
            duration_seconds=prompt.duration_seconds,
            tempo=prompt.tempo,
            genre=prompt.genre,
        )
        result = get_music_provider(provider_id, provider_options).generate(req)
        extension = mimetypes.guess_extension(result.mime_type) or ".mp3"
        filename = f"{item['id']}{extension}"
        (BENCHMARK_MUSIC_OUTPUT / filename).write_bytes(result.audio_bytes)
        item.update(
            {
                "status": "completed",
                "audio_url": f"/benchmark-audio/{filename}",
                "model": result.model,
                "request_id": result.request_id,
                "usage": dict(result.usage),
                "estimated_output_cost_usd": _estimated_music_output_cost(provider_id, prompt.duration_seconds),
            }
        )
    except (MusicProviderError, OSError, ValueError) as exc:
        item["error"] = str(exc)
    except Exception as exc:
        item["error"] = f"Unexpected error: {exc}"
    finally:
        item["completed_at"] = time.time()
        item["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
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


def _estimated_music_output_cost(provider_id: str, duration_seconds: float) -> float | None:
    spec = next((spec for spec in list_music_provider_specs() if spec["id"] == provider_id), None)
    if not spec:
        return None
    if "estimated_cost_usd_per_generation" in spec:
        return spec["estimated_cost_usd_per_generation"]
    rate_30s = spec.get("estimated_cost_usd_30s")
    return round((duration_seconds / 30.0) * rate_30s, 6) if rate_30s is not None else None


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


