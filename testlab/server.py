"""Standalone development server for the browser-based Narratron test labs."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

app = FastAPI(title="Narratron Test Lab")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/effects-static", StaticFiles(directory=PROJECT_ROOT / "static"), name="effects-static")
app.mount("/carousel", StaticFiles(directory=PROJECT_ROOT / "templates" / "carousel"), name="carousel")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/vad")


@app.get("/vad", include_in_schema=False)
def vad_lab():
    return FileResponse(ROOT / "audio_vad_lab.html", media_type="text/html")


@app.get("/effects", include_in_schema=False)
def effects_lab():
    return FileResponse(ROOT / "effects_lab.html", media_type="text/html")
