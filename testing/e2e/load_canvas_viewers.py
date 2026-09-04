"""Open isolated, uniquely authenticated viewer sessions against a local canvas.

The harness starts Narratron with ``--testing_use_local``, creates a disposable
theater, and tears the server down when the run completes. Each viewer gets a
fresh account and Playwright browser context, so server-side viewer identity
and access-cache keys are exercised independently.
"""

import argparse
import asyncio
import base64
import http.cookiejar
import json
import secrets
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, async_playwright


PASSWORD = "LoadTestViewerPassword123!"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Viewer:
    username: str
    auth_token: str


@dataclass(frozen=True)
class CanvasSession:
    url: str
    owner_token: str


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_server(base_url: str, timeout_seconds: float = 20) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/me", timeout=1):
                return
        except urllib.error.URLError:
            time.sleep(0.25)
    raise RuntimeError(f"Local server at {base_url} did not start within {timeout_seconds}s.")


def start_local_server(port: int) -> tuple[subprocess.Popen, str]:
    base_url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "main.py",
        "--testing_use_local",
        "--host=127.0.0.1",
        f"--port={port}",
    ]
    process = subprocess.Popen(command, cwd=REPOSITORY_ROOT)
    try:
        wait_for_server(base_url)
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise
    return process, base_url


def stop_local_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def request_json(url: str, body: dict, cookie_jar: http.cookiejar.CookieJar) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    with opener.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def create_viewer(base_url: str, run_id: str, index: int) -> Viewer:
    username = f"load_viewer_{run_id}_{index}"
    cookie_jar = http.cookiejar.CookieJar()
    try:
        request_json(
            f"{base_url}/api/auth/register",
            {
                "username": username,
                "email": f"{username}@narratron.test",
                "password": PASSWORD,
            },
            cookie_jar,
        )
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Could not register {username}: {detail}") from error

    for cookie in cookie_jar:
        if cookie.name == "auth_token":
            return Viewer(username=username, auth_token=cookie.value)
    raise RuntimeError(f"Registration for {username} did not return an auth_token cookie.")


def create_test_canvas(base_url: str) -> CanvasSession:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    with opener.open(f"{base_url}/api/auth/me", timeout=15) as response:
        auth_state = json.loads(response.read().decode("utf-8"))
    if not auth_state.get("authenticated"):
        raise RuntimeError("The local server did not authenticate its seeded test user.")

    request = urllib.request.Request(
        f"{base_url}/api/theaters/create-and-deploy",
        data=urllib.parse.urlencode({"name": "Viewer Load Test"}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(request, timeout=30) as response:
        created = json.loads(response.read().decode("utf-8"))
    theater_id = created.get("theater_id")
    join_key = created.get("theater", {}).get("join_key")
    if not theater_id or not join_key:
        raise RuntimeError("Local server did not return a theater ID and join key.")
    owner_token = next((cookie.value for cookie in cookie_jar if cookie.name == "auth_token"), None)
    if not owner_token:
        raise RuntimeError("The seeded test user did not receive an auth_token cookie.")
    return CanvasSession(
        url=f"{base_url}/canvas?{urllib.parse.urlencode({'theater_id': theater_id, 'join_key': join_key})}",
        owner_token=owner_token,
    )


def resolve_testcase_audio(testcase: str) -> Path:
    candidate = Path(testcase)
    if not candidate.exists():
        candidate = REPOSITORY_ROOT / "testing" / "testcases" / testcase
    if candidate.is_file():
        return candidate.resolve()
    expectations_path = candidate / "expectations.json"
    if not expectations_path.exists():
        raise FileNotFoundError(f"Testcase {testcase!r} must be an audio file or folder with expectations.json.")
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    audio_path = candidate / expectations.get("narration_audio", "")
    if not audio_path.exists():
        raise FileNotFoundError(f"Testcase audio file does not exist: {audio_path}")
    return audio_path.resolve()


def transcode_to_agent_wav(audio_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000", str(output_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"ffmpeg could not transcode {audio_path}: {result.stderr}")
    return output_path


def load_pcm_audio(wav_path: Path) -> str:
    with wave.open(str(wav_path), "rb") as wav_file:
        if (wav_file.getnchannels(), wav_file.getsampwidth(), wav_file.getframerate()) != (1, 2, 16000):
            raise ValueError("Agent audio must be mono, 16-bit, 16kHz PCM WAV.")
        return base64.b64encode(wav_file.readframes(wav_file.getnframes())).decode("ascii")


async def open_viewer(
    browser: Browser,
    canvas_url: str,
    cookie_domain: str,
    viewer: Viewer,
) -> tuple[BrowserContext, str]:
    context = await browser.new_context()
    await context.add_cookies(
        [{"name": "auth_token", "value": viewer.auth_token, "domain": cookie_domain, "path": "/"}]
    )
    page = await context.new_page()
    await page.goto(canvas_url, wait_until="domcontentloaded")
    await page.wait_for_selector("#image-container")
    auth_state = await page.evaluate(
        "fetch('/api/auth/me').then(async response => ({ok: response.ok, body: await response.json()}))"
    )
    user = auth_state["body"].get("user") if auth_state["ok"] else None
    if not auth_state["body"].get("authenticated") or user is None:
        await context.close()
        raise RuntimeError(f"{viewer.username} was not authenticated after opening the canvas.")
    if user.get("username") != viewer.username:
        await context.close()
        raise RuntimeError(
            f"{viewer.username} opened the canvas as {user.get('username')!r}; viewer identities are not isolated."
        )
    return context, viewer.username


async def open_viewer_after_delay(
    browser: Browser,
    canvas_url: str,
    cookie_domain: str,
    viewer: Viewer,
    delay_seconds: float,
) -> tuple[BrowserContext, str]:
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    return await open_viewer(browser, canvas_url, cookie_domain, viewer)


async def open_owner(browser: Browser, canvas: CanvasSession, cookie_domain: str) -> BrowserContext:
    context = await browser.new_context(permissions=["microphone"])
    await context.add_cookies(
        [{"name": "auth_token", "value": canvas.owner_token, "domain": cookie_domain, "path": "/"}]
    )
    page = await context.new_page()
    await page.goto(f"{canvas.url}&role=orator", wait_until="domcontentloaded")
    await page.wait_for_selector("#image-container")
    await page.add_init_script("localStorage.setItem('narratron_orator_howto_seen', 'true')")
    return context


async def arm_image_visibility_probe(page) -> None:
    await page.evaluate(
        """() => {
            const image = document.getElementById('current-image');
            if (!image) throw new Error('Canvas image element is missing.');
            const baseline = image.currentSrc || image.src;
            window.__loadTestImageSeenAt = null;
            const recordImageVisibility = () => {
                if (
                    !window.__loadTestImageSeenAt &&
                    image.src && image.src !== baseline &&
                    image.complete && image.naturalWidth > 0
                ) {
                    window.__loadTestImageSeenAt = Date.now();
                }
            };
            image.addEventListener('load', recordImageVisibility, { once: false });
            new MutationObserver(recordImageVisibility).observe(image, {
                attributes: true,
                attributeFilter: ['src'],
            });
            recordImageVisibility();
        }"""
    )


async def connect_agent(owner_page, timeout_seconds: float) -> None:
    await owner_page.wait_for_function("typeof window.connectAgentAndMic === 'function'", timeout=10_000)
    await owner_page.evaluate(
        """async () => {
            localStorage.setItem('narratron_orator_howto_seen', 'true');
            if (typeof window.closeOratorHowtoModal === 'function') window.closeOratorHowtoModal();
            await window.startAgentTheater();
            await window.connectAgentAndMic();
        }"""
    )
    await owner_page.wait_for_function("window.isSetupComplete === true", timeout=timeout_seconds * 1000)


async def stream_test_audio(owner_page, pcm_audio: str) -> None:
    """Encode the testcase PCM as the raw Opus packets expected by the live socket."""
    await owner_page.evaluate(
        """async (base64Audio) => {
            if (typeof AudioEncoder !== 'function') {
                throw new Error('This Chromium build does not support WebCodecs AudioEncoder.');
            }
            const websocket = window.agentWs;
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                throw new Error('Agent WebSocket is not open.');
            }
            const binary = atob(base64Audio);
            const rawBytes = new Uint8Array(binary.length);
            for (let index = 0; index < binary.length; index += 1) rawBytes[index] = binary.charCodeAt(index);
            const pcm = new Int16Array(rawBytes.buffer);
            const encoder = new AudioEncoder({
                output: (chunk) => {
                    const opus = new Uint8Array(chunk.byteLength);
                    chunk.copyTo(opus);
                    websocket.send(opus.buffer);
                },
                error: (error) => { throw error; },
            });
            encoder.configure({ codec: 'opus', sampleRate: 16000, numberOfChannels: 1, bitrate: 24000 });
            websocket.send(JSON.stringify({ type: 'activity_start', reason: 'load_test_audio' }));
            let timestampUs = 0;
            const framesPerPacket = 480;
            for (let offset = 0; offset < pcm.length; offset += framesPerPacket) {
                const frameCount = Math.min(framesPerPacket, pcm.length - offset);
                const floatSamples = new Float32Array(frameCount);
                for (let frame = 0; frame < frameCount; frame += 1) floatSamples[frame] = pcm[offset + frame] / 32768;
                const audioData = new AudioData({
                    format: 'f32-planar', sampleRate: 16000, numberOfFrames: frameCount,
                    numberOfChannels: 1, timestamp: timestampUs, data: floatSamples,
                });
                encoder.encode(audioData);
                audioData.close();
                timestampUs += frameCount * 62.5;
                await new Promise(resolve => setTimeout(resolve, (frameCount / 16000) * 1000));
            }
            await encoder.flush();
            encoder.close();
            websocket.send(JSON.stringify({ type: 'activity_end', reason: 'load_test_audio_complete' }));
        }""",
        pcm_audio,
    )


async def measure_trial(
    browser: Browser,
    base_url: str,
    cookie_domain: str,
    run_id: str,
    trial_number: int,
    viewers_count: int,
    launch_interval_seconds: float,
    pcm_audio: str,
    image_timeout_seconds: float,
    hold_seconds: float,
) -> dict:
    canvas = create_test_canvas(base_url)
    print(f"Trial {trial_number}: created disposable canvas.")
    viewers = [
        create_viewer(base_url, f"{run_id}_trial{trial_number}", index)
        for index in range(1, viewers_count + 1)
    ]
    viewer_contexts: list[BrowserContext] = []
    owner_context: BrowserContext | None = None
    try:
        opened_viewers = await asyncio.gather(
            *(
                open_viewer_after_delay(
                    browser,
                    canvas.url,
                    cookie_domain,
                    viewer,
                    index * launch_interval_seconds,
                )
                for index, viewer in enumerate(viewers)
            )
        )
        viewer_contexts = [context for context, _ in opened_viewers]
        viewer_pages = [context.pages[0] for context in viewer_contexts]

        owner_context = await open_owner(browser, canvas, cookie_domain)
        owner_page = owner_context.pages[0]
        print(f"Trial {trial_number}: connecting the agent.")
        await connect_agent(owner_page, image_timeout_seconds)
        await asyncio.gather(*(arm_image_visibility_probe(page) for page in viewer_pages))
        audio_started_at_ms = time.time() * 1000
        await stream_test_audio(owner_page, pcm_audio)

        await asyncio.gather(
            *(page.wait_for_function("window.__loadTestImageSeenAt !== null", timeout=image_timeout_seconds * 1000) for page in viewer_pages)
        )
        observations = []
        for viewer, page in zip(viewers, viewer_pages):
            image_seen_at_ms = await page.evaluate("window.__loadTestImageSeenAt")
            observations.append(
                {
                    "username": viewer.username,
                    "latency_ms": round(image_seen_at_ms - audio_started_at_ms, 2),
                }
            )
        if hold_seconds:
            await asyncio.sleep(hold_seconds)
        latencies = [item["latency_ms"] for item in observations]
        return {
            "trial": trial_number,
            "audio_started_at_unix_ms": round(audio_started_at_ms, 2),
            "viewer_latencies": observations,
            "mean_latency_ms": round(statistics.mean(latencies), 2),
            "min_latency_ms": round(min(latencies), 2),
            "max_latency_ms": round(max(latencies), 2),
        }
    finally:
        if owner_context:
            await owner_context.close()
        await asyncio.gather(*(context.close() for context in viewer_contexts), return_exceptions=True)


def summarize_trials(trials: list[dict]) -> dict:
    means = [trial["mean_latency_ms"] for trial in trials if "mean_latency_ms" in trial]
    all_latencies = [
        observation["latency_ms"]
        for trial in trials
        for observation in trial.get("viewer_latencies", [])
    ]
    return {
        "successful_trials": len(means),
        "mean_trial_latency_ms": round(statistics.mean(means), 2) if means else None,
        "trial_latency_stdev_ms": round(statistics.stdev(means), 2) if len(means) > 1 else 0.0,
        "mean_viewer_latency_ms": round(statistics.mean(all_latencies), 2) if all_latencies else None,
        "observations": len(all_latencies),
    }


async def run(args: argparse.Namespace) -> None:
    port = args.port or find_free_port()
    run_id = f"{int(time.time())}_{secrets.token_hex(3)}"
    results_dir = REPOSITORY_ROOT / "evaluation_results" / f"viewer_load_{run_id}"
    audio_path = resolve_testcase_audio(args.testcase)
    pcm_audio = load_pcm_audio(
        transcode_to_agent_wav(audio_path, results_dir / "narration_16khz_mono.wav")
    )
    print(f"Starting local Narratron server on port {port}.")
    server_process, base_url = start_local_server(port)
    results: dict = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "testcase_audio": str(audio_path),
        "viewers_per_trial": args.viewers,
        "requested_trials": args.trials,
        "trials": [],
    }
    try:
        cookie_domain = urllib.parse.urlsplit(base_url).hostname
        if not cookie_domain:
            raise ValueError("Could not determine the local server hostname.")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=args.headless)
            try:
                for trial_number in range(1, args.trials + 1):
                    try:
                        trial = await measure_trial(
                            browser,
                            base_url,
                            cookie_domain,
                            run_id,
                            trial_number,
                            args.viewers,
                            args.launch_interval_seconds,
                            pcm_audio,
                            args.image_timeout_seconds,
                            args.hold_seconds,
                        )
                        results["trials"].append(trial)
                        print(f"Trial {trial_number}: mean viewer image latency {trial['mean_latency_ms']}ms.")
                    except Exception as error:
                        results["trials"].append({"trial": trial_number, "error": str(error)})
                        print(f"Trial {trial_number} failed: {error}", file=sys.stderr)
            finally:
                await browser.close()
    finally:
        print("Stopping local Narratron server.")
        stop_local_server(server_process)
        results["summary"] = summarize_trials(results["trials"])
        results_dir.mkdir(parents=True, exist_ok=True)
        findings_path = results_dir / "findings.json"
        findings_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Findings written to {findings_path}")

    if results["summary"]["successful_trials"] != args.trials:
        raise RuntimeError(f"{args.trials - results['summary']['successful_trials']} trial(s) failed; see {findings_path}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Local server port; 0 selects a free port.")
    parser.add_argument("--viewers", type=int, default=10, help="Number of independent viewer sessions to open.")
    parser.add_argument("--testcase", default="desert_basic", help="Testcase folder or narration audio file.")
    parser.add_argument("--trials", type=int, default=1, help="Number of independent audio-to-image measurements.")
    parser.add_argument("--image-timeout-seconds", type=float, default=180, help="Maximum wait for the image to reach all viewers.")
    parser.add_argument("--hold-seconds", type=float, default=0, help="Extra time to keep viewers open after each measurement.")
    parser.add_argument("--launch-interval-seconds", type=float, default=0, help="Delay between viewer launches.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.viewers < 1:
        parser.error("--viewers must be at least 1.")
    if args.trials < 1:
        parser.error("--trials must be at least 1.")
    if args.port < 0 or args.port > 65535:
        parser.error("--port must be between 0 and 65535.")
    if args.hold_seconds < 0 or args.launch_interval_seconds < 0 or args.image_timeout_seconds <= 0:
        parser.error("durations must be non-negative.")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
