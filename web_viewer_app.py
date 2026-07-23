import glob
import logging
import os
from pathlib import Path
import time
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
import uvicorn
import yaml

from components.chat_manager import ChatManager

logger = logging.getLogger(__name__)

config_path = Path(__file__).parent / "config.yaml"
with open(config_path, "r") as f:
    config = yaml.safe_load(f)

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

chat_manager = ChatManager(output_dir="output/chats")
current_image_basename = None

active_ws_connections: List[WebSocket] = []
doodles_state: List[dict] = []

# Shared state for music
current_playlist = None
current_playlist_tracks = []
music_paused = False
current_playlist_time = 0.0

def update_current_playlist(playlist_name: str, tracks: List[str]):
    global current_playlist, current_playlist_tracks, current_playlist_time, music_paused
    current_playlist = playlist_name
    current_playlist_tracks = tracks
    music_paused = False
    current_playlist_time = time.time()

def pause_current_playlist():
    global music_paused, current_playlist_time
    music_paused = True
    current_playlist_time = time.time()

def resume_current_playlist():
    global music_paused, current_playlist_time
    music_paused = False
    current_playlist_time = time.time()

# Shared state between agent's show_image tool and web viewer
shown_image_path = None
shown_image_time = 0
shown_image_prompt = ""

def extract_image_prompt(file_path: str) -> str:
    prompt_text = ""
    try:
        if os.path.exists(file_path):
            with Image.open(file_path) as img:
                # 1. Try PNG tEXt chunks first
                if hasattr(img, "info") and img.info:
                    prompt_text = img.info.get("Prompt", "")
                
                # 2. Fallback to EXIF tags (0x010e for ImageDescription / 0x9c9b for XPTitle)
                if not prompt_text:
                    exif = img.getexif()
                    if exif:
                        if 0x010e in exif:
                            prompt_text = exif[0x010e]
                        elif 0x9c9b in exif:
                            val = exif[0x9c9b]
                            if isinstance(val, bytes):
                                try:
                                    prompt_text = val.decode("utf-16le").rstrip("\x00")
                                except Exception:
                                    prompt_text = str(val)
                            else:
                                prompt_text = str(val)
    except Exception:
        pass
    return prompt_text

def update_shown_image(file_path: str):
    global shown_image_path, shown_image_time, shown_image_prompt
    shown_image_path = file_path
    shown_image_time = time.time()
    shown_image_prompt = extract_image_prompt(file_path)

def add_chat_message(text: str, author: str = "agent"):
    chat_manager.add_message({"author": author, "text": text})

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_ws_connections.append(websocket)
    
    # Send existing
    for action in doodles_state:
        await websocket.send_json(action)
        
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "clear":
                doodles_state.clear()
            else:
                doodles_state.append(data)
                
            for connection in active_ws_connections:
                if connection != websocket:
                    try:
                        await connection.send_json(data)
                    except Exception:
                        pass
    except WebSocketDisconnect:
        if websocket in active_ws_connections:
            active_ws_connections.remove(websocket)

@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle():
    count = 0
    for ws in list(active_ws_connections):
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
    music_state = {
        "playlist": current_playlist,
        "tracks": current_playlist_tracks,
        "paused": music_paused,
        "time": current_playlist_time
    }

    if not os.path.exists(folder):
        return {"latest": None, "time": 0, "music": music_state}
    
    files = glob.glob(os.path.join(folder, "*.png"))
    files.extend(glob.glob(os.path.join(folder, "*.jpg")))
    files.extend(glob.glob(os.path.join(folder, "*.jpeg")))
        
    if not files:
        return {"latest": None, "time": 0, "music": music_state}
        
    # Get the newest file by modification time
    latest_file = max(files, key=os.path.getmtime)
    latest_file_time = os.path.getmtime(latest_file)
    
    global shown_image_path, shown_image_time, shown_image_prompt, current_image_basename
    
    # Decide which image to show
    if shown_image_path and os.path.exists(shown_image_path) and shown_image_time >= latest_file_time:
        selected_file = shown_image_path
        selected_time = shown_image_time
        prompt_text = shown_image_prompt
    else:
        selected_file = latest_file
        selected_time = latest_file_time
        prompt_text = extract_image_prompt(selected_file)
        
    basename = os.path.basename(selected_file)
    
    if current_image_basename is not None and current_image_basename != basename:
        chat_manager.export_and_reset(current_image_basename)
        doodles_state.clear()
        
    current_image_basename = basename
    
    res = {
        "latest": f"/images/{basename}",
        "time": selected_time,
        "prompt": prompt_text,
        "music": music_state
    }
    logger.debug(f"[/api/latest] returning latest={res['latest']}, time={res['time']}, playlist={music_state['playlist']}")
    return res

@app.get("/api/chat")
def get_chat():
    return chat_manager.get_messages()

@app.post("/api/chat")
def post_chat(msg: ChatMessage):
    chat_manager.add_message({"author": msg.author, "text": msg.text})
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
