import os
import glob
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
from typing import List
from pydantic import BaseModel
from PIL import Image
from components.chat_manager import ChatManager

app = FastAPI()

# Default folder, can be overridden when run directly
folder = "output/images"

class ChatMessage(BaseModel):
    author: str
    text: str

chat_manager = ChatManager(output_dir="output/chats")
current_image_basename = None

active_ws_connections: List[WebSocket] = []
doodles_state: List[dict] = []

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

@app.get("/api/latest")
def get_latest_image():
    if not os.path.exists(folder):
        return {"latest": None, "time": 0}
    
    files = glob.glob(os.path.join(folder, "*.png"))
    files.extend(glob.glob(os.path.join(folder, "*.jpg")))
        
    if not files:
        return {"latest": None, "time": 0}
        
    global current_image_basename
    latest_file = max(files, key=os.path.getctime)
    basename = os.path.basename(latest_file)
    
    if current_image_basename is not None and current_image_basename != basename:
        chat_manager.export_and_reset(current_image_basename)
        doodles_state.clear()
        
    current_image_basename = basename
    
    prompt_text = ""
    try:
        if latest_file.endswith(".png"):
            with Image.open(latest_file) as img:
                prompt_text = img.info.get("Prompt", "")
    except Exception:
        pass
    return {"latest": f"/images/{basename}", "time": os.path.getctime(latest_file), "prompt": prompt_text}

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
    with open(template_path, "r") as f:
        return f.read()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    
    os.makedirs(folder, exist_ok=True)
    app.mount("/images", StaticFiles(directory=folder), name="images")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
