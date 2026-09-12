"""Open a guided, real TikTok sandbox draft-export test in the default browser.

Run only after the Narratron server and its reserved ngrok tunnel are running:
    uv run python scripts/test_tiktok_sandbox.py
"""

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Open the guided TikTok sandbox draft-export test.")
    parser.add_argument(
        "--public-base-url",
        help="HTTPS tunnel origin to test, for example https://example.ngrok-free.dev",
    )
    args = parser.parse_args()
    if args.public_base_url:
        os.environ["PUBLIC_BASE_URL"] = args.public_base_url
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        print("Set PUBLIC_BASE_URL to the HTTPS ngrok tunnel before running this test.")
        return 2
    if not os.getenv("TIKTOK_CLIENT", "").strip() or not os.getenv("TIKTOK_CLIENT_SECRET", "").strip():
        print("Set TIKTOK_CLIENT and TIKTOK_CLIENT_SECRET in .env before running this test.")
        return 2

    url = f"{base_url}/obs?clip=1"
    print("Opening the TikTok sandbox draft test:", url)
    print("Ensure the running Narratron server was started with this same PUBLIC_BASE_URL.")
    print("1. Sign in to Narratron in this tunnel-backed browser session if prompted.")
    print("2. Record a short clip, then select ‘Send to TikTok draft’.")
    print("3. Complete TikTok sandbox consent and confirm the clip appears in TikTok inbox.")
    webbrowser.open(url, new=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
