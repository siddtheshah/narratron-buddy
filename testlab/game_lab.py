"""HTTP entry points for the isolated game harness."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from time import monotonic
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field, JsonValue

from providers import get_text_response_provider, list_text_response_provider_specs
from components.canvas.game_runtime import GameAction, MAX_GAME_BYTES, validate_game_package
from components.canvas.game_state import GameState
from providers.text_response_provider import TextResponseProvider
from testlab.game_harness import (
    DEFAULT_GAME_MODEL, GameModel, GameReport, GameScenario, Move, PlayerStep, TurnTrace,
    no_notify, no_persist, play_turn, run_game_test,
)

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
router = APIRouter()


class GameTestRequest(GameModel):
    game_path: str = Field(min_length=1)
    scenario: GameScenario
    provider_id: str = "gemini-3"
    model: str | None = DEFAULT_GAME_MODEL


class GameCatalog(GameModel):
    games: list[str]
    providers: list[dict[str, JsonValue]]


class GameSessionRequest(GameModel):
    game_path: str = Field(min_length=1)
    provider_id: str = "gemini-3"
    model: str | None = DEFAULT_GAME_MODEL
    max_ai_actions: int = Field(default=8, ge=1, le=32)
    ai_play: bool = Field(default=True)


class SessionUpdateRequest(GameModel):
    ai_play: bool | None = None


class GameSession:
    def __init__(self, path: Path, provider: TextResponseProvider, config: GameSessionRequest) -> None:
        with path.open("rb") as source:
            content = source.read(MAX_GAME_BYTES + 1)
        validate_game_package(content)
        self.directory: TemporaryDirectory[str] = TemporaryDirectory(prefix="narratron-manual-game-")
        try:
            root = Path(self.directory.name)
            (root / "game").mkdir()
            (root / "game/game.json").write_bytes(content)
            for svg_file in path.parent.rglob("*.svg"):
                if svg_file.is_file():
                    target_svg = (root / "game") / svg_file.relative_to(path.parent)
                    target_svg.parent.mkdir(parents=True, exist_ok=True)
                    target_svg.write_bytes(svg_file.read_bytes())
            self.game = GameState(root, no_persist, no_notify)
            if self.game.program is None:
                raise ValueError(self.game.error)
        except Exception:
            self.directory.cleanup()
            raise
        self.provider = provider
        self.config = config
        self.turns: list[TurnTrace] = []
        self.lock = RLock()
        self.touched = monotonic()

    def snapshot(self, session_id: str) -> dict[str, JsonValue]:
        return {
            "session_id": session_id,
            "game": self.game.payload(),
            "state": self.game.story_snapshot("narratron"),
            "turns": [turn.model_dump(mode="json") for turn in self.turns],
            "ai_play": self.config.ai_play,
        }


_sessions: dict[str, GameSession] = {}
_sessions_lock = RLock()


def game_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    if not path.is_relative_to(PROJECT_ROOT) or path.name != "game.json" or not path.is_file():
        raise HTTPException(400, "Choose a workspace game.json file.")
    return path


def get_session(session_id: str) -> GameSession:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Game session expired. Load the game again.")
        session.touched = monotonic()
        return session


@router.get("/game-lab-assets/{kind}/{name}", include_in_schema=False)
def game_renderer_asset(kind: str, name: str) -> FileResponse:
    if (kind, name) not in (("js", "game-renderer.js"), ("js", "game-runtime.js"), ("css", "game-renderer.css")):
        raise HTTPException(404, "Unknown game asset.")
    return FileResponse(
        PROJECT_ROOT / "static" / kind / name,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.post("/api/game-lab/sessions")
def create_game_session(body: GameSessionRequest) -> dict[str, JsonValue]:
    path = game_path(body.game_path)
    try:
        provider = get_text_response_provider(body.provider_id)
        session = GameSession(path, provider, body)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    with _sessions_lock:
        for key, old in list(_sessions.items()):
            if monotonic() - old.touched > 3600 and old.lock.acquire(blocking=False):
                try:
                    old.directory.cleanup()
                    del _sessions[key]
                finally:
                    old.lock.release()
        if len(_sessions) >= 64:
            session.directory.cleanup()
            raise HTTPException(429, "Close an existing game session before loading another.")
        session_id = uuid4().hex
        _sessions[session_id] = session
    return session.snapshot(session_id)


@router.get("/api/game-lab/sessions/{session_id}")
def read_game_session(session_id: str) -> dict[str, JsonValue]:
    session = get_session(session_id)
    with session.lock:
        return session.snapshot(session_id)


@router.patch("/api/game-lab/sessions/{session_id}")
def update_game_session(session_id: str, body: SessionUpdateRequest) -> dict[str, JsonValue]:
    session = get_session(session_id)
    with session.lock:
        if body.ai_play is not None:
            session.config.ai_play = body.ai_play
        return session.snapshot(session_id)


@router.delete("/api/game-lab/sessions/{session_id}")
def close_game_session(session_id: str) -> dict[str, bool]:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is not None:
            with session.lock:
                session.directory.cleanup()
                del _sessions[session_id]
    return {"closed": True}


@router.post("/api/game-lab/sessions/{session_id}/action")
def game_session_action(
    session_id: str,
    action: GameAction,
    ai_play: bool | None = None,
    actor: Literal["player", "contributor", "narratron"] = "player",
) -> dict[str, JsonValue]:
    session = get_session(session_id)
    with session.lock:
        if action.revision != session.game.revision:
            raise HTTPException(409, "The game changed. Refresh and try again.")
        effective_ai = session.config.ai_play if ai_play is None else ai_play
        if not effective_ai or actor == "narratron":
            before = json.loads(json.dumps(session.game.state))
            try:
                session.game.act(action, actor=actor)
            except Exception as exc:
                session.game.report_error(str(exc))
                raise HTTPException(400, str(exc)) from exc
            trace = TurnTrace(
                player=Move(name=action.name, arguments=action.arguments),
                before=before,
                after_player=json.loads(json.dumps(session.game.state)),
                after=json.loads(json.dumps(session.game.state)),
            )
            session.turns.append(trace)
            session.touched = monotonic()
            return session.game.payload() or {}

        trace = play_turn(
            session.game, PlayerStep(move=Move(name=action.name, arguments=action.arguments)),
            session.provider, session.config.model or DEFAULT_GAME_MODEL, session.config.max_ai_actions, action,
        )
        session.turns.append(trace)
        session.touched = monotonic()
        if trace.errors:
            session.game.report_error(trace.errors[0])
            if not trace.after_player:
                raise HTTPException(400, trace.errors[0])
        # Match the canvas renderer's action-response contract. Diagnostics use GET.
        return session.game.payload() or {}


@router.get("/game-lab", include_in_schema=False)
def game_lab_page() -> FileResponse:
    return FileResponse(
        ROOT / "game_lab.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/api/game-lab/catalog")
def game_lab_catalog() -> GameCatalog:
    games = sorted(
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for folder in (PROJECT_ROOT / "adventures", PROJECT_ROOT / "theaters")
        for path in folder.glob("*/game/game.json")
        if path.resolve().is_relative_to(PROJECT_ROOT)
    )
    return GameCatalog(games=games, providers=list_text_response_provider_specs())


@router.post("/api/game-lab/run", response_model=GameReport)
def run_game_lab(body: GameTestRequest) -> GameReport:
    path = game_path(body.game_path)
    try:
        provider = get_text_response_provider(body.provider_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_game_test(path, body.scenario, provider, body.model or DEFAULT_GAME_MODEL)
