"""Load-test a baton handoff while authenticated canvas viewers stay connected.

This reuses the isolated-account, disposable-canvas, and local-server helpers
from :mod:`load_canvas_viewers`.  The first viewer is made an allowed orator;
the owner passes the baton to that viewer while every other viewer remains on
the canvas.  Each trial records request delivery and the accepted-handoff
delivery time for every connected browser.
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

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from load_canvas_viewers import (
    REPOSITORY_ROOT,
    create_test_canvas,
    create_viewer,
    find_free_port,
    open_owner,
    open_viewer_after_delay,
    start_local_server,
    stop_local_server,
)


async def fetch_json(page: Page, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    """Call a canvas API with the authenticated browser session and reject errors."""
    result = await page.evaluate(
        """async ({path, method, body}) => {
            const response = await fetch(path, {
                method,
                headers: body ? { 'Content-Type': 'application/json' } : {},
                body: body ? JSON.stringify(body) : undefined,
            });
            return { ok: response.ok, status: response.status, body: await response.json() };
        }""",
        {"path": path, "method": method, "body": body},
    )
    if not result["ok"]:
        raise RuntimeError(f"{method} {path} returned {result['status']}: {result['body']}")
    return result["body"]


async def wait_for_baton_holder(page: Page, username: str, timeout_seconds: float) -> float:
    """Wait for the rendered baton control so WebSocket UI delivery is exercised."""
    await page.wait_for_function(
        """username => {
            const badge = document.getElementById('baton-status-badge');
            return badge && badge.textContent === `Baton: @${username}`;
        }""",
        arg=username,
        timeout=timeout_seconds * 1000,
    )
    return time.time() * 1000


async def measure_trial(
    browser: Browser,
    base_url: str,
    cookie_domain: str,
    run_id: str,
    trial_number: int,
    viewers_count: int,
    launch_interval_seconds: float,
    timeout_seconds: float,
    hold_seconds: float,
) -> dict:
    canvas = create_test_canvas(base_url)
    theater_id = urllib.parse.parse_qs(urllib.parse.urlsplit(canvas.url).query)["theater_id"][0]
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
                    browser, canvas.url, cookie_domain, viewer, index * launch_interval_seconds
                )
                for index, viewer in enumerate(viewers)
            )
        )
        viewer_contexts = [context for context, _ in opened_viewers]
        viewer_pages = [context.pages[0] for context in viewer_contexts]
        secondary, *spectators = viewers
        secondary_page = viewer_pages[0]

        owner_context = await open_owner(browser, canvas, cookie_domain)
        owner_page = owner_context.pages[0]
        owner_auth = await fetch_json(owner_page, "/api/auth/me")
        if not owner_auth.get("authenticated") or not owner_auth.get("user"):
            raise RuntimeError("The theater owner was not authenticated.")
        secondary_auth = await fetch_json(secondary_page, "/api/auth/me")
        secondary_user = secondary_auth.get("user")
        if not secondary_auth.get("authenticated") or not secondary_user or not secondary_user.get("id"):
            raise RuntimeError("The secondary viewer was not authenticated.")

        baton_path = f"/api/theaters/{urllib.parse.quote(theater_id, safe='')}/baton"
        await fetch_json(
            owner_page,
            f"{baton_path}/allowed_orators",
            method="POST",
            body={"target_user_id": secondary_user["id"]},
        )

        request_started_at_ms = time.time() * 1000
        await fetch_json(
            owner_page,
            f"{baton_path}/request",
            method="POST",
            body={"target_user_id": secondary_user["id"], "timeout_seconds": int(timeout_seconds)},
        )
        await secondary_page.wait_for_selector("#baton-request-modal.active", timeout=timeout_seconds * 1000)
        request_seen_at_ms = time.time() * 1000

        handoff_started_at_ms = time.time() * 1000
        await secondary_page.click("#accept-baton-btn")
        seen_at_ms = await asyncio.gather(
            *(wait_for_baton_holder(page, secondary.username, timeout_seconds) for page in viewer_pages)
        )
        await secondary_page.wait_for_function(
            "document.getElementById('role-badge')?.textContent === 'Orator'",
            timeout=timeout_seconds * 1000,
        )
        if hold_seconds:
            await asyncio.sleep(hold_seconds)

        observations = [
            {"username": viewer.username, "handoff_latency_ms": round(observed - handoff_started_at_ms, 2)}
            for viewer, observed in zip(viewers, seen_at_ms)
        ]
        return {
            "trial": trial_number,
            "secondary_viewer": secondary.username,
            "spectator_count": len(spectators),
            "request_delivery_latency_ms": round(request_seen_at_ms - request_started_at_ms, 2),
            "viewer_handoff_latencies": observations,
            "mean_handoff_latency_ms": round(statistics.mean(item["handoff_latency_ms"] for item in observations), 2),
            "max_handoff_latency_ms": round(max(item["handoff_latency_ms"] for item in observations), 2),
        }
    finally:
        if owner_context:
            await owner_context.close()
        await asyncio.gather(*(context.close() for context in viewer_contexts), return_exceptions=True)


def summarize_trials(trials: list[dict]) -> dict:
    successes = [trial for trial in trials if "mean_handoff_latency_ms" in trial]
    latencies = [
        observation["handoff_latency_ms"]
        for trial in successes
        for observation in trial["viewer_handoff_latencies"]
    ]
    return {
        "successful_trials": len(successes),
        "mean_request_delivery_latency_ms": round(
            statistics.mean(trial["request_delivery_latency_ms"] for trial in successes), 2
        ) if successes else None,
        "mean_handoff_latency_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "max_handoff_latency_ms": round(max(latencies), 2) if latencies else None,
        "viewer_observations": len(latencies),
    }


async def run(args: argparse.Namespace) -> None:
    port = args.port or find_free_port()
    run_id = f"{int(time.time())}_{secrets.token_hex(3)}"
    results_dir = REPOSITORY_ROOT / "evaluation_results" / f"baton_handoff_load_{run_id}"
    print(f"Starting local Narratron server on port {port}.")
    server_process, base_url = start_local_server(port)
    results: dict = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "viewers_per_trial": args.viewers,
        "requested_trials": args.trials,
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
                            browser, base_url, cookie_domain, run_id, trial_number,
                            args.viewers, args.launch_interval_seconds,
                            args.timeout_seconds, args.hold_seconds,
                        )
                        results["trials"].append(trial)
                        print(f"Trial {trial_number}: mean handoff latency {trial['mean_handoff_latency_ms']}ms.")
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
    parser.add_argument("--viewers", type=int, default=10, help="Total logged-in viewers, including the secondary orator.")
    parser.add_argument("--trials", type=int, default=1, help="Number of independent handoff measurements.")
    parser.add_argument("--timeout-seconds", type=float, default=30, help="Maximum wait for each baton UI update.")
    parser.add_argument("--hold-seconds", type=float, default=0, help="Extra time to keep connected viewers open after each handoff.")
    parser.add_argument("--launch-interval-seconds", type=float, default=0, help="Delay between viewer launches.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.viewers < 3:
        parser.error("--viewers must be at least 3: one secondary orator and two other viewers.")
    if args.trials < 1:
        parser.error("--trials must be at least 1.")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535.")
    if args.timeout_seconds <= 0 or args.hold_seconds < 0 or args.launch_interval_seconds < 0:
        parser.error("--timeout-seconds must be positive and other durations non-negative.")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
