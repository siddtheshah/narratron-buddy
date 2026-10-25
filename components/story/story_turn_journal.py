"""Durable bounded turn scheduling, independent of note content revisions."""

from pathlib import Path
import sqlite3
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    position: int = 0
    ingress: Literal["communicated_action", "game_action"]
    entry_role: Literal["recorder", "responder"] | None = None
    action: str
    action_logged: bool = False
    nudge: str = ""
    source_revision: int = 0
    game_payload: dict[str, JsonValue] = Field(default_factory=dict)
    response: dict[str, JsonValue] | None = None
    recorder_completed: bool = False
    responder_skipped: bool = False
    narration_reason: str | None = None
    narrative_brief: str | None = None
    planner_completed: bool = False  # Legacy save marker; planning now has its own journal.
    # Acknowledges durable staging; the output cycle may still defer or supersede it.
    delivered: bool = False
    cycle_id: str | None = None
    response_owner_id: str | None = None

    @model_validator(mode="after")
    def validate_entry_role(self) -> "StoryTurn":
        expected = "recorder" if self.ingress == "game_action" else "responder"
        if self.entry_role is not None and self.entry_role != expected:
            raise ValueError("Entry role does not match the accepted ingress.")
        self.entry_role = expected
        return self

    @property
    def foreground_ready(self) -> bool:
        return self.recorder_completed and (self.response is not None or self.responder_skipped or self.response_owner_id is not None)

    @property
    def completed(self) -> bool:
        return self.foreground_ready


class StoryTurnJournal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA synchronous = FULL")
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS story_turns (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id TEXT NOT NULL UNIQUE,
                    turn_json TEXT NOT NULL
                )"""
            )

    def accept(self, turn: StoryTurn) -> StoryTurn:
        with self._lock, self._connection:
            existing = self.get(turn.turn_id)
            if existing is not None:
                if (existing.ingress, existing.action, existing.nudge, existing.game_payload) != (
                    turn.ingress, turn.action, turn.nudge, turn.game_payload,
                ):
                    raise ValueError("Turn identity reused with different ingress.")
                return existing
            cursor = self._connection.execute(
                "INSERT INTO story_turns (turn_id, turn_json) VALUES (?, ?)",
                (turn.turn_id, turn.model_dump_json()),
            )
            assert cursor.lastrowid is not None
            turn.position = cursor.lastrowid
            self.save(turn)
            return turn

    def get(self, turn_id: str) -> StoryTurn | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT turn_json FROM story_turns WHERE turn_id = ?", (turn_id,),
            ).fetchone()
        return None if row is None else StoryTurn.model_validate_json(row[0])

    def save(self, turn: StoryTurn) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE story_turns SET turn_json = ? WHERE turn_id = ?",
                (turn.model_dump_json(), turn.turn_id),
            )

    def turns(self) -> list[StoryTurn]:
        with self._lock:
            rows = self._connection.execute("SELECT turn_json FROM story_turns ORDER BY position").fetchall()
        return [StoryTurn.model_validate_json(row[0]) for row in rows]

    def bind_cycle(self, turn_ids: list[str]) -> None:
        """Freeze membership before any model runs, including across restarts."""
        with self._lock, self._connection:
            for turn_id in turn_ids:
                turn = self.get(turn_id)
                assert turn is not None
                if turn.cycle_id is None:
                    turn.cycle_id = turn_ids[0]
                    self._connection.execute(
                        "UPDATE story_turns SET turn_json = ? WHERE turn_id = ?",
                        (turn.model_dump_json(), turn.turn_id),
                    )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
