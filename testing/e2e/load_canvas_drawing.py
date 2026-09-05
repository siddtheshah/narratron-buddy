"""Load-test canvas drawing updates while multiple authenticated viewers stay connected.

This test harness starts Narratron with ``--testing_use_local``, creates a disposable
theater, and opens isolated Playwright browser contexts for each viewer with fresh accounts.
Drawing strokes are performed on the canvas, and end-to-end latencies are measured
for every connected spectator:
  - Network delivery latency (drawer emit -> viewer WebSocket packet reception)
  - End-to-end render latency (drawer emit -> viewer 2D canvas stroke execution)
  - Server acknowledgement roundtrip latency (drawer emit -> drawer doodle_ack)
"""

import argparse
import asyncio
import json
import secrets
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

try:
    from load_canvas_viewers import (
        REPOSITORY_ROOT,
        Viewer,
        create_test_canvas,
        create_viewer,
        find_free_port,
        start_local_server,
        stop_local_server,
    )
except ImportError:
    from testing.e2e.load_canvas_viewers import (
        REPOSITORY_ROOT,
        Viewer,
        create_test_canvas,
        create_viewer,
        find_free_port,
        start_local_server,
        stop_local_server,
    )

DOODLE_PROBE_SCRIPT = """
(() => {
    window.__loadTestProbeArmed = false;
    window.__loadTestDoodleReceivedAt = null;
    window.__loadTestDoodleRenderedAt = null;
    window.__loadTestDoodleEvents = [];

    window.__loadTestDrawerArmed = false;
    window.__loadTestDrawerSentAt = null;
    window.__loadTestDrawerAckedAt = null;

    window.__armViewerDoodleProbe = () => {
        window.__loadTestDoodleReceivedAt = null;
        window.__loadTestDoodleRenderedAt = null;
        window.__loadTestDoodleEvents = [];
        window.__loadTestProbeArmed = true;
    };

    window.__armDrawerDoodleProbe = () => {
        window.__loadTestDrawerSentAt = null;
        window.__loadTestDrawerAckedAt = null;
        window.__loadTestDrawerArmed = true;
    };

    const origStroke = CanvasRenderingContext2D.prototype.stroke;
    CanvasRenderingContext2D.prototype.stroke = function(...args) {
        if (window.__loadTestProbeArmed && this.canvas && this.canvas.id === 'doodle-canvas') {
            if (!window.__loadTestDoodleRenderedAt) {
                window.__loadTestDoodleRenderedAt = performance.timeOrigin + performance.now();
            }
        }
        return origStroke.apply(this, args);
    };

    const OrigWS = window.WebSocket;
    const origSend = OrigWS.prototype.send;
    OrigWS.prototype.send = function(data) {
        if (typeof data === 'string') {
            try {
                const parsed = JSON.parse(data);
                if (window.__loadTestDrawerArmed && (parsed.type === 'draw_batch' || parsed.type === 'draw')) {
                    if (!window.__loadTestDrawerSentAt) {
                        window.__loadTestDrawerSentAt = performance.timeOrigin + performance.now();
                    }
                }
            } catch (_) {}
        }
        return origSend.apply(this, arguments);
    };

    window.WebSocket = new Proxy(OrigWS, {
        construct(target, args) {
            const ws = Reflect.construct(target, args);
            const url = args[0] || '';
            if (typeof url === 'string' && url.includes('/ws/doodle')) {
                window.__doodleWs = ws;
                ws.addEventListener('message', (e) => {
                    try {
                        const parsed = JSON.parse(e.data);
                        if (window.__loadTestProbeArmed && (parsed.type === 'draw_batch' || parsed.type === 'draw')) {
                            if (!window.__loadTestDoodleReceivedAt) {
                                window.__loadTestDoodleReceivedAt = performance.timeOrigin + performance.now();
                            }
                            window.__loadTestDoodleEvents.push({
                                type: parsed.type,
                                received_at: performance.timeOrigin + performance.now()
                            });
                        }
                        if (window.__loadTestDrawerArmed && parsed.type === 'doodle_ack') {
                            if (!window.__loadTestDrawerAckedAt) {
                                window.__loadTestDrawerAckedAt = performance.timeOrigin + performance.now();
                            }
                        }
                    } catch (_) {}
                });
            }
            return ws;
        }
    });
})();
"""


async def open_monitored_viewer(
    browser: Browser,
    canvas_url: str,
    cookie_domain: str,
    viewer: Viewer,
) -> tuple[BrowserContext, str]:
    context = await browser.new_context()
    await context.add_init_script(DOODLE_PROBE_SCRIPT)
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


async def open_monitored_viewer_after_delay(
    browser: Browser,
    canvas_url: str,
    cookie_domain: str,
    viewer: Viewer,
    delay_seconds: float,
) -> tuple[BrowserContext, str]:
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    return await open_monitored_viewer(browser, canvas_url, cookie_domain, viewer)


async def open_monitored_owner(browser: Browser, canvas, cookie_domain: str) -> BrowserContext:
    context = await browser.new_context(permissions=["microphone"])
    await context.add_init_script(DOODLE_PROBE_SCRIPT)
    await context.add_cookies(
        [{"name": "auth_token", "value": canvas.owner_token, "domain": cookie_domain, "path": "/"}]
    )
    page = await context.new_page()
    await page.goto(f"{canvas.url}&role=orator", wait_until="domcontentloaded")
    await page.wait_for_selector("#image-container")
    return context


async def wait_for_doodle_ready(page: Page, timeout_seconds: float) -> None:
    await page.wait_for_function(
        "window.__doodleWs && window.__doodleWs.readyState === WebSocket.OPEN",
        timeout=timeout_seconds * 1000,
    )


async def perform_stroke(drawer_page: Page, stroke_index: int, timeout_seconds: float) -> tuple[float, float | None]:
    """Execute a realistic canvas stroke via dispatchEvent, returning (sent_at_unix_ms, acked_at_unix_ms)."""
    # Offset coordinates by stroke index so each stroke has distinct coordinates
    y_offset = (stroke_index * 25) % 150
    start_x = 50 + ((stroke_index * 15) % 80)
    start_y = 100 + y_offset
    end_x = 220 + ((stroke_index * 20) % 80)
    end_y = 220 + y_offset

    await drawer_page.evaluate(
        """({sx, sy, ex, ey}) => {
            const canvas = document.getElementById('doodle-canvas');
            if (!canvas) throw new Error('doodle-canvas element not found');
            const rect = canvas.getBoundingClientRect();

            const down = new MouseEvent('mousedown', { clientX: rect.left + sx, clientY: rect.top + sy, bubbles: true });
            canvas.dispatchEvent(down);

            const move = new MouseEvent('mousemove', { clientX: rect.left + ex, clientY: rect.top + ey, bubbles: true });
            canvas.dispatchEvent(move);

            const up = new MouseEvent('mouseup', { clientX: rect.left + ex, clientY: rect.top + ey, bubbles: true });
            canvas.dispatchEvent(up);
        }""",
        {"sx": start_x, "sy": start_y, "ex": end_x, "ey": end_y},
    )

    await drawer_page.wait_for_function(
        "window.__loadTestDrawerSentAt !== null",
        timeout=timeout_seconds * 1000,
    )
    sent_at = await drawer_page.evaluate("window.__loadTestDrawerSentAt")

    acked_at = None
    try:
        await drawer_page.wait_for_function(
            "window.__loadTestDrawerAckedAt !== null",
            timeout=min(timeout_seconds, 5.0) * 1000,
        )
        acked_at = await drawer_page.evaluate("window.__loadTestDrawerAckedAt")
    except Exception:
        pass

    return sent_at, acked_at


async def measure_trial(
    browser: Browser,
    base_url: str,
    cookie_domain: str,
    run_id: str,
    trial_number: int,
    viewers_count: int,
    strokes_per_trial: int,
    launch_interval_seconds: float,
    timeout_seconds: float,
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
                open_monitored_viewer_after_delay(
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

        owner_context = await open_monitored_owner(browser, canvas, cookie_domain)
        owner_page = owner_context.pages[0]

        # Wait for all doodle sockets to connect
        await asyncio.gather(
            wait_for_doodle_ready(owner_page, timeout_seconds),
            *(wait_for_doodle_ready(page, timeout_seconds) for page in viewer_pages),
        )

        # Allow initial snapshot and toggle messages to settle
        await asyncio.sleep(0.3)

        strokes_data: list[dict] = []
        for stroke_index in range(1, strokes_per_trial + 1):
            # Arm/reset probes
            await asyncio.gather(*(page.evaluate("window.__armViewerDoodleProbe()") for page in viewer_pages))
            await owner_page.evaluate("window.__armDrawerDoodleProbe()")

            sent_at_ms, acked_at_ms = await perform_stroke(owner_page, stroke_index, timeout_seconds)
            server_ack_latency = round(acked_at_ms - sent_at_ms, 2) if acked_at_ms is not None else None

            # Wait for all viewers to render the stroke
            await asyncio.gather(
                *(
                    page.wait_for_function(
                        "window.__loadTestDoodleRenderedAt !== null",
                        timeout=timeout_seconds * 1000,
                    )
                    for page in viewer_pages
                )
            )

            stroke_observations = []
            for viewer, page in zip(viewers, viewer_pages):
                stats = await page.evaluate(
                    """() => ({
                        rendered_at: window.__loadTestDoodleRenderedAt,
                        received_at: window.__loadTestDoodleReceivedAt
                    })"""
                )
                rendered_at = stats.get("rendered_at")
                received_at = stats.get("received_at")
                render_lat = round(max(0.0, rendered_at - sent_at_ms), 2) if rendered_at else None
                delivery_lat = round(max(0.0, received_at - sent_at_ms), 2) if received_at else None
                stroke_observations.append(
                    {
                        "username": viewer.username,
                        "render_latency_ms": render_lat,
                        "delivery_latency_ms": delivery_lat,
                    }
                )

            render_latencies = [o["render_latency_ms"] for o in stroke_observations if o["render_latency_ms"] is not None]
            delivery_latencies = [o["delivery_latency_ms"] for o in stroke_observations if o["delivery_latency_ms"] is not None]

            strokes_data.append(
                {
                    "stroke": stroke_index,
                    "drawer_sent_at_unix_ms": round(sent_at_ms, 2),
                    "server_ack_latency_ms": server_ack_latency,
                    "viewer_observations": stroke_observations,
                    "mean_render_latency_ms": round(statistics.mean(render_latencies), 2) if render_latencies else None,
                    "min_render_latency_ms": round(min(render_latencies), 2) if render_latencies else None,
                    "max_render_latency_ms": round(max(render_latencies), 2) if render_latencies else None,
                    "mean_delivery_latency_ms": round(statistics.mean(delivery_latencies), 2) if delivery_latencies else None,
                }
            )

            if stroke_index < strokes_per_trial:
                await asyncio.sleep(0.1)

        if hold_seconds:
            await asyncio.sleep(hold_seconds)

        all_render_lats = [
            obs["render_latency_ms"]
            for s in strokes_data
            for obs in s["viewer_observations"]
            if obs["render_latency_ms"] is not None
        ]
        all_deliv_lats = [
            obs["delivery_latency_ms"]
            for s in strokes_data
            for obs in s["viewer_observations"]
            if obs["delivery_latency_ms"] is not None
        ]
        all_ack_lats = [
            s["server_ack_latency_ms"]
            for s in strokes_data
            if s["server_ack_latency_ms"] is not None
        ]

        return {
            "trial": trial_number,
            "strokes": strokes_data,
            "mean_render_latency_ms": round(statistics.mean(all_render_lats), 2) if all_render_lats else None,
            "min_render_latency_ms": round(min(all_render_lats), 2) if all_render_lats else None,
            "max_render_latency_ms": round(max(all_render_lats), 2) if all_render_lats else None,
            "mean_delivery_latency_ms": round(statistics.mean(all_deliv_lats), 2) if all_deliv_lats else None,
            "mean_server_ack_latency_ms": round(statistics.mean(all_ack_lats), 2) if all_ack_lats else None,
        }
    finally:
        if owner_context:
            await owner_context.close()
        await asyncio.gather(*(context.close() for context in viewer_contexts), return_exceptions=True)


def summarize_trials(trials: list[dict]) -> dict:
    successful_trials = [t for t in trials if t.get("mean_render_latency_ms") is not None]
    all_render_lats = [
        obs["render_latency_ms"]
        for trial in successful_trials
        for stroke in trial.get("strokes", [])
        for obs in stroke.get("viewer_observations", [])
        if obs.get("render_latency_ms") is not None
    ]
    all_deliv_lats = [
        obs["delivery_latency_ms"]
        for trial in successful_trials
        for stroke in trial.get("strokes", [])
        for obs in stroke.get("viewer_observations", [])
        if obs.get("delivery_latency_ms") is not None
    ]
    all_ack_lats = [
        stroke["server_ack_latency_ms"]
        for trial in successful_trials
        for stroke in trial.get("strokes", [])
        if stroke.get("server_ack_latency_ms") is not None
    ]

    return {
        "successful_trials": len(successful_trials),
        "total_viewer_observations": len(all_render_lats),
        "mean_render_latency_ms": round(statistics.mean(all_render_lats), 2) if all_render_lats else None,
        "min_render_latency_ms": round(min(all_render_lats), 2) if all_render_lats else None,
        "max_render_latency_ms": round(max(all_render_lats), 2) if all_render_lats else None,
        "render_latency_stdev_ms": round(statistics.stdev(all_render_lats), 2) if len(all_render_lats) > 1 else 0.0,
        "mean_delivery_latency_ms": round(statistics.mean(all_deliv_lats), 2) if all_deliv_lats else None,
        "mean_server_ack_latency_ms": round(statistics.mean(all_ack_lats), 2) if all_ack_lats else None,
    }


async def run(args: argparse.Namespace) -> None:
    port = args.port or find_free_port()
    run_id = f"{int(time.time())}_{secrets.token_hex(3)}"
    results_dir = REPOSITORY_ROOT / "evaluation_results" / f"canvas_drawing_load_{run_id}"
    print(f"Starting local Narratron server on port {port}.")
    server_process, base_url = start_local_server(port)
    results: dict = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "viewers_per_trial": args.viewers,
        "requested_trials": args.trials,
        "strokes_per_trial": args.strokes_per_trial,
        "trials": [],
    }
    try:
        cookie_domain = urllib.parse.urlsplit(base_url).hostname
        if not cookie_domain:
            raise RuntimeError("Could not determine the local server hostname.")
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
                            args.strokes_per_trial,
                            args.launch_interval_seconds,
                            args.timeout_seconds,
                            args.hold_seconds,
                        )
                        results["trials"].append(trial)
                        print(
                            f"Trial {trial_number}: mean render latency {trial['mean_render_latency_ms']}ms "
                            f"(delivery: {trial['mean_delivery_latency_ms']}ms, ack: {trial['mean_server_ack_latency_ms']}ms)."
                        )
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
        raise RuntimeError(
            f"{args.trials - results['summary']['successful_trials']} trial(s) failed; see {findings_path}."
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Local server port; 0 selects a free port.")
    parser.add_argument("--viewers", type=int, default=10, help="Number of independent viewer sessions to open.")
    parser.add_argument("--trials", type=int, default=1, help="Number of independent measurement trials.")
    parser.add_argument("--strokes-per-trial", type=int, default=1, help="Number of drawing strokes per trial.")
    parser.add_argument("--timeout-seconds", type=float, default=30, help="Maximum wait for drawing to reach all viewers.")
    parser.add_argument("--hold-seconds", type=float, default=0, help="Extra time to keep viewers open after each trial.")
    parser.add_argument("--launch-interval-seconds", type=float, default=0, help="Delay between viewer launches.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    if args.viewers < 1:
        parser.error("--viewers must be at least 1.")
    if args.trials < 1:
        parser.error("--trials must be at least 1.")
    if args.strokes_per_trial < 1:
        parser.error("--strokes-per-trial must be at least 1.")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    if args.timeout_seconds <= 0 or args.hold_seconds < 0 or args.launch_interval_seconds < 0:
        parser.error("Durations must be positive/non-negative.")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
