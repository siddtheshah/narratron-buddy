"""Record the clean OBS stage for the mobile theater controller."""

import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from playwright.async_api import Browser, JSHandle, async_playwright

from api_server.shared import app, _require_canvas_access_async, can_control_agent_websocket, get_current_user_async
from object_registry import FLAGS

logger = logging.getLogger(__name__)
_recording_slots = asyncio.Semaphore(2)
_clip_duration_seconds = 30


async def _start_stage(browser: Browser, origin: str, theater_id: str, token: str) -> JSHandle:
    context = await browser.new_context(viewport={"width": 1280, "height": 720}, accept_downloads=False)
    await context.add_cookies([{"name": "auth_token", "value": token, "url": origin, "httpOnly": True}])
    page = await context.new_page()
    stage = await page.goto(f"{origin}/obs?{urlencode({'theater_id': theater_id})}", wait_until="domcontentloaded")
    if stage is None or not stage.ok:
        raise RuntimeError("The OBS stage failed to load.")
    await page.locator('#canvas-renderer[data-stage-ready="true"]').wait_for(state="attached")
    return await page.evaluate_handle("""async () => {
        const { startObsStageRecording } = await import('/static/generated/pages/obs-stage-recording.js');
        return startObsStageRecording();
    }""")


def _encode_mp4(ffmpeg: str, webm: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="narratron-mobile-clip-") as directory:
        source = Path(directory) / "stage.webm"
        target = Path(directory) / "stage.mp4"
        source.write_bytes(webm)
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-i", str(source), "-t", str(_clip_duration_seconds),
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
                "-crf", "23", "-maxrate", "2M", "-bufsize", "2M", "-threads", "2",
                "-c:a", "aac", "-movflags", "+faststart", str(target),
            ],
            check=True, capture_output=True, timeout=20,
        )
        return target.read_bytes()


@app.websocket("/ws/clip-recording")
async def record_mobile_clip(websocket: WebSocket, theater_id: str) -> None:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    token = websocket.cookies.get("auth_token")
    if not host or origin not in {f"http://{host}", f"https://{host}"} or not token:
        await websocket.close(code=1008)
        return

    try:
        deployment = await _require_canvas_access_async(websocket, theater_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    user = await get_current_user_async(websocket)
    if not can_control_agent_websocket(deployment, current_user=user):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or _recording_slots.locked():
        message = "Clip recording is busy. Try again shortly." if ffmpeg else "Clip recording is unavailable."
        await websocket.send_json({"type": "error", "message": message})
        await websocket.close()
        return

    local_host = {"0.0.0.0": "127.0.0.1", "::": "[::1]", "::1": "[::1]"}.get(FLAGS.host, FLAGS.host)
    stage_origin = f"http://{local_host}:{FLAGS.port}"
    try:
        async with _recording_slots, async_playwright() as playwright:
            # Full Chromium supports tab capture in headless mode. The browser
            # has one tab, and only the OBS page title can match the picker.
            browser = await playwright.chromium.launch(
                channel="chromium",
                args=[
                    "--auto-select-tab-capture-source-by-title=Narratron OBS Canvas",
                    "--autoplay-policy=no-user-gesture-required",
                ],
                ignore_default_args=["--mute-audio"],
                timeout=10_000,
            )
            async with browser:
                async with asyncio.timeout(15):
                    recording = await _start_stage(browser, stage_origin, theater_id, token)
                await websocket.send_json({"type": "recording"})
                try:
                    async with asyncio.timeout(_clip_duration_seconds):
                        if await websocket.receive_text() != "stop":
                            await websocket.close(code=1008)
                            return
                except TimeoutError:
                    pass
                await websocket.send_json({"type": "processing"})
                async with asyncio.timeout(8):
                    webm = base64.b64decode(await recording.evaluate("recording => recording.stop()"))
            mp4 = await asyncio.to_thread(_encode_mp4, ffmpeg, webm)
            await websocket.send_bytes(mp4)
            await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Mobile OBS clip recording failed.")
        try:
            await websocket.send_json({"type": "error", "message": "Could not create the clip. Try recording again."})
            await websocket.close()
        except WebSocketDisconnect:
            pass
