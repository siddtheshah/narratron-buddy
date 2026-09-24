"""Print the filter values available in Gemini's Extended Voice Library.

Run with ``uv run python testlab/list_gemini_voice_tags.py`` after setting
``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``. The script never prints the key.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from google import genai
from dotenv import load_dotenv


def _value(voice: Any, name: str) -> Any:
    return voice.get(name) if isinstance(voice, dict) else getattr(voice, name, None)


def _values(voices: Iterable[Any], field: str) -> list[str]:
    values: set[str] = set()
    for voice in voices:
        value = _value(voice, field)
        if isinstance(value, (list, tuple, set)):
            values.update(str(item) for item in value if item)
        elif value:
            values.add(str(value))
    return sorted(values)


def main() -> None:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before listing Gemini voices.")

    client = genai.Client(api_key=api_key, enterprise=False, vertexai=False)
    voices: list[Any] = []
    page_token: str | None = None
    while True:
        response = client.voices.list(type_=["prebuilt"], page_size=1000, page_token=page_token)
        voices.extend(response.voices or [])
        page_token = response.next_page_token
        if not page_token:
            break
    print(f"voices={len(voices)}")
    for field in ("language_code", "region_code", "gender", "accent", "pitch", "persona", "contexts"):
        print(f"{field}={_values(voices, field)}")


if __name__ == "__main__":
    main()
