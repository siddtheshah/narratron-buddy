"""Run one real provider call that creates and validates two A2UI health bars.

Usage:
    python testlab/a2ui_canvas_smoke.py
    python testlab/a2ui_canvas_smoke.py --image testlab/images/trace-knight-sword.png
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from providers import get_text_response_provider  # noqa: E402
from testlab.a2ui_canvas_lab import run_health_bars_smoke  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test real A2UI Canvas health-bar generation.")
    parser.add_argument("--model", default="gemini-3.7-flash")
    parser.add_argument("--image", type=Path, help="Optional image to supply with the canvas request.")
    parser.add_argument("--debug", action="store_true", help="Print generated drafts and provider diagnostics.")
    args = parser.parse_args()
    if args.image is not None and not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}")
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    provider = get_text_response_provider("gemini-3", options={"model": args.model})
    result = run_health_bars_smoke(provider, model=args.model, image_path=args.image)
    print(json.dumps(result, indent=2, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
