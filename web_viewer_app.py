import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from components.canvas_state import CanvasStateManager
from deployer.deployer import LocalDeployer, SessionMetadata
from deployer.session_manager import SessionManager
from utils.config_loader import get_config

logger = logging.getLogger(__name__)

config = get_config()

app = FastAPI()

# Deployer and Session Manager instances
local_deployer = LocalDeployer()
session_manager = SessionManager(deployer=local_deployer)

# Default folder from config (absolute path resolution)
folder = str((Path(__file__).parent / config.get("image_generation", {}).get("output_folder", "output/images")).resolve())
os.makedirs(folder, exist_ok=True)
app.mount("/images", StaticFiles(directory=folder), name="images")

# Playlists folder from config (absolute path resolution)
playlists_folder = str((Path(__file__).parent / config.get("music", {}).get("playlists_folder", "playlists")).resolve())
os.makedirs(playlists_folder, exist_ok=True)
app.mount("/playlists", StaticFiles(directory=playlists_folder), name="playlists")

# Reference library folder (absolute path resolution)
ref_library_folder = str((Path(__file__).parent / "reference_library").resolve())
os.makedirs(ref_library_folder, exist_ok=True)
app.mount("/reference_library", StaticFiles(directory=ref_library_folder), name="reference_library")

class ChatMessage(BaseModel):
    author: str
    text: str

canvas_state = CanvasStateManager(chat_output_dir="output/chats")

def update_current_playlist(playlist_name: str, tracks: list[str]):
    canvas_state.update_current_playlist(playlist_name, tracks)

def pause_current_playlist():
    canvas_state.pause_current_playlist()

def resume_current_playlist():
    canvas_state.resume_current_playlist()

def update_shown_image(file_path: str):
    canvas_state.update_shown_image(file_path)

def add_chat_message(text: str, author: str = "agent"):
    canvas_state.add_chat_message(text, author=author)

# ========================================
# Session Asset Dynamic Routes
# ========================================

@app.get("/sessions/{session_id}/references/{filename}")
async def serve_session_reference(session_id: str, filename: str):
    file_path = session_manager.get_session_reference_dir(session_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Session reference file not found")
    return FileResponse(file_path)

@app.get("/sessions/{session_id}/playlists/{playlist_name}/{filename}")
async def serve_session_playlist_track(session_id: str, playlist_name: str, filename: str):
    file_path = session_manager.get_session_playlists_dir(session_id) / playlist_name / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Session playlist track not found")
    return FileResponse(file_path)

@app.get("/sessions/{session_id}/output/{filename}")
async def serve_session_output(session_id: str, filename: str):
    file_path = session_manager.get_session_output_dir(session_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Session output file not found")
    return FileResponse(file_path)

# ========================================
# Deployer & Session API Endpoints
# ========================================

@app.get("/api/sessions")
def list_sessions():
    """List all deployed or created session instances."""
    return local_deployer.list_sessions()

@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    """Retrieve metadata and mounted assets for a specific session."""
    meta = local_deployer.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "metadata": meta,
        "references": session_manager.get_session_references(session_id),
        "playlists": session_manager.get_session_playlists(session_id),
    }

@app.post("/api/sessions/create-and-deploy")
async def create_and_deploy_session(request: Request):
    """API endpoint to handle multi-file asset upload and deploy a session canvas instance."""
    form = await request.form()
    name = str(form.get("name", "Narratron Session"))

    reference_files = []
    playlists_data = {}

    for key, value in form.multi_items():
        filename = getattr(value, "filename", None)
        if filename:
            content = await value.read()
            if content:
                if key == "reference_files":
                    reference_files.append((filename, content))
                elif key.startswith("playlist_"):
                    pl_name = key[len("playlist_"):]
                    if pl_name not in playlists_data:
                        playlists_data[pl_name] = []
                    playlists_data[pl_name].append((filename, content))

    metadata = local_deployer.create_session(
        name=name,
        reference_files=reference_files,
        playlists_data=playlists_data,
    )
    deployed_meta = local_deployer.deploy_session(metadata.session_id)
    return {"status": "ok", "session_id": deployed_meta.session_id, "session": deployed_meta}

@app.post("/api/sessions/{session_id}/deploy")
def deploy_existing_session(session_id: str):
    """Deploy an existing created session."""
    meta = local_deployer.deploy_session(session_id)
    return {"status": "ok", "session": meta}

@app.delete("/api/sessions/{session_id}")
def destroy_session(session_id: str):
    """Remove and clean up a local session instance."""
    success = local_deployer.destroy_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or could not be removed")
    return {"status": "ok", "session_id": session_id}

# ========================================
# Canvas & WebSocket Endpoints
# ========================================

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    canvas_state.register_websocket(websocket)
    
    # Send existing doodle actions to newly connected client
    for action in canvas_state.doodles_state:
        await websocket.send_json(action)
        
    try:
        while True:
            data = await websocket.receive_json()
            canvas_state.add_doodle(data)
            await canvas_state.broadcast_ws_message(data, sender=websocket)
    except WebSocketDisconnect:
        canvas_state.unregister_websocket(websocket)

@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle():
    count = 0
    for ws in list(canvas_state.active_ws_connections):
        try:
            await ws.send_json({"type": "toggle_mic"})
            count += 1
        except Exception:
            pass
    return {"status": "ok", "broadcasted_to": count}

@app.get("/api/orator/config")
def get_orator_config():
    return config.get("orator", {
        "hotkey": "<ctrl>+<shift>+[",
        "server_url": "http://127.0.0.1:8000/api/orator/toggle_mic"
    })

@app.get("/api/latest")
def get_latest_image(session_id: Optional[str] = None):
    if session_id:
        img_folder = str(session_manager.get_session_output_dir(session_id))
    else:
        img_folder = folder
    return canvas_state.get_latest_state(image_folder=img_folder, session_id=session_id)

@app.get("/api/chat")
def get_chat():
    return canvas_state.chat_manager.get_messages()

@app.post("/api/chat")
def post_chat(msg: ChatMessage):
    canvas_state.add_chat_message(msg.text, author=msg.author)
    return {"status": "ok"}

# ========================================
# Application Root Pages
# ========================================

@app.get("/", response_class=HTMLResponse)
def read_root():
    """Serve the Session Creation & App Deployer Screen."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "session_creation.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/canvas", response_class=HTMLResponse)
def read_canvas(session_id: Optional[str] = None):
    """Serve the Canvas interface for a specific session."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        
    os.makedirs(folder, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)

