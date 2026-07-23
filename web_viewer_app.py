import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from components.canvas_state import CanvasStateManager
from utils.config_loader import get_config

logger = logging.getLogger(__name__)

config = get_config()

app = FastAPI()

# Default folder from config (absolute path resolution)
folder = str((Path(__file__).parent / config.get("image_generation", {}).get("output_folder", "output/images")).resolve())
os.makedirs(folder, exist_ok=True)
app.mount("/images", StaticFiles(directory=folder), name="images")

# Playlists folder from config (absolute path resolution)
playlists_folder = str((Path(__file__).parent / config.get("music", {}).get("playlists_folder", "playlists")).resolve())
os.makedirs(playlists_folder, exist_ok=True)
app.mount("/playlists", StaticFiles(directory=playlists_folder), name="playlists")

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
def get_latest_image():
    return canvas_state.get_latest_state(image_folder=folder)

@app.get("/api/chat")
def get_chat():
    return canvas_state.chat_manager.get_messages()

@app.post("/api/chat")
def post_chat(msg: ChatMessage):
    canvas_state.add_chat_message(msg.text, author=msg.author)
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
def read_root():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        
    os.makedirs(folder, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
