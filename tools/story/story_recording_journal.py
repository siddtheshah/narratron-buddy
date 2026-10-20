"""Durable pending events and atomic factual projection commits."""

from pathlib import Path
import sqlite3
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from components.notepad import NoteSnapshot
from tools.story.story_handoffs import CommittedGameReceipt, GameControlDecision


class RecordingEvent(BaseModel):
    """An application-created handoff; routing is never taken from model prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    source_event_ids: tuple[str, ...] = ()
    kind: Literal["completed_response", "committed_game_action"]
    source_revision: int = Field(ge=0)
    payload: dict[str, JsonValue]


class RecordedNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    schema_token: str
    notes: dict[str, str]
    structured_notes: dict[str, str | dict[str, str]]

    @classmethod
    def from_snapshot(cls, snapshot: NoteSnapshot) -> "RecordedNotes":
        return cls(
            revision=snapshot.revision,
            schema_token=snapshot.schema_token,
            notes=snapshot.notes,
            structured_notes=snapshot.structured_notes,
        )

    def snapshot(self) -> NoteSnapshot:
        return NoteSnapshot(
            revision=self.revision,
            schema_token=self.schema_token,
            notes=dict(self.notes),
            structured_notes={
                topic: value.copy() if type(value) is dict else value
                for topic, value in self.structured_notes.items()
            },
        )


class StoryRecordingJournal:
    """One SQLite transaction commits note state and the consumed event together.

    This journal records already-committed game receipts. It does not execute
    game actions or claim atomicity with the game's separate persistence store.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA synchronous = FULL")
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS recording_events (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    recorded_revision INTEGER
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS recorded_notes (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    snapshot_json TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS game_control_receipts (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    parent_event_id TEXT NOT NULL,
                    game_revision INTEGER NOT NULL,
                    receipt_json TEXT NOT NULL,
                    UNIQUE (parent_event_id, game_revision)
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS game_control_decisions (
                    parent_event_id TEXT PRIMARY KEY,
                    decision_json TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def enqueue(self, event: RecordingEvent) -> None:
        """Persist before reconciliation; retries must retain the exact identity."""
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT event_json FROM recording_events WHERE event_id = ?", (event.event_id,),
            ).fetchone()
            if row is not None:
                if RecordingEvent.model_validate_json(row[0]) != event:
                    raise ValueError("Event identity was reused with different content.")
                return
            self._connection.execute(
                "INSERT INTO recording_events (event_id, event_json) VALUES (?, ?)",
                (event.event_id, event.model_dump_json()),
            )

    def pending(self) -> list[RecordingEvent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM recording_events WHERE recorded_revision IS NULL ORDER BY position",
            ).fetchall()
        return [RecordingEvent.model_validate_json(row[0]) for row in rows]

    def recorded_revision(self, event_id: str) -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT recorded_revision FROM recording_events WHERE event_id = ?", (event_id,),
            ).fetchone()
        return None if row is None else row[0]

    def latest_snapshot(self) -> NoteSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot_json FROM recorded_notes WHERE singleton = 1",
            ).fetchone()
        return None if row is None else RecordedNotes.model_validate_json(row[0]).snapshot()

    def commit(self, event_id: str, snapshot: NoteSnapshot) -> None:
        """Commit only the oldest pending event; a no-change event still advances progress."""
        encoded = RecordedNotes.from_snapshot(snapshot).model_dump_json()
        with self._lock, self._connection:
            # Acquire the write lock before reading progress, including across connections.
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """SELECT event_id FROM recording_events
                   WHERE recorded_revision IS NULL ORDER BY position LIMIT 1"""
            ).fetchone()
            if row is None or row[0] != event_id:
                raise ValueError("Recording must commit the oldest pending event.")
            self._connection.execute(
                """INSERT INTO recorded_notes (singleton, snapshot_json) VALUES (1, ?)
                   ON CONFLICT(singleton) DO UPDATE SET snapshot_json = excluded.snapshot_json""",
                (encoded,),
            )
            self._connection.execute(
                "UPDATE recording_events SET recorded_revision = ? WHERE event_id = ?",
                (snapshot.revision, event_id),
            )

    @staticmethod
    def receipt_recording_event(receipt: CommittedGameReceipt) -> RecordingEvent:
        return RecordingEvent(
            event_id=receipt.event_id,
            action_id=receipt.action_id,
            source_event_ids=(receipt.parent_event_id,),
            kind="committed_game_action",
            source_revision=receipt.source_revision,
            payload={
                "actor": "narratron",
                "action": receipt.action.model_dump(mode="json"),
                "receipt": receipt.receipt,
            },
        )

    def record_narratron_receipt(self, receipt: CommittedGameReceipt) -> RecordingEvent:
        """Persist a committed Narratron game receipt and enqueue its note projection."""
        event = self.receipt_recording_event(receipt)
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """SELECT receipt_json FROM game_control_receipts
                   WHERE parent_event_id = ? AND game_revision = ?""",
                (receipt.parent_event_id, receipt.action.revision),
            ).fetchone()
            if existing is not None:
                if CommittedGameReceipt.model_validate_json(existing[0]) != receipt:
                    raise ValueError(
                        "Game receipt revision was reused with different content."
                    )
                return event
            decision_row = self._connection.execute(
                "SELECT 1 FROM game_control_decisions WHERE parent_event_id = ?",
                (receipt.parent_event_id,),
            ).fetchone()
            if decision_row is not None:
                raise ValueError(
                    "Game control decision is already finalized for this event."
                )
            by_id = self._connection.execute(
                "SELECT receipt_json FROM game_control_receipts WHERE event_id = ?",
                (receipt.event_id,),
            ).fetchone()
            if by_id is not None:
                if CommittedGameReceipt.model_validate_json(by_id[0]) != receipt:
                    raise ValueError(
                        "Game receipt identity was reused with different content."
                    )
                return event
            self._connection.execute(
                """INSERT INTO game_control_receipts
                   (event_id, parent_event_id, game_revision, receipt_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    receipt.event_id,
                    receipt.parent_event_id,
                    receipt.action.revision,
                    receipt.model_dump_json(),
                ),
            )
            self._connection.execute(
                "INSERT INTO recording_events (event_id, event_json) VALUES (?, ?)",
                (event.event_id, event.model_dump_json()),
            )
        return event

    def narratron_receipt_for_revision(
        self, parent_event_id: str, game_revision: int
    ) -> CommittedGameReceipt | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT receipt_json FROM game_control_receipts
                   WHERE parent_event_id = ? AND game_revision = ?""",
                (parent_event_id, game_revision),
            ).fetchone()
        return None if row is None else CommittedGameReceipt.model_validate_json(row[0])

    def narratron_receipts(self, parent_event_id: str) -> list[CommittedGameReceipt]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT receipt_json FROM game_control_receipts
                   WHERE parent_event_id = ? ORDER BY position""",
                (parent_event_id,),
            ).fetchall()
        return [CommittedGameReceipt.model_validate_json(row[0]) for row in rows]

    def save_game_control_decision(
        self, parent_event_id: str, decision: GameControlDecision
    ) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT decision_json FROM game_control_decisions WHERE parent_event_id = ?",
                (parent_event_id,),
            ).fetchone()
            if row is not None:
                if GameControlDecision.model_validate_json(row[0]) != decision:
                    raise ValueError(
                        "Game control decision was already saved with different content."
                    )
                return
            self._connection.execute(
                """INSERT INTO game_control_decisions
                   (parent_event_id, decision_json, completed) VALUES (?, ?, 0)""",
                (parent_event_id, decision.model_dump_json()),
            )

    def game_control_decision(self, parent_event_id: str) -> GameControlDecision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT decision_json FROM game_control_decisions WHERE parent_event_id = ?",
                (parent_event_id,),
            ).fetchone()
        return None if row is None else GameControlDecision.model_validate_json(row[0])

    def is_game_control_completed(self, parent_event_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT completed FROM game_control_decisions WHERE parent_event_id = ?",
                (parent_event_id,),
            ).fetchone()
        return False if row is None else bool(row[0])

    def complete_game_control(self, parent_event_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            decision_row = self._connection.execute(
                "SELECT 1 FROM game_control_decisions WHERE parent_event_id = ?",
                (parent_event_id,),
            ).fetchone()
            if decision_row is None:
                raise ValueError("Cannot complete game control before saving its decision.")
            unrecorded = self._connection.execute(
                """SELECT 1 FROM recording_events
                   WHERE recorded_revision IS NULL
                     AND (
                         event_id = ?
                         OR event_id IN (
                             SELECT event_id FROM game_control_receipts
                             WHERE parent_event_id = ?
                         )
                     )
                   LIMIT 1""",
                (parent_event_id, parent_event_id),
            ).fetchone()
            if unrecorded is not None:
                raise ValueError(
                    "Cannot complete game control while its factual events remain unrecorded."
                )
            self._connection.execute(
                "UPDATE game_control_decisions SET completed = 1 WHERE parent_event_id = ?",
                (parent_event_id,),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
