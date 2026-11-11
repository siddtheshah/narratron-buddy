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
from components.story.story_recording_journal import DEFAULT_HISTORY_EVENT_LIMIT, RecordedNotes


logger = logging.getLogger(__name__)


class BackgroundPlanning:
    def __init__(
        self, path: Path, planner: StoryPlanningModule, notepad: Notepad,
        game_snapshot: Callable[[], dict[str, JsonValue] | None],
        *, context_event_limit: int = DEFAULT_HISTORY_EVENT_LIMIT,
    ) -> None:
        self.planner = planner
        self.notepad = notepad
        self.game_snapshot = game_snapshot
        self.context_event_limit = max(1, context_event_limit)
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
            if self._closed or self.planner.token_budget_per_event == 0:
                logger.debug(
                    "[BackgroundPlanning] Skipping enqueue for %s (closed=%s, budget=%d)",
                    event_id,
                    self._closed,
                    self.planner.token_budget_per_event,
                )
                return
            try:
                with self._connection:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO story_planning_updates (event_id, payload) VALUES (?, ?)",
                        (event_id, json.dumps(payload, ensure_ascii=False)),
                    )
                logger.debug(
                    "[BackgroundPlanning] Enqueued update %s (kind=%s)",
                    event_id,
                    payload.get("kind"),
                )
                self._start_locked()
            except sqlite3.Error:
                # The turn journal can reconstruct this update on retry/restart.
                logger.exception("Could not queue background story update %s", event_id)

    def retry(self) -> None:
        logger.debug("[BackgroundPlanning] retry() called")
        with self._condition:
            self._start_locked()

    def _pending_locked(self, after: int = 0) -> list[tuple[int, str]]:
        # Coalesce an old backlog into its newest window. The current snapshot
        # and completion journal supply established state, not a replay of every
        # update missed while the planner was unavailable.
        rows = self._connection.execute(
            "SELECT position, payload FROM story_planning_updates WHERE completed = 0 AND position > ? ORDER BY position DESC LIMIT ?",
            (after, self.context_event_limit),
        ).fetchall()
        return list(reversed(rows))

    def _start_locked(self) -> None:
        pending = self._pending_locked() if not (self._active or self._closed or self.planner.token_budget_per_event == 0) else []
        if self._active or self._closed or self.planner.token_budget_per_event == 0 or not pending:
            return
        self._active = True
        logger.debug("[BackgroundPlanning] Starting planner worker (%d pending row(s))", len(pending))
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
        logger.debug(
            "[BackgroundPlanning] Supplying %d mid-invocation update(s) after pos=%d",
            len(rows),
            self._through,
        )
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
        logger.debug(
            "[BackgroundPlanning] Committed planner notes at rev=%d through pos=%d",
            snapshot.revision,
            self._through,
        )

    def _run(self) -> None:
        failed = False
        logger.debug("[BackgroundPlanning] Worker loop entered")
        try:
            while True:
                for attempt in range(3):
                    with self._condition:
                        rows = self._pending_locked()
                    if not rows:
                        logger.debug("[BackgroundPlanning] No pending updates left; worker exiting")
                        return
                    handoff = self._context(rows)
                    logger.debug(
                        "[BackgroundPlanning] Planning attempt %d/3 for %d row(s) through pos=%d (source_rev=%d)",
                        attempt + 1,
                        len(rows),
                        self._through,
                        self._source.revision,
                    )
                    updates = self.planner.plan_story_updates(handoff, self._source)
                    logger.debug(
                        "[BackgroundPlanning] Planner produced %d update(s): %s",
                        len(updates),
                        list(updates.keys()),
                    )
                    try:
                        if self.game_snapshot() != self._game_source:
                            raise StaleNoteSnapshot("Game state changed during planning.")
                        self.notepad.publish_role_updates(
                            "planner", updates, source=self._source, persist=self._commit,
                        )
                        break
                    except StaleNoteSnapshot as exc:
                        logger.warning(
                            "[BackgroundPlanning] StaleNoteSnapshot on attempt %d/3 (through=%d): %s",
                            attempt + 1,
                            self._through,
                            exc,
                        )
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
        if self.planner.token_budget_per_event == 0:
            return True
        logger.debug("[BackgroundPlanning] wait() started (timeout=%.1fs)", timeout)
        with self._condition:
            idle = self._condition.wait_for(lambda: not self._active, timeout)
            pending = self._pending_locked()
            done = idle and not pending
        if not done:
            logger.warning(
                "[BackgroundPlanning] wait() returning False (idle=%s, pending_count=%d)",
                idle,
                len(pending),
            )
        else:
            logger.debug("[BackgroundPlanning] wait() completed")
        return done

    def close(self) -> None:
        logger.debug("[BackgroundPlanning] Closing background planning")
        with self._condition:
            self._closed = True
        self._executor.shutdown(wait=True)
        if self.planner.story_session is not None:
            self.planner.story_session.planner_context = None
        self._connection.close()
        logger.debug("[BackgroundPlanning] Closed background planning")
