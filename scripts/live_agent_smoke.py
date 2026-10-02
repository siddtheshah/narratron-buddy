"""Exercise the configured Live model through ADK and Narratron's input queue.

Run with ``uv run python -m scripts.live_agent_smoke``. Optionally supply a
16 kHz mono PCM WAV with ``--audio`` to also check voice input. This makes live
API requests, but the probe tool has no external side effects.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import wave

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

from services.live_agent import DeveloperLiveGemini, build_run_config
from services.priority_live_request_queue import PriorityLiveRequestQueue
from utils.config_loader import get_app_config


def report_probe(label: str) -> dict[str, str]:
    """Record a completed input probe and return confirmation to the model."""
    return {"status": "ok", "label": label}


def read_audio(path: Path) -> bytes:
    """Read a WAV in the same PCM format as Narratron's microphone stream."""
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("Audio must be 16 kHz, 16-bit mono PCM WAV")
        return audio.readframes(audio.getnframes())


async def send_audio(queue: PriorityLiveRequestQueue, audio: bytes) -> None:
    """Stream 100 ms microphone chunks with explicit activity boundaries."""
    queue.send_activity_start()
    for offset in range(0, len(audio), 3200):
        queue.send_realtime(types.Blob(data=audio[offset:offset + 3200], mime_type="audio/pcm;rate=16000"))
        await asyncio.sleep(0.1)
    queue.send_activity_end()


async def run_probe(audio_path: Path | None) -> None:
    """Verify input and tool-result delivery using the application's Live setup."""
    audio = read_audio(audio_path) if audio_path is not None else None
    model_id = get_app_config().get("live_agent", {}).get("model_id", "gemini-3.8-live")
    agent = Agent(
        name="live_smoke_probe",
        model=DeveloperLiveGemini(model=model_id),
        instruction=(
            "You are a transport test. For the text 'typed input probe', call "
            "report_probe exactly once with label 'typed_input'. For any spoken "
            "audio input, call report_probe exactly once with label 'audio_input'. "
            "Do not call tools before user input. After a successful tool result, "
            "stop. Do not retry successful calls."
        ),
        tools=[report_probe],
    )
    runner = InMemoryRunner(agent=agent, app_name="live_smoke_probe")
    session = await runner.session_service.create_session(app_name=runner.app_name, user_id="smoke")
    queue = PriorityLiveRequestQueue(live_tool_budget=5)
    queue.send_user_input(types.Content(role="user", parts=[types.Part(text="typed input probe")]))
    expected = {"typed_input", "audio_input"} if audio is not None else {"typed_input"}
    completed: set[str] = set()
    audio_task: asyncio.Task[None] | None = None
    events = runner.run_live(
        user_id="smoke", session_id=session.id,
        live_request_queue=queue, run_config=build_run_config(),
    )
    try:
        async for event in events:
            calls = event.get_function_calls()
            if calls:
                queue.record_model_tool_calls(len(calls))
            for response in event.get_function_responses():
                if response.name == "report_probe" and response.response.get("status") == "ok":
                    completed.add(str(response.response["label"]))
                    print(f"{model_id}: {response.response['label']} tool result received", flush=True)
            if event.turn_complete and "typed_input" in completed and audio is not None and audio_task is None:
                audio_task = asyncio.create_task(send_audio(queue, audio))
            if event.turn_complete and expected <= completed:
                print(f"PASS: {model_id} completed input, tool call, tool result, and model turn", flush=True)
                return
        raise RuntimeError(f"Live stream ended before completing probes: {expected - completed}")
    finally:
        if audio_task is not None:
            audio_task.cancel()
            await asyncio.gather(audio_task, return_exceptions=True)
        queue.close()
        await events.aclose()
        await runner.close()


def main() -> None:
    """Load local credentials and run a bounded live smoke test."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, help="Optional 16 kHz mono PCM WAV voice input")
    parser.add_argument("--timeout", type=float, default=60, help="Timeout in seconds")
    args = parser.parse_args()
    asyncio.run(asyncio.wait_for(run_probe(args.audio), timeout=args.timeout))


if __name__ == "__main__":
    main()
