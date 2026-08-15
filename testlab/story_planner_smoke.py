"""Run one real Vertex-backed Story Planner turn outside the Test Lab web UI.

Usage:
    python testlab/story_planner_smoke.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from testlab.server import app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a real Story Planner ADK turn.")
    parser.add_argument("--model", default="gemini-3.7-flash")
    parser.add_argument("--nodes", type=int, default=3, choices=range(1, 9))
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    client = TestClient(app)
    created = client.post(
        "/api/story-planner/sessions",
        json={"planner_model": args.model, "nodes_ahead": args.nodes},
    )
    if not created.is_success:
        print(created.text, file=sys.stderr)
        return 1
    session = created.json()
    submitted = client.post(
        f"/api/story-planner/sessions/{session['id']}/actions",
        json={"action": "I climb the spiral stairs and call out into the dark."},
    )
    if not submitted.is_success:
        print(submitted.text, file=sys.stderr)
        return 1

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/story-planner/sessions/{session['id']}")
        if not response.is_success:
            print(response.text, file=sys.stderr)
            return 1
        session = response.json()
        if session["events"]:
            break
        time.sleep(0.25)
    else:
        print(json.dumps({"error": f"No callback after {args.timeout:.0f} seconds."}, indent=2))
        return 1

    result = session["events"][-1]["result"]
    output = {
        "result": result,
        "characters": session["state"]["characters"],
        "plot_beats": session["state"]["plot_beats"],
    }
    print(json.dumps(output, indent=2))
    if result.get("error"):
        return 1
    if len(output["plot_beats"]) != args.nodes:
        print("Planner returned a result but did not commit the expected plot beats.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
