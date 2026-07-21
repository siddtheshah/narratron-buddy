import asyncio
import base64
import json
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import os

app = FastAPI()

html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Test Image Display</title>
        <script>
            function createImageBubble(imageDataUrl, isUser) {
                const messageDiv = document.createElement("div");
                messageDiv.className = `message ${isUser ? "user" : "agent"}`;
                
                const bubbleDiv = document.createElement("div");
                bubbleDiv.className = "bubble image-bubble";
                
                const img = document.createElement("img");
                img.src = imageDataUrl;
                img.className = "bubble-image";
                img.alt = "Captured image";
                img.style.maxWidth = "300px";
                
                bubbleDiv.appendChild(img);
                messageDiv.appendChild(bubbleDiv);
                
                return messageDiv;
            }

            let ws = new WebSocket("ws://localhost:8081/ws");
            ws.onmessage = function(event) {
                const adkEvent = JSON.parse(event.data);
                console.log("Received:", adkEvent);
                const messagesDiv = document.getElementById("messages");

                if (adkEvent.custom_image) {
                    const imageDataUrl = `data:${adkEvent.custom_image.mimeType};base64,${adkEvent.custom_image.data}`;
                    console.log("Creating image bubble with URL length: ", imageDataUrl.length);
                    const imageBubble = createImageBubble(imageDataUrl, false);
                    messagesDiv.appendChild(imageBubble);
                } else {
                    console.log("No custom_image payload found.");
                }
            };
        </script>
    </head>
    <body>
        <h1>Test Image WebSocket</h1>
        <p>If the websocket connects successfully, an image should appear below automatically.</p>
        <div id="messages" style="border: 1px solid black; padding: 10px; min-height: 200px;"></div>
    </body>
</html>
"""

@app.get("/")
async def get():
    return HTMLResponse(html)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # Send an image
    try:
        output_dir = "output/images"
        if os.path.exists(output_dir):
            images = [f for f in os.listdir(output_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
            if images:
                file_path = os.path.join(output_dir, images[0])
                print(f"Sending image {file_path}")
                with open(file_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
                
                mime_type = "image/png"
                if file_path.lower().endswith((".jpg", ".jpeg")):
                    mime_type = "image/jpeg"
                
                custom_event = {
                    "custom_image": {
                        "mimeType": mime_type,
                        "data": img_b64
                    }
                }
                await websocket.send_text(json.dumps(custom_event))
            else:
                await websocket.send_text('{"error": "No images found"}')
        else:
            await websocket.send_text('{"error": "Directory not found"}')
    except Exception as e:
        print(f"Error: {e}")
    
    try:
        while True:
            await websocket.receive_text()
    except:
        pass

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081)
