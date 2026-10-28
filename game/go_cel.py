"""Offline, trusted-fixture CEL-Go worker experiment; no production runtime flag.

The worker handles a complete operation. Python retains schemas, browser export,
revision checks, receipts and persistence. Declared error recovery and contributor
authorization are deliberately excluded from this first benchmark adapter.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
from pathlib import Path
import subprocess
import sys
from threading import Lock
from time import perf_counter
from typing import Literal
from unittest.mock import patch

from jsonschema import ValidationError as SchemaValidationError
from pydantic import Field, JsonValue

from game.game_runtime import (
    Budget, GameAction, GameDefinition, GameModel, GameProgram, GameStep,
    GameWorkBudgetExceeded, MAX_GAME_BYTES, MAX_MODEL_STATE_BYTES, MAX_VALUE_BYTES,
    RenderedNode, load_game_svg_assets,
)
from game.game_state import GameState, InvalidGameInput
from game.game_source import game_source_json

MAX_FRAME = 4 * 1024 * 1024
DEFAULT_COST_LIMIT = 1_000_000


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


class GoWorker:
    """One bounded, serial stdio connection with parent-enforced deadlines and CEL cost limits.

    Timeouts kill and reap the worker and discard its response. The next explicit
    start followed by load creates a replacement. In-process CEL CostLimit aborts
    runaway expressions cleanly without killing the worker.
    """

    def __init__(self, executable: Path, timeout: float = 5.0, cost_limit: int = DEFAULT_COST_LIMIT) -> None:
        self.executable = executable.resolve()
        self.timeout = timeout
        self.cost_limit = cost_limit
        self.process: subprocess.Popen[bytes] | None = None
        self.executor: ThreadPoolExecutor | None = None
        self.lock = Lock()
        self.last_metrics: dict[str, int] = {}
        self.requests = 0
        self.last_elapsed_ms = 0.0
        self.history: list[dict[str, float]] = []
        self.info: WorkerInfo | None = None

    def start(self) -> float:
        if self.process is not None:
            raise RuntimeError("Worker is already started")
        started = perf_counter()
        self.process = subprocess.Popen(
            [str(self.executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cel-go-stdio")
        self.info = WorkerInfo.model_validate(self.request({"op": "ping"}))
        return (perf_counter() - started) * 1000

    def close(self) -> None:
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
        if not self.lock.acquire(blocking=False):
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

    def load(self, program: GameProgram) -> Prepared:
        message: dict[str, JsonValue] = {
            "op": "load", "definition": program.definition.model_dump(mode="json"),
            "sources": program.dependencies, "random": sorted(program.random_expressions),
            "assets": {name: node.model_dump(mode="json") for name, node in (program.svg_assets or {}).items()},
        }
        return Prepared.model_validate(self.request(message))


def validate_prepared(program: GameProgram, prepared: Prepared) -> list[JsonValue]:
    for state in [*prepared.candidates, prepared.state]:
        if len(json.dumps(state, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Game state exceeds its size budget")
        program.state_validator.validate(state)
    view: list[JsonValue] = [node.model_dump(mode="json") for node in prepared.view]
    # Conservative: count expanded SVGs too in this offline adapter.
    if len(json.dumps(view, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Rendered view exceeds its size budget")
    if program.definition.model_state is not None and len(json.dumps(prepared.model_state, ensure_ascii=False).encode("utf-8")) > MAX_MODEL_STATE_BYTES:
        raise ValueError("Model-facing game state exceeds its size budget")
    if len(json.dumps(prepared.actions, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Model-facing game actions exceed their size budget")
    return view


class GoGameState(GameState):
    """Reuse the real parent commit/rollback path, swapping only offline compute.

    There is one worker request for an action including all draft steps. Responses
    are stateless proposals; parent replay validates every candidate before the
    existing GameState.act commits anything or invokes its persistence callback.
    """

    def __init__(
        self, directory: Path, persist: Callable[[], None], notify: Callable[..., None],
        *, worker: GoWorker,
    ) -> None:
        self.worker = worker
        self.pending_steps: list[GameStep] = []
        self.pending_actor: Literal["player", "contributor", "narratron"] = "player"
        self.proposal: Prepared | None = None
        self.proposal_index = 0
        self.operation_metrics: dict[str, int] = {}

        def load_package(
            content: bytes, *, filename: str = "game.json", game_dir: Path | None = None,
        ) -> GameProgram:
            normalized = game_source_json(content, filename)
            if len(content) > MAX_GAME_BYTES or len(normalized) > MAX_GAME_BYTES:
                raise ValueError("Game definition exceeds size limit")
            definition = GameDefinition.model_validate_json(normalized)
            if any(t.on_error is not None for t in definition.transitions.values()):
                raise ValueError("The Go benchmark does not implement declared error recovery")
            program = GameProgram(definition, load_game_svg_assets(game_dir) if game_dir is not None else None)
            prepared = worker.load(program)
            program.initial_view = validate_prepared(program, prepared)
            program.initial_model_state = prepared.model_state
            program.initial_narratron_actions = prepared.actions
            return program

        with patch("game.game_state.validate_game_package", load_package):
            super().__init__(directory, persist, notify)
        self.operation_metrics = dict(worker.last_metrics)

    def act(
        self, action: GameAction, actor: Literal["player", "contributor", "narratron"] = "player",
        *, story_event_id: str = "",
    ) -> dict[str, JsonValue] | None:
        if actor == "contributor":
            raise ValueError("Contributor authorization is outside this offline benchmark")
        self.pending_steps = [*action.draft, GameStep(name=action.name, arguments=action.arguments)]
        self.pending_actor = actor
        self.proposal = None
        self.proposal_index = 0
        try:
            return super().act(action, actor, story_event_id=story_event_id)
        finally:
            self.pending_steps = []
            self.proposal = None
            self.operation_metrics = dict(self.worker.last_metrics)

    def _apply_step(
        self, step: GameStep, actor: Literal["player", "contributor", "narratron"],
        *, exploratory: bool = False,
    ) -> None:
        # One deadline for the whole batch; no automatic recovery/retry/reset.
        self._evaluate_step(step, actor, Budget(), exploratory=exploratory)

    def _evaluate_step(
        self, step: GameStep, actor: Literal["player", "contributor", "narratron"], budget: Budget,
        *, exploratory: bool = False,
    ) -> None:
        assert self.program is not None
        if self.proposal is None:
            for index, candidate in enumerate(self.pending_steps):
                transition = self.program.definition.transitions.get(candidate.name)
                if transition is None:
                    raise InvalidGameInput("Unknown game action.")
                if index < len(self.pending_steps) - 1 and (not transition.is_player_actor or transition.triggering):
                    raise InvalidGameInput("Only exploratory actions can precede a turn commitment.")
                if (actor == "narratron") != (transition.actor == "narratron"):
                    raise InvalidGameInput("This actor cannot take that game action.")
                if len(candidate.model_dump_json().encode("utf-8")) > 4096:
                    raise InvalidGameInput("Game action exceeds its size budget.")
                try:
                    self.program.action_validators[candidate.name].validate(candidate.arguments)
                except SchemaValidationError as exc:
                    raise InvalidGameInput(f"Invalid input: {exc.message[:200]}") from exc
            try:
                result = self.worker.request({
                    "op": "act", "state": self.state, "actor": actor,
                    "steps": [candidate.model_dump(mode="json") for candidate in self.pending_steps],
                })
            except ValueError as exc:
                if str(exc) == "This action is not permitted in the current game state.":
                    raise InvalidGameInput(str(exc)) from exc
                raise
            self.proposal = Prepared.model_validate(result)
            validate_prepared(self.program, self.proposal)
            if len(self.proposal.candidates) != len(self.pending_steps):
                raise ValueError("Worker returned an incorrect number of candidates")
            if self.proposal.candidates[-1] != self.proposal.state:
                raise ValueError("Worker prepared a different final state")
        self.state = self.proposal.candidates[self.proposal_index]
        self.proposal_index += 1
        if not exploratory:
            self._view = [node.model_dump(mode="json") for node in self.proposal.view]
            self._model_state = self.proposal.model_state
            self._narratron_actions = self.proposal.actions
