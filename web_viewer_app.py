import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from components.canvas_state import CanvasStateManager
from deployer.database import DatabaseManager
from deployer.deployer import LocalDeployer, SessionMetadata
from deployer.session_manager import SessionManager
from utils.config_loader import get_config

logger = logging.getLogger(__name__)

config = get_config()

app = FastAPI()

# Deployer, Database, and Session Manager instances
local_deployer = LocalDeployer()
session_manager = SessionManager(deployer=local_deployer)
db = DatabaseManager()

# Sessions folder (absolute path resolution)
sessions_folder = str((Path(__file__).parent / "sessions").resolve())
os.makedirs(sessions_folder, exist_ok=True)

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

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    username_or_email: str
    password: str

class ResolveJoinKeyRequest(BaseModel):
    join_key: str

canvas_state = CanvasStateManager(chat_output_dir="output/chats")

def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("auth_token")
    if not token:
        return None
    return db.validate_session_token(token)

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
# Authentication API Endpoints
# ========================================

@app.post("/api/auth/register")
def register_user(req: RegisterRequest, response: Response):
    try:
        user = db.register_user(req.username, req.email, req.password)
        token = db.create_auth_session(user["id"])
        response.set_cookie(key="auth_token", value=token, httponly=True, max_age=604800)
        return {"status": "ok", "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
def login_user(req: LoginRequest, response: Response):
    user = db.authenticate_user(req.username_or_email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username/email or password.")
    token = db.create_auth_session(user["id"])
    response.set_cookie(key="auth_token", value=token, httponly=True, max_age=604800)
    return {"status": "ok", "user": user}

@app.post("/api/auth/logout")
def logout_user(request: Request, response: Response):
    token = request.cookies.get("auth_token")
    if token:
        db.invalidate_session_token(token)
    response.delete_cookie("auth_token")
    return {"status": "ok"}

@app.get("/api/auth/me")
def get_auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}

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

@app.post("/api/sessions/resolve-join-key")
def resolve_join_key(req: ResolveJoinKeyRequest):
    dep = db.get_session_by_join_key(req.join_key)
    if not dep:
        raise HTTPException(status_code=404, detail="Invalid Join Key. No matching active session found.")
    meta = local_deployer.get_session(dep["session_id"])
    if not meta:
        raise HTTPException(status_code=404, detail="Session files no longer exist.")
    return {"status": "ok", "session_id": meta.session_id, "name": meta.name}

@app.get("/api/sessions")
def list_sessions(request: Request):
    """List all deployed sessions. Hide join_key for non-owners, prioritize user's sessions."""
    current_user = get_current_user(request)
    current_user_id = current_user["id"] if current_user else None

    sessions = local_deployer.list_sessions()
    result = []

    for s in sessions:
        dep = db.get_deployment(s.session_id)
        owner_id = dep["user_id"] if dep else None
        is_owner = (current_user_id is not None and owner_id == current_user_id)

        s_dict = s.model_dump()
        s_dict["is_owner"] = is_owner
        
        # Hide join_key if not owner
        if not is_owner:
            s_dict["join_key"] = "🔒 Owner Only"
            
        result.append(s_dict)

    # Sort: owned sessions first, then by created_at desc
    result.sort(key=lambda x: (not x["is_owner"], x.get("created_at", "")), reverse=False)
    return result

@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    """Retrieve metadata and mounted assets for a specific session."""
    meta = local_deployer.get_session(session_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Session not found")
    
    current_user = get_current_user(request)
    dep = db.get_deployment(session_id)
    owner_id = dep["user_id"] if dep else None
    is_owner = (current_user is not None and owner_id == current_user["id"])

    meta_dict = meta.model_dump()
    meta_dict["is_owner"] = is_owner
    if not is_owner:
        meta_dict["join_key"] = "🔒 Owner Only"

    return {
        "metadata": meta_dict,
        "references": session_manager.get_session_references(session_id),
        "playlists": session_manager.get_session_playlists(session_id),
    }

@app.post("/api/sessions/create-and-deploy")
async def create_and_deploy_session(request: Request):
    """API endpoint to handle multi-file asset upload and deploy a session canvas instance."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to deploy sessions.")

    if user["credits"] < 5.0:
        raise HTTPException(status_code=402, detail="Insufficient credits (5.0 credits required).")

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

    # Record deployment & deduct credits
    db.record_deployment(deployed_meta.session_id, user["id"], deployed_meta.join_key, cost=5.0)

    res_dict = deployed_meta.model_dump()
    res_dict["is_owner"] = True

    return {"status": "ok", "session_id": deployed_meta.session_id, "session": res_dict}

@app.post("/api/sessions/{session_id}/deploy")
def deploy_existing_session(session_id: str, request: Request):
    """Deploy an existing created session."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    
    dep = db.get_deployment(session_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the session owner can deploy this session.")

    meta = local_deployer.deploy_session(session_id)
    return {"status": "ok", "session": meta}

@app.delete("/api/sessions/{session_id}")
def destroy_session(session_id: str, request: Request):
    """Remove and clean up a local session instance. Requires owner login."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to delete sessions.")

    dep = db.get_deployment(session_id)
    if dep and dep["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Permission denied. Only the session owner can delete this session.")

    success = local_deployer.destroy_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or could not be removed")
    return {"status": "ok", "session_id": session_id}

# ========================================
# Canvas & WebSocket Endpoints
# ========================================

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket, session_id: Optional[str] = None):
    await websocket.accept()
    websocket.state.session_id = session_id
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

@app.post("/api/sessions/{session_id}/save")
def save_session_to_db(session_id: str, request: Request):
    """Save canvas session state and image assets to SQLite database on user demand."""
    session_dir = local_deployer._get_session_dir(session_id)
    meta = local_deployer.get_session(session_id)
    dep = db.get_deployment(session_id)
    user_id = dep["user_id"] if dep else None
    name = meta.name if meta else session_id

    state_data, image_files = canvas_state.export_session_data(session_dir=session_dir)
    db.export_session_to_db(
        session_id=session_id,
        state_data=state_data,
        image_files=image_files,
        user_id=user_id,
        name=name
    )
    logger.info(f"Session '{session_id}' saved to database on user demand.")
    return {"status": "ok", "session_id": session_id}

@app.get("/api/sessions/{session_id}/export-assets")
def export_session_assets(session_id: str, request: Request):
    """Package and export all generated and reference image assets for a session into a ZIP file."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    session_dir = local_deployer._get_session_dir(session_id)
    if not session_dir.exists():
        db.reconstruct_session_from_db(session_id, session_dir)

    import io
    import zipfile
    from fastapi.responses import Response

    zip_buffer = io.BytesIO()
    has_output_files = False

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # 1. Add files from session directory recursively
        if session_dir.exists():
            out_dir = session_dir / "output"
            ref_dir = session_dir / "reference_library"

            if out_dir.exists():
                for file_path in out_dir.rglob("*"):
                    if file_path.is_file() and file_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                        arc_name = f"output/{file_path.relative_to(out_dir)}"
                        zip_file.write(file_path, arcname=arc_name.replace("\\", "/"))
                        has_output_files = True

            if ref_dir.exists():
                for file_path in ref_dir.rglob("*"):
                    if file_path.is_file() and file_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                        arc_name = f"reference_library/{file_path.relative_to(ref_dir)}"
                        zip_file.write(file_path, arcname=arc_name.replace("\\", "/"))

        # 2. Add images stored in SQLite database export if present
        db_exp = db.get_exported_session(session_id)
        if db_exp and "images" in db_exp:
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT filename, category, image_data FROM exported_session_images WHERE session_id = ?", (session_id,))
                for row in cursor.fetchall():
                    cat = row["category"]
                    fn = row["filename"]
                    arc_path = f"{'reference_library' if cat == 'reference' else 'output'}/{fn}"
        # 3. Add all historical shown images from canvas state
        for img_path in canvas_state.shown_images_history:
            if img_path and os.path.exists(img_path):
                fn = os.path.basename(img_path)
                arc_path = f"output/{fn}"
                if arc_path not in zip_file.namelist():
                    zip_file.write(img_path, arcname=arc_path)

    zip_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="{session_id}_assets.zip"'
    }
    return Response(content=zip_buffer.getvalue(), media_type="application/zip", headers=headers)

@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle(request: Request, session_id: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to control Orator microphone.")
    
    if session_id:
        dep = db.get_deployment(session_id)
        if dep and dep["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied. Only the session owner can control the Orator microphone.")

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
        session_dir = local_deployer._get_session_dir(session_id)
        if not session_dir.exists():
            db.reconstruct_session_from_db(session_id, session_dir)
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
# Application Root Pages & Navigation
# ========================================

@app.get("/", response_class=HTMLResponse)
@app.get("/join", response_class=HTMLResponse)
def read_join_splash():
    """Serve the public Join Splash Page."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "join_splash.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/deploy", response_class=HTMLResponse)
def read_deployer():
    """Serve the Session Creation & App Deployer Dashboard."""
    template_path = os.path.join(os.path.dirname(__file__), "templates", "session_creation.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/canvas", response_class=HTMLResponse)
def read_canvas(session_id: Optional[str] = None):
    """Serve the Canvas interface for a specific session."""
    if session_id:
        session_dir = local_deployer._get_session_dir(session_id)
        if not session_dir.exists():
            db.reconstruct_session_from_db(session_id, session_dir)
        artifacts_dir = session_dir / "output" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        
    os.makedirs(folder, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
