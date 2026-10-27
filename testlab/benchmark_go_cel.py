"""Compare Python game transactions with a persistent CEL-Go worker.

Build the binary first; run with uv run python -m testlab.benchmark_go_cel.
Only trusted fixtures: this prototype has no OS memory containment.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from importlib.metadata import version
import json
from pathlib import Path
import platform
from statistics import median
from typing import Literal

from pydantic import JsonValue

from game.game_state import GameState
from testlab.game_benchmark import sample
from game.go_cel import GoGameState, GoWorker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, default=Path("scratch/cel-go-worker.exe" if platform.system() == "Windows" else "scratch/cel-go-worker"))
    parser.add_argument("--game", type=Path, default=Path("adventures/example_adventure"))
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if not args.worker.is_file():
        parser.error("Build game/cel_go_worker first and provide --worker")
    backends: list[Literal["python", "go"]] = ["python", "go"]
    timings: dict[str, list[dict[str, float]]] = {backend: [] for backend in backends}
    profiles: list[list[dict[str, float]]] = []
    startup: list[float] = []
    payload_bytes: JsonValue = None
    runtime_info: dict[str, JsonValue] = {}
    for round_index in range(args.rounds):
        results: dict[str, dict[str, JsonValue]] = {}
        # Rotate execution order to reduce a consistent warm-cache/order bias.
        for backend in backends[round_index % len(backends):] + backends[:round_index % len(backends)]:
            if backend != "go":
                elapsed, result = sample(args.game)
            else:
                worker = GoWorker(args.worker)
                try:
                    startup.append(worker.start())
                    assert worker.info is not None
                    runtime_info = worker.info.model_dump(mode="json")

                    def factory(directory: Path, persist: Callable[[], None], notify: Callable[..., None]) -> GameState:
                        return GoGameState(directory, persist, notify, worker=worker)

                    before = len(worker.history)
                    elapsed, result = sample(args.game, factory)
                    assert worker.requests == 5, "Expected ping, load and one RPC per action"
                    profiles.append(worker.history[before:])
                finally:
                    worker.close()
            timings[backend].append(elapsed)
            results[backend] = result
        for backend, result in results.items():
            assert result == results["python"], f"{backend} transaction outputs differ from Python"
        payload_bytes = results["python"]["payload_bytes"]
    names = ["load", "player_with_draft", "narratron", "invalid_move"]
    report = {
        "platform": platform.platform(), "python": platform.python_version(),
        "cel_python": version("cel-python"), "worker": runtime_info,
        "rounds": args.rounds, "parity": True, "payload_bytes": payload_bytes,
        "parent_timeout_seconds": 5, "os_memory_limit_enforced": False,
        "declared_error_recovery_supported": False,
        "worker_startup_median_ms": median(startup),
        "median_ms": {backend: {name: median(row[name] for row in rows) for name in names} for backend, rows in timings.items()},
        "go_profile_medians": {
            name: {key: median(profile[i][key] for profile in profiles) for key in profiles[0][i]}
            for i, name in enumerate(names)
        },
        "round_ms": timings,
        "go_profile_rounds": profiles,
        "worker_startup_round_ms": startup,
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
