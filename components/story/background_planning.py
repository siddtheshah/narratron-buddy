"""Durable story updates consumed by a planner independently of canvas cycles."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import sqlite3
from threading import Condition
from typing import Callable

from pydantic import JsonValue

from components.notepad import NoteSnapshot, Notepad, StaleNoteSnapshot
from components.story.story_planning_module import StoryPlanningModule
from components.story.story_recording_journal import RecordedNotes


logger = logging.getLogger(__name__)


class BackgroundPlanning:
    def __init__(
        self, path: Path, planner: StoryPlanningModule, notepad: Notepad,
        game_snapshot: Callable[[], dict[str, JsonValue] | None],
    ) -> None:
        self.planner = planner
        self.notepad = notepad
        self.game_snapshot = game_snapshot
        self._condition = Condition()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA synchronous = FULL")
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS story_planning_updates (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0
                )"""
            )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-planning")
        self._active = False
        self._closed = False
        self._through = 0
        self._source = notepad.snapshot()
        self._game_source: dict[str, JsonValue] | None = None
        if planner.story_session is not None:
            planner.story_session.planner_context = self._new_context

    def enqueue(self, event_id: str, payload: dict[str, JsonValue]) -> None:
        with self._condition:
            if self._closed:
                return
            try:
                with self._connection:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO story_planning_updates (event_id, payload) VALUES (?, ?)",
                        (event_id, json.dumps(payload, ensure_ascii=False)),
                    )
                self._start_locked()
            except sqlite3.Error:
                # The turn journal can reconstruct this update on retry/restart.
                logger.exception("Could not queue background story update %s", event_id)

    def retry(self) -> None:
        with self._condition:
            self._start_locked()

    def _pending_locked(self, after: int = 0) -> list[tuple[int, str]]:
        return self._connection.execute(
            "SELECT position, payload FROM story_planning_updates WHERE completed = 0 AND position > ? ORDER BY position",
            (after,),
        ).fetchall()

    def _start_locked(self) -> None:
        if self._active or self._closed or not self._pending_locked():
            return
        self._active = True
        self._executor.submit(self._run)

    def _context(self, rows: list[tuple[int, str]]) -> dict[str, JsonValue]:
        self._through = rows[-1][0]
        self._source = self.notepad.snapshot()
        self._game_source = self.game_snapshot()
        return {
            "story_updates": [json.loads(payload) for _, payload in rows],
            "source_revision": self._source.revision,
            "sticky_notes": self._source.notes,
            "game_state": self._game_source,
            "instruction": "Reconcile all staged plans with these latest updates. Player intentions are not committed outcomes.",
        }

    def _new_context(self) -> str:
        """Called between model requests, including tool continuations."""
        with self._condition:
            rows = self._pending_locked(self._through)
        if not rows:
            return ""
        self.planner.reset_story_budget()
        return json.dumps(self._context(rows), ensure_ascii=False)

    def _commit(self, snapshot: NoteSnapshot) -> None:
        with self._condition, self._connection:
            self._connection.execute(
                """INSERT INTO recorded_notes (singleton, snapshot_json) VALUES (1, ?)
                   ON CONFLICT(singleton) DO UPDATE SET snapshot_json = excluded.snapshot_json""",
                (RecordedNotes.from_snapshot(snapshot).model_dump_json(),),
            )
            self._connection.execute(
                "UPDATE story_planning_updates SET completed = 1 WHERE position <= ?", (self._through,),
            )

    def _run(self) -> None:
        failed = False
        try:
            while True:
                for attempt in range(3):
                    with self._condition:
                        rows = self._pending_locked()
                    if not rows:
                        return
                    handoff = self._context(rows)
                    updates = self.planner.plan_story_updates(handoff, self._source)
                    try:
                        if self.game_snapshot() != self._game_source:
                            raise StaleNoteSnapshot("Game state changed during planning.")
                        self.notepad.publish_role_updates(
                            "planner", updates, source=self._source, persist=self._commit,
                        )
                        break
                    except StaleNoteSnapshot:
                        if attempt == 2:
                            raise
        except Exception:
            failed = True
            logger.exception("Background story planning remains pending")
        finally:
            with self._condition:
                self._active = False
                if not failed or self._pending_locked(self._through):
                    self._start_locked()
                self._condition.notify_all()

    def wait(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: not self._active, timeout) and not self._pending_locked()

    def close(self) -> None:
        with self._condition:
            self._closed = True
        self._executor.shutdown(wait=True)
        if self.planner.story_session is not None:
            self.planner.story_session.planner_context = None
        self._connection.close()
