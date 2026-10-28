"""Persistent CEL-Go worker client and GameState integration.

The worker handles complete game operations (package load, state preparation,
multi-step action transitions, contributor view authorization, and declared
error recovery) inside a bounded Go subprocess with cel.CostLimit and parent
deadlines. Python retains schemas, browser export, revision checks, story
receipts, and persistence.
"""
from __future__ import annotations

import atexit
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock, RLock
from time import perf_counter

from pydantic import Field, JsonValue

from game.game_runtime import (
    GameDefinition, GameModel, GameProgram, GameWorkBudgetExceeded,
    MAX_GAME_BYTES, MAX_MODEL_STATE_BYTES, MAX_VALUE_BYTES,
    RenderedNode, load_game_svg_assets,
)
from game.game_source import game_source_json

MAX_FRAME = 4 * 1024 * 1024
DEFAULT_COST_LIMIT = 1_000_000
MAX_CACHED_PACKAGES = 32
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class WorkerResponse(GameModel):
    result: JsonValue
    error: str
    metrics: dict[str, int]


class WorkerInfo(GameModel):
    go_version: str
    cel_go_version: str
    metrics_clock: str


class Prepared(GameModel):
    state: dict[str, JsonValue]
    view: list[RenderedNode]
    model_state: JsonValue
    actions: dict[str, JsonValue]
    candidates: list[dict[str, JsonValue]] = Field(default_factory=list)
    svg_expanded_bytes: int = 0


class GoWorker:
    """One bounded, serial stdio connection with parent-enforced deadlines and CEL cost limits.

    Timeouts kill and reap the worker and discard its response. In-process CEL CostLimit
    aborts runaway expressions cleanly without killing the worker.
    """

    def __init__(
        self,
        executable: Path,
        timeout: float = 5.0,
        cost_limit: int = DEFAULT_COST_LIMIT,
        *,
        serialize_calls: bool = False,
    ) -> None:
        self.executable = executable.resolve()
        self.timeout = timeout
        self.cost_limit = cost_limit
        self.serialize_calls = serialize_calls
        self.process: subprocess.Popen[bytes] | None = None
        self.executor: ThreadPoolExecutor | None = None
        self.lock = RLock() if serialize_calls else Lock()
        self.loaded_packages: set[str] = set()
        self.last_metrics: dict[str, int] = {}
        self.requests = 0
        self.last_elapsed_ms = 0.0
        self.history: list[dict[str, float]] = []
        self.info: WorkerInfo | None = None

    def start(self) -> float:
        if self.process is not None:
            raise RuntimeError("Worker is already started")
        started = perf_counter()
        self.loaded_packages.clear()
        self.process = subprocess.Popen(
            [str(self.executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cel-go-stdio")
        self.info = WorkerInfo.model_validate(self.request({"op": "ping"}))
        return (perf_counter() - started) * 1000

    def ensure_started(self) -> None:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                self.start()

    def close(self) -> None:
        self.loaded_packages.clear()
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=5)
            if self.process.stdin is not None:
                self.process.stdin.close()
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process = None
        if self.executor is not None:
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.executor = None

    def _exchange(self, frame: bytes) -> bytes:
        assert self.process is not None
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(frame)
        self.process.stdin.flush()
        response = self.process.stdout.readline(MAX_FRAME + 1)
        if not response.endswith(b"\n") or len(response) > MAX_FRAME:
            raise ValueError("Worker closed the connection or exceeded the response limit")
        return response

    def request(self, message: dict[str, JsonValue]) -> JsonValue:
        started = perf_counter()
        acquired = (
            self.lock.acquire(timeout=max(0.001, self.timeout))
            if self.serialize_calls
            else self.lock.acquire(blocking=False)
        )
        if not acquired:
            if self.serialize_calls:
                raise GameWorkBudgetExceeded("CEL-Go operation exceeded its parent deadline waiting for worker lock")
            raise RuntimeError("Worker already has an operation; queueing is disabled")
        self.last_metrics = {}
        try:
            if self.process is None or self.executor is None:
                raise RuntimeError("Start and load a worker before evaluating")
            message = {
                "cost_limit": max(1, self.cost_limit),
                **message,
                "timeout_ms": max(1, int(self.timeout * 1000)),
            }
            frame = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(frame) >= MAX_FRAME:
                raise ValueError("Worker request exceeds the frame limit")
            self.requests += 1
            future = self.executor.submit(self._exchange, frame)
            try:
                data = future.result(timeout=max(0, self.timeout - (perf_counter() - started)))
            except FutureTimeout as exc:
                self.close()
                raise GameWorkBudgetExceeded("CEL-Go operation exceeded its parent deadline") from exc
            except (OSError, ValueError):
                self.close()
                raise
            response = WorkerResponse.model_validate_json(data)
            self.last_metrics = response.metrics
            if perf_counter() - started > self.timeout or "context deadline exceeded" in response.error:
                self.close()
                raise GameWorkBudgetExceeded("CEL-Go operation exceeded its parent deadline")
            if "actual cost limit exceeded" in response.error:
                raise GameWorkBudgetExceeded("CEL-Go operation exceeded its cost limit")
            if response.error:
                raise ValueError(response.error)
            return response.result
        finally:
            self.last_elapsed_ms = (perf_counter() - started) * 1000
            self.history.append({"rpc_ms": self.last_elapsed_ms, **{
                name.removesuffix("_ns") + "_ms" if name.endswith("_ns") else name:
                value / 1_000_000 if name.endswith("_ns") else float(value)
                for name, value in self.last_metrics.items()
            }})
            self.lock.release()

    def load(self, program: GameProgram, *, package: str = "") -> Prepared:
        message: dict[str, JsonValue] = {
            "op": "load",
            "definition": program.definition.model_dump(mode="json"),
            "sources": program.dependencies,
            "random": sorted(program.random_expressions),
            "assets": {name: node.model_dump(mode="json") for name, node in (program.svg_assets or {}).items()},
        }
        if package:
            message["package"] = package
        prepared = Prepared.model_validate(self.request(message))
        if package:
            if len(self.loaded_packages) >= MAX_CACHED_PACKAGES:
                self.loaded_packages.clear()
            self.loaded_packages.add(package)
        return prepared


def find_go_worker_executable() -> Path | None:
    configured = os.environ.get("NARRATRON_CEL_GO_WORKER", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate.resolve()
    binary_name = "cel-go-worker.exe" if sys.platform == "win32" else "cel-go-worker"
    for candidate in (
        PROJECT_ROOT / "scratch" / binary_name,
        PROJECT_ROOT / "game" / "cel_go_worker" / binary_name,
        Path("/usr/local/bin/cel-go-worker"),
    ):
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which("cel-go-worker")
    if found is not None:
        return Path(found).resolve()
    return None


_shared_worker_lock = Lock()
_shared_worker: GoWorker | None = None


def shared_go_worker() -> GoWorker | None:
    global _shared_worker
    with _shared_worker_lock:
        if _shared_worker is not None:
            return _shared_worker
        executable = find_go_worker_executable()
        if executable is None:
            return None
        _shared_worker = GoWorker(executable, serialize_calls=True)
        atexit.register(_shared_worker.close)
        return _shared_worker


def validate_prepared(program: GameProgram, prepared: Prepared) -> list[JsonValue]:
    for state in [*prepared.candidates, prepared.state]:
        if len(json.dumps(state, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Game state exceeds its size budget.")
        program.state_validator.validate(state)
    view: list[JsonValue] = [node.model_dump(mode="json") for node in prepared.view]
    view_bytes = len(json.dumps(view, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    if view_bytes - prepared.svg_expanded_bytes > MAX_VALUE_BYTES:
        raise ValueError("Rendered view exceeds its size budget.")
    if (
        program.definition.model_state is not None
        and len(json.dumps(prepared.model_state, ensure_ascii=False).encode("utf-8")) > MAX_MODEL_STATE_BYTES
    ):
        raise ValueError("Model-facing game state exceeds its size budget.")
    if len(json.dumps(prepared.actions, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Model-facing game actions exceed their size budget.")
    return view


def load_go_package(
    worker: GoWorker,
    content: bytes,
    *,
    filename: str = "game.json",
    game_dir: Path | None = None,
    package_key: str = "",
    auto_start: bool = False,
) -> GameProgram:
    if len(content) > MAX_GAME_BYTES:
        raise ValueError("Game definition exceeds size limit.")
    normalized = game_source_json(content, filename)
    if len(normalized) > MAX_GAME_BYTES:
        raise ValueError("Game definition exceeds size limit after decoding.")
    definition = GameDefinition.model_validate_json(normalized)
    program = GameProgram(definition, load_game_svg_assets(game_dir) if game_dir is not None else None)
    if auto_start:
        worker.ensure_started()
    prepared = worker.load(program, package=package_key)
    program.initial_view = validate_prepared(program, prepared)
    program.initial_model_state = prepared.model_state
    program.initial_narratron_actions = prepared.actions
    return program


from game.game_state import GameState  # noqa: E402


class GoGameState(GameState):
    """GameState bound to the CEL-Go worker backend."""

    def __init__(
        self,
        directory: Path,
        persist: Callable[[], None],
        notify: Callable[..., None],
        *,
        worker: GoWorker | None = None,
    ) -> None:
        super().__init__(directory, persist, notify, worker=worker, backend="go")
