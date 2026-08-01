#!/usr/bin/env python
import asyncio
import base64
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from absl import app, flags
from dotenv import load_dotenv
from google import genai
from google.genai import types
from playwright.async_api import async_playwright
import yaml

load_dotenv()

FLAGS = flags.FLAGS

flags.DEFINE_string(
    "testcase",
    None,
    "Path to input audio narration file OR testcase folder containing expectations.json",
    short_name="t",
)
flags.DEFINE_string(
    "output",
    "evaluation_results/eval_result1.mp4",
    "Path to the output video file",
    short_name="o",
)
flags.DEFINE_integer(
    "port",
    0,
    "Port to run the FastAPI server on (0 defaults to free port)",
    short_name="p",
)
flags.DEFINE_boolean(
    "headless",
    True,
    "Run browser in headless mode",
)
flags.DEFINE_integer(
    "buffer",
    15,
    "Extra buffer time in seconds at the end of the video",
    short_name="b",
)
flags.DEFINE_boolean(
    "use_in_memory_artifacts",
    True,
    "Use InMemoryArtifactService pre-loaded with test artifacts",
)

def find_free_port():
    """Finds a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def wait_for_server(port, timeout=20):
    """Waits for the FastAPI server to become responsive."""
    url = f"http://127.0.0.1:{port}/"
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(url) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionRefusedError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Server at {url} failed to start within {timeout} seconds.")

def transcode_audio_to_wav(input_path, output_wav_path):
    """Transcodes input audio file to 16kHz, 16-bit, mono WAV format using ffmpeg."""
    print(f"[Evaluator] Transcoding {input_path} to mono 16kHz WAV: {output_wav_path}...")
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ac", "1",
        "-ar", "16000",
        output_wav_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode('utf-8', errors='ignore')
        raise RuntimeError(f"FFmpeg transcoding failed:\n{error_msg}")
    print("[Evaluator] Transcoding complete.")

def mux_audio_video(video_path, audio_path, output_path, delay_ms):
    """Muxes the recorded WebM video with the delayed narration WAV audio using ffmpeg."""
    print(f"[Evaluator] Merging video and audio into {output_path} (delaying audio by {delay_ms}ms)...")
    # Using adelay filter to sync narration with the moment streaming actually started
    # For mono audio input, adelay=delay_ms is sufficient
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", f"[1:a]adelay={delay_ms}[aud]",
        "-map", "0:v",
        "-map", "[aud]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        error_msg = result.stderr.decode('utf-8', errors='ignore')
        raise RuntimeError(f"FFmpeg muxing failed:\n{error_msg}")
    print("[Evaluator] Muxing complete. Video saved successfully.")

async def stream_audio_task(page, wav_path, buffer_time):
    """Streams mono WAV file chunks into the browser's agent WebSocket via page.evaluate()."""
    print("[Evaluator] Connecting agent and enabling audio input visually in browser...")
    
    # Latency measurement state
    streaming_start_time = 0.0
    streaming_active = False
    first_tool_time = None
    
    # Setup console listener to catch server events and debugging logs
    def handle_console(msg):
        nonlocal first_tool_time
        text = msg.text
        if text.startswith("WS_MSG:"):
            try:
                data = json.loads(text[7:])
                if "inputTranscription" in data:
                    t = data["inputTranscription"].get("text", "")
                    if t:
                        print(f"[Agent User Transcript] {t}")
                elif "outputTranscription" in data:
                    t = data["outputTranscription"].get("text", "")
                    if t:
                        print(f"[Agent Output Transcript] {t}")
                elif "content" in data and "parts" in data["content"]:
                    for part in data["content"]["parts"]:
                        if "text" in part:
                            print(f"[Agent Text] {part['text']}")
                elif "custom_image" in data:
                    tool_now = time.time()
                    if streaming_start_time > 0 and first_tool_time is None:
                        first_tool_time = tool_now
                        lag = first_tool_time - streaming_start_time
                        is_while_talking = streaming_active
                        status_str = "⚡ WHILE NARRATOR TALKING" if is_while_talking else "⚠️ AFTER PAUSE/STREAM END"
                        print(f"\n[Evaluator Latency] ⏱️ IMAGE TOOL CALL COMPLETED: {lag:.2f}s after narration start ({status_str})\n")
                    print("[Agent Action] Show Image payload received.")
            except Exception:
                pass
        else:
            print(f"[Browser Console] {msg.type.upper()}: {text}")
                
    page.on("console", handle_console)
    
    # Wait for helper functions to be exposed on window
    await page.wait_for_function("typeof window.connectAgentAndMic === 'function'", timeout=10000)
    
    # Start Agent first before connecting
    print("[Evaluator] Starting agent before connecting...")
    await page.evaluate("""async () => {
        if (typeof window.startAgentTheater === 'function') {
            await window.startAgentTheater();
        } else {
            const btn = document.getElementById('agent-start-stop-btn');
            if (btn) btn.click();
        }
    }""")
    await asyncio.sleep(1)

    # Trigger connection and microphone status visually
    await page.evaluate("async () => { await window.connectAgentAndMic(); }")
    
    # Wait for setupComplete from Gemini Live with auto-reconnection
    print("[Evaluator] Waiting for Gemini Live API setup to be complete...")
    setup_start_time = time.time()
    setup_timeout = 180.0
    is_connecting = False
    while True:
        # Check if setup is complete
        is_setup_complete = await page.evaluate("window.isSetupComplete === true")
        if is_setup_complete:
            break
            
        if time.time() - setup_start_time > setup_timeout:
            raise TimeoutError("Timeout waiting for Gemini Live API setup to complete.")
            
        # Check if agentWs is closed or disconnected
        ws_status = await page.evaluate("""() => {
            if (!window.agentWs) return "disconnected";
            return window.agentWs.readyState; // 0: CONNECTING, 1: OPEN, 2: CLOSING, 3: CLOSED
        }""")
        
        if ws_status == 1: # OPEN
            is_connecting = False
        elif (ws_status == "disconnected" or ws_status == 3) and not is_connecting: # CLOSED or null
            print("[Evaluator] Agent WebSocket closed. Re-triggering connection in 5 seconds...")
            is_connecting = True
            await asyncio.sleep(5)
            await page.evaluate("window.connectAgentAndMic()")
            
        await asyncio.sleep(2)
    
    print("[Evaluator] Audio input active and Gemini theater ready. Ready to stream narration WAV...")
    
    with wave.open(wav_path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sampwidth = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        
        if n_channels != 1 or sampwidth != 2 or framerate != 16000:
            raise ValueError(f"WAV must be mono, 16-bit, 16kHz. Got channels={n_channels}, width={sampwidth}, rate={framerate}")
        
        all_pcm_data = wav_file.readframes(n_frames)
        full_b64 = base64.b64encode(all_pcm_data).decode('utf-8')
        
        print(f"[Evaluator] Streaming {n_frames} frames ({n_frames/16000:.2f}s) via in-browser audio loop...")
        streaming_start_time = time.time()
        streaming_active = True
        
        await page.evaluate(f"window.streamFullAudio('{full_b64}', 2048, 64)")
        
        actual_duration = time.time() - streaming_start_time
        streaming_active = False
        print(f"[Evaluator] Audio streaming completed in {actual_duration:.2f}s.")
        
    print(f"[Evaluator] Keeping WebSocket open for {buffer_time}s buffer for agent to settle...")
    await asyncio.sleep(buffer_time)

def evaluate_video_expectations(video_path, expectations_path):
    print(f"\n[Evaluator] Loading expectations from {expectations_path}...")
    
    with open(expectations_path, "r") as f:
        expectations_data = json.load(f)
        
    expectations_list = expectations_data.get("expectations", [])
    if not expectations_list:
        print("[Evaluator] No expectations found to evaluate.")
        return
        
    print(f"[Evaluator] Starting AI Video Evaluation using Veo (Gemini model)...")
    
    # Load config for gcloud project/location
    config_data = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r") as f:
            try:
                config_data = yaml.safe_load(f) or {}
            except Exception:
                pass
                
    project_id = config_data.get("gcloud", {}).get("project_id", os.getenv("GOOGLE_CLOUD_PROJECT"))
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    
    # Initialize genai client in Vertex AI mode
    # Initialize genai client in Developer API mode using local GEMINI_API_KEY
    client = genai.Client(vertexai=False)
    
    # Read video file
    print(f"[Evaluator] Reading video file for evaluation: {video_path}...")
    with open(video_path, "rb") as f:
        video_bytes = f.read()
        
    # Prepare inline video part using Blob structure
    video_part = types.Part(
        inline_data=types.Blob(
            mime_type="video/mp4",
            data=video_bytes
        )
    )
    
    # We will use gemini-3.5-flash which is optimized for video understanding and structured outputs
    model_name = "gemini-3.5-flash"
    
    prompt = f"""
You are an expert automated AI video evaluator grading the performance of a story-assistant agent (Narratron) based on a recorded video of its browser UI canvas.

The video has visual elements (the canvas showing generated images, header controls, sidebar chat, loading animations) and audio elements.

Analyze the attached video and score the following expectations strictly based on the criteria specified in each expectation. Do not hallucinate or assume success without clear, empirical evidence in the video.

Expectations to check:
{json.dumps(expectations_list, indent=2)}

For each expectation, determine whether it passed or failed based strictly on the criteria and evidence in the video.
Provide your evaluation as a JSON object matching this schema:
{{
  "evaluation_results": [
    {{
      "id": "expectation_id",
      "passed": true,
      "reasoning": "Detailed evidence from the video supporting the pass/fail grade."
    }}
  ],
  "overall_summary": "A high-level summary of the agent's performance and any grading notes."
}}

Only return the raw JSON object matching this schema. Do not add markdown backticks.
"""

    print(f"[Evaluator] Sending video to {model_name}...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[video_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "evaluation_results": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "id": types.Schema(type=types.Type.STRING),
                                    "passed": types.Schema(type=types.Type.BOOLEAN),
                                    "reasoning": types.Schema(type=types.Type.STRING)
                                },
                                required=["id", "passed", "reasoning"]
                            )
                        ),
                        "overall_summary": types.Schema(type=types.Type.STRING)
                    },
                    required=["evaluation_results", "overall_summary"]
                )
            )
        )
        
        result_json = response.text
        # Save evaluation result alongside expectations
        report_path = Path(video_path).parent / "eval_report.json"
        with open(report_path, "w", encoding="utf-8") as rf:
            rf.write(result_json)
            
        result_data = json.loads(result_json)
        
        # Display a beautiful console summary table
        testcase_name = expectations_data.get("testcase", "Unknown")
        print("\n" + "=" * 60)
        print(f"            EVALUATION REPORT: {testcase_name}")
        print("=" * 60)
        
        all_passed = True
        for res in result_data.get("evaluation_results", []):
            eid = res.get("id")
            passed = res.get("passed")
            reasoning = res.get("reasoning")
            status = "[PASS]" if passed else "[FAIL]"
            if not passed:
                all_passed = False
            print(f"{status} {eid}")
            print(f"       Reasoning: {reasoning}")
            print("-" * 60)
            
        print(f"Overall Summary: {result_data.get('overall_summary')}")
        print("=" * 60)
        print(f"[Evaluator] Full evaluation report saved to: {report_path}")
        
        if not all_passed:
            print("[Evaluator] Warning: One or more expectations FAILED.")
            
    except Exception as e:
        print(f"[Evaluator] Error running video evaluation: {e}")

async def run_evaluation(audio_path, output_path, port, headless, buffer_time, expectations_path=None, use_in_memory_artifacts=True):
    # Setup paths
    audio_path_obj = Path(audio_path).resolve()
    if not audio_path_obj.exists():
        raise FileNotFoundError(f"Input audio file not found: {audio_path}")
        
    output_path_obj = Path(output_path).resolve()
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    # Temporary transcode path
    temp_wav_path = output_path_obj.parent / "temp_eval_narration.wav"
    
    # Step 1: Transcode input audio to WAV
    transcode_audio_to_wav(str(audio_path_obj), str(temp_wav_path))
    
    # Step 2: Start the FastAPI application server
    print(f"[Evaluator] Starting Narratron FastAPI server on port {port}...")
    python_exe = sys.executable
    server_cmd = [
        python_exe, "-m", "uvicorn", 
        "main:app",
        "--host", "127.0.0.1", 
        "--port", str(port),
        "--log-level", "info"
    ]
    
    # Open log file for server output
    server_log_path = output_path_obj.parent / "eval_server.log"
    server_log = open(server_log_path, "w", encoding="utf-8")
    
    server_env = os.environ.copy()
    server_env["USE_IN_MEMORY_ARTIFACTS"] = "1" if use_in_memory_artifacts else "0"
    
    server_process = subprocess.Popen(
        server_cmd,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        cwd=str(Path(__file__).parent.resolve()),
        env=server_env
    )
    
    try:
        # Wait for the server to be responsive
        wait_for_server(port)
        print("[Evaluator] Server is online and responsive.")
        
        # Step 2.5: Create & Deploy a Session with Authentication
        theater_id = None
        join_key = ""
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        auth_user = "eval_user"
        auth_email = "eval@example.com"
        auth_pass = "EvalPassword123!"

        # Register or login evaluation user
        reg_url = f"http://127.0.0.1:{port}/api/auth/register"
        reg_body = json.dumps({
            "username": auth_user,
            "email": auth_email,
            "password": auth_pass
        }).encode("utf-8")
        reg_req = urllib.request.Request(reg_url, data=reg_body, headers={"Content-Type": "application/json"})

        try:
            with opener.open(reg_req) as resp:
                print("[Evaluator] Registered eval_user successfully.")
        except Exception:
            login_url = f"http://127.0.0.1:{port}/api/auth/login"
            login_body = json.dumps({
                "username_or_email": auth_user,
                "password": auth_pass
            }).encode("utf-8")
            login_req = urllib.request.Request(login_url, data=login_body, headers={"Content-Type": "application/json"})
            with opener.open(login_req) as resp:
                print("[Evaluator] Logged in eval_user successfully.")

        # Create & deploy theater via form data
        create_url = f"http://127.0.0.1:{port}/api/theaters/create-and-deploy"
        form_data = urllib.parse.urlencode({"name": "Evaluation Session"}).encode("utf-8")
        create_req = urllib.request.Request(
            create_url,
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with opener.open(create_req) as resp:
            created_theater = json.loads(resp.read().decode("utf-8"))
            theater_id = created_theater.get("theater_id")
            theater_info = created_theater.get("theater", {})
            join_key = theater_info.get("join_key", "")
            print(f"[Evaluator] Created theater '{theater_id}' (Join Key: '{join_key}')")

        if not theater_id:
            raise RuntimeError("Failed to create theater via API: theater_id is empty.")

        # Step 3: Launch Playwright & start video recording
        async with async_playwright() as p:
            print("[Evaluator] Launching browser...")
            browser = await p.chromium.launch(
                headless=headless,
                args=["--autoplay-policy=no-user-gesture-required"]
            )
            
            # Setup recording context
            video_dir = output_path_obj.parent / "raw_videos"
            video_dir.mkdir(parents=True, exist_ok=True)
            
            context = await browser.new_context(
                record_video_dir=str(video_dir),
                record_video_size={"width": 1280, "height": 720},
                viewport={"width": 1280, "height": 720},
                permissions=["microphone"]
            )

            # Pass auth cookies to Playwright context
            cookies_to_add = []
            for cookie in cj:
                cookies_to_add.append({
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": "127.0.0.1",
                    "path": cookie.path or "/",
                })
            if cookies_to_add:
                await context.add_cookies(cookies_to_add)
            
            # Dismiss onboarding modal by setting localStorage before page load
            await context.add_init_script("localStorage.setItem('narratron_orator_howto_seen', 'true');")
            
            # Measure time of recording start
            record_start_time = time.time()
            
            page = await context.new_page()
            
            # Build target canvas URL
            canvas_url = f"http://127.0.0.1:{port}/canvas?theater_id={theater_id}&join_key={join_key}&role=orator"

            print(f"[Evaluator] Opening Narratron Orator Canvas at {canvas_url} ...")
            await page.goto(canvas_url)
            await page.wait_for_selector("#image-container")

            # Ensure any onboarding popup modal is dismissed when page is entered
            await page.evaluate("""() => {
                localStorage.setItem('narratron_orator_howto_seen', 'true');
                if (typeof window.closeOratorHowtoModal === 'function') {
                    window.closeOratorHowtoModal();
                }
            }""")
            try:
                ack_btn = page.locator("#howto-ack-btn")
                if await ack_btn.count() > 0 and await ack_btn.is_visible():
                    await ack_btn.click()
            except Exception:
                pass
            
            # Measure delay from recording start to audio stream start
            stream_start_time = time.time()
            audio_delay_ms = int((stream_start_time - record_start_time) * 1000)
            
            # Step 4: Stream audio chunks directly through browser websocket
            await stream_audio_task(page, str(temp_wav_path), buffer_time)
            
            # Step 5.5: Save a screenshot for visual debugging
            screenshot_path = output_path_obj.parent / "eval_screenshot.png"
            await page.screenshot(path=str(screenshot_path))
            print(f"[Evaluator] Debug screenshot saved to: {screenshot_path}")
            
            # Step 6: Close browser to finish video recording
            print("[Evaluator] Closing browser context...")
            video_path = await page.video.path()
            await context.close()
            await browser.close()
            
            print(f"[Evaluator] Raw video recorded to: {video_path}")
            
    finally:
        # Clean up the server process
        print("[Evaluator] Terminating FastAPI server...")
        server_process.terminate()
        server_process.wait()
        server_log.close()
        print("[Evaluator] Server terminated.")
        
    # Step 7: Mux original narration audio onto the video
    try:
        mux_audio_video(video_path, str(temp_wav_path), str(output_path_obj), audio_delay_ms)
    finally:
        # Clean up temp WAV file
        if temp_wav_path.exists():
            try:
                os.remove(temp_wav_path)
            except Exception as e:
                print(f"[Evaluator] Warning: could not remove temp file {temp_wav_path}: {e}")
                
    print(f"\n[Evaluator] Success! Evaluation video saved to: {output_path_obj}")
    
    # Step 8: Evaluate expectations if present
    if expectations_path:
        evaluate_video_expectations(str(output_path_obj), expectations_path)

def main(argv):
    del argv  # Unused.
    import json
    
    input_str = FLAGS.testcase
    if not input_str:
        print("[Evaluator] Error: --testcase flag is required (e.g. --testcase=test_narration.wav or --testcase=case1).", file=sys.stderr)
        sys.exit(1)
        
    # Parse testcase or direct audio input
    input_path = Path(input_str)
    
    # If the input path doesn't exist directly, check if it refers to a case name in testing/testcases
    if not input_path.exists():
        testcase_dir = Path("testing/testcases") / input_str
        if testcase_dir.exists() and testcase_dir.is_dir():
            input_path = testcase_dir
            
    expectations_path = None
    if input_path.is_dir():
        expectations_file = input_path / "expectations.json"
        if not expectations_file.exists():
            raise FileNotFoundError(f"Testcase directory {input_path} must contain expectations.json")
            
        with open(expectations_file, "r") as f:
            expectations_data = json.load(f)
            
        audio_filename = expectations_data.get("narration_audio", "test_narration.wav")
        audio_path = input_path / audio_filename
        expectations_path = expectations_file
        print(f"[Evaluator] Input recognized as testcase folder: {input_path}")
    else:
        audio_path = input_path
        # Check if expectations.json exists in same folder
        sibling_expectations = audio_path.parent / "expectations.json"
        if sibling_expectations.exists():
            expectations_path = sibling_expectations
            print(f"[Evaluator] Input recognized as direct audio with expectations: {audio_path}")
        else:
            print(f"[Evaluator] Input recognized as direct audio without expectations: {audio_path}")
            
    port = FLAGS.port if FLAGS.port != 0 else find_free_port()
    
    try:
        asyncio.run(run_evaluation(
            audio_path=str(audio_path),
            output_path=FLAGS.output,
            port=port,
            headless=FLAGS.headless,
            buffer_time=FLAGS.buffer,
            expectations_path=expectations_path,
            use_in_memory_artifacts=FLAGS.use_in_memory_artifacts
        ))
    except Exception as e:
        print(f"\n[Evaluator] Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    app.run(main)
