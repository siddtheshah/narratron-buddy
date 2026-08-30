"""Database module for user management, authentication, and theater deployment tracking using PostgreSQL."""

import asyncio
import datetime
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, Dict, Iterable, List, Optional
import logging
import re
from dotenv import load_dotenv
from pricing.pricing_controller import PricingController

load_dotenv()

logger = logging.getLogger(__name__)


class DatabaseConnectionTimeout(TimeoutError):
    """Raised when a live database connection is not available in time."""


class DatabaseOperationTimeout(TimeoutError):
    """Raised after a live database operation exceeds its deadline."""


class _DictCursor:
    """Wraps a DB-API cursor so fetchone/fetchall return dicts keyed by column name.

    pg8000 returns tuples, so this uses cursor.description
    to map tuple positions to column names after each query.
    """

    def __init__(
        self,
        cursor,
        retry_cursor_factory=None,
        operation_timeout: Optional[float] = None,
        timeout_callback=None,
        sql_adapter=None,
    ):
        self._cursor = cursor
        self._retry_cursor_factory = retry_cursor_factory
        self._operation_timeout = operation_timeout
        self._timeout_callback = timeout_callback
        self._sql_adapter = sql_adapter

    def execute(self, sql, params=()):
        try:
            self._execute_with_deadline(sql, params)
        except Exception as exc:
            # A response timeout can leave a remote connection unusable.
            # Retrying reads is safe; retrying a write after an unknown outcome
            # could duplicate a credit charge or another state change.
            if not self._retry_cursor_factory or not _is_retryable_read_error(sql, exc):
                raise
            self._cursor = self._retry_cursor_factory()
            self._execute_with_deadline(sql, params)
        return self

    def _execute_with_deadline(self, sql, params) -> None:
        """Execute a statement and tear down a connection that outlives its deadline."""
        sql = self._sql_adapter(sql) if self._sql_adapter else sql
        self._run_with_deadline(lambda: self._cursor.execute(sql, params))

    def _run_with_deadline(self, operation):
        """Run one remote database call within its deadline."""
        if self._operation_timeout is None:
            return operation()

        expired = threading.Event()

        def _expire() -> None:
            expired.set()
            if self._timeout_callback:
                try:
                    self._timeout_callback()
                except Exception:
                    logger.warning("Failed to close a timed-out live database connection.", exc_info=True)

        timer = threading.Timer(self._operation_timeout, _expire)
        timer.daemon = True
        timer.start()
        try:
            result = operation()
        finally:
            timer.cancel()

        if expired.is_set():
            raise DatabaseOperationTimeout(
                f"Live database operation exceeded {self._operation_timeout:.1f}s."
            )
        return result

    def fetchone(self):
        row = self._run_with_deadline(self._cursor.fetchone)
        if row is None:
            return None
        return self._row_to_dict(row)

    def fetchall(self):
        return [self._row_to_dict(r) for r in self._run_with_deadline(self._cursor.fetchall)]

    def _row_to_dict(self, row):
        if isinstance(row, dict):
            return row
        if self._cursor.description is None:
            return row
        cols = [col[0] for col in self._cursor.description]
        return dict(zip(cols, row))

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _is_retryable_read_error(sql: str, exc: Exception) -> bool:
    """Return whether a failed idempotent query may use a fresh connection."""
    statement = sql.lstrip().upper()
    if not statement.startswith(("SELECT", "PRAGMA", "EXPLAIN")):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or "interfaceerror" in exc.__class__.__name__.lower():
        return True
    message = str(exc).lower()
    return any(marker in message for marker in ("timeout", "connection", "network", "transport", "interface", "bad parameter", "misuse"))


class _ReusableConnection:
    """Wraps a DB-API connection so context managers commit or rollback without closing the underlying connection."""

    def __init__(
        self,
        conn,
        is_dict_cursor: bool = False,
        lock: Optional[Any] = None,
        retry_cursor_factory=None,
        release_callback=None,
        operation_timeout: Optional[float] = None,
        timeout_callback=None,
        sql_adapter=None,
    ):
        self._conn = conn
        self._is_dict_cursor = is_dict_cursor
        self._lock = lock
        self._retry_cursor_factory = retry_cursor_factory
        self._release_callback = release_callback
        self._operation_timeout = operation_timeout
        self._timeout_callback = timeout_callback
        self._sql_adapter = sql_adapter

    def cursor(self):
        if self._is_dict_cursor:
            return _DictCursor(
                self._conn.cursor(),
                self._retry_cursor_factory,
                operation_timeout=self._operation_timeout,
                timeout_callback=self._timeout_callback,
                sql_adapter=self._sql_adapter,
            )
        return self._conn.cursor()

    def commit(self):
        self._run_with_deadline(self._conn.commit)

    def rollback(self):
        try:
            self._run_with_deadline(self._conn.rollback)
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        if self._lock:
            started_at = time.monotonic()
            self._lock.acquire()
            wait_seconds = time.monotonic() - started_at
            if wait_seconds >= 0.5:
                logger.warning("Waited %.2fs for the shared database connection.", wait_seconds)
        return self

    def _run_with_deadline(self, operation) -> None:
        """Keep a stuck transaction finalization from retaining a live lease."""
        if self._operation_timeout is None:
            operation()
            return

        expired = threading.Event()

        def _expire() -> None:
            expired.set()
            if self._timeout_callback:
                try:
                    self._timeout_callback()
                except Exception:
                    logger.warning("Failed to close a timed-out live database connection.", exc_info=True)

        timer = threading.Timer(self._operation_timeout, _expire)
        timer.daemon = True
        timer.start()
        try:
            operation()
        finally:
            timer.cancel()

        if expired.is_set():
            raise DatabaseOperationTimeout(
                f"Live database operation exceeded {self._operation_timeout:.1f}s."
            )

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                # A timed-out live operation marks this lease as poisoned.
                # Calling rollback after the driver has timed out can panic
                # while the driver is still processing it, so discard the lease instead.
                if getattr(self, "_discard_connection", None) is not self._conn:
                    self.rollback()
            else:
                self.commit()
        finally:
            if self._lock:
                self._lock.release()
            if self._release_callback:
                self._release_callback(self._conn)
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _DatabaseManagerBase:
    """Shared Narratron queries; concrete backends own connection details."""

    def __init__(self, pricing_controller: Optional[PricingController] = None):
        self.pricing_controller = pricing_controller or PricingController.from_env()

    @staticmethod
    def _get_live_connection_timeout(value: Optional[float]) -> float:
        """Return a bounded timeout for each Cloud SQL operation."""
        try:
            timeout = float(5.0 if value is None else value)
        except (TypeError, ValueError):
            timeout = 5.0
        return max(0.1, timeout)

    @staticmethod
    def _get_live_pool_size(value: Optional[int]) -> int:
        """Return a bounded live connection pool size."""
        try:
            size = int(8 if value is None else value)
        except (TypeError, ValueError):
            size = 8
        return min(max(1, size), 32)

    def _get_live_checkout_timeout(self, value: Optional[float]) -> float:
        """Bound how long a request may wait for an idle live connection."""
        try:
            timeout = float(self._live_connection_timeout if value is None else value)
        except (TypeError, ValueError):
            timeout = self._live_connection_timeout
        return max(0.1, timeout)

    def get_pricing_rates(self) -> Dict[str, float]:
        """Return current pricing rates dictionary for polling."""
        return self.pricing_controller.get_rates()

    def _get_connection(self):
        raise NotImplementedError

    def add_music_catalog_track(
        self,
        track_id: str,
        artifact_filename: str,
        prompt: str,
        provider: str,
        model: str,
        term_frequencies: Dict[str, int],
    ) -> None:
        """Persist one private catalog document and its corpus statistics.

        This index deliberately lives only in Cloud SQL PostgreSQL. Local
        development can generate music normally without attempting a database
        connection or maintaining a divergent search index.
        """
        if not self.is_live:
            return
        token_count = sum(term_frequencies.values())
        if not token_count:
            return
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO music_catalog_tracks "
                "(id, artifact_filename, prompt, provider, model, token_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (track_id, artifact_filename, prompt, provider, model, token_count, now),
            )
            for term, frequency in term_frequencies.items():
                cursor.execute(
                    "INSERT INTO music_catalog_terms (track_id, term, term_frequency) VALUES (?, ?, ?)",
                    (track_id, term, frequency),
                )
                cursor.execute(
                    "INSERT INTO music_catalog_term_stats (term, document_frequency) VALUES (?, 1) "
                    "ON CONFLICT (term) DO UPDATE SET document_frequency = "
                    "music_catalog_term_stats.document_frequency + 1",
                    (term,),
                )
            cursor.execute(
                "UPDATE music_catalog_stats SET document_count = document_count + 1, "
                "total_token_count = total_token_count + ? WHERE id = TRUE",
                (token_count,),
            )

    def find_music_catalog_candidates(
        self, term_frequencies: Dict[str, int], limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Return BM25-ranked private catalog candidates from PostgreSQL."""
        if not self.is_live or not term_frequencies:
            return []
        values_sql = ", ".join("(?, ?)" for _ in term_frequencies)
        params: list[Any] = [value for item in term_frequencies.items() for value in item]
        params.append(max(1, int(limit)))
        sql = f"""
            WITH raw_query_terms(term, query_tf) AS (VALUES {values_sql}),
            query_terms AS (
                -- pg8000 may bind VALUES parameters as text.  Cast here so
                -- BM25 arithmetic never depends on driver type inference.
                SELECT term::TEXT, query_tf::DOUBLE PRECISION
                FROM raw_query_terms
            ),
            corpus AS (
                SELECT document_count, total_token_count,
                       total_token_count::DOUBLE PRECISION / NULLIF(document_count, 0) AS average_length
                FROM music_catalog_stats WHERE id = TRUE
            ), scores AS (
                SELECT terms.track_id,
                    SUM(
                        LN(1 + (corpus.document_count - stats.document_frequency + 0.5)
                           / (stats.document_frequency + 0.5))
                        * ((terms.term_frequency * 2.2)
                           / (terms.term_frequency + 1.2 * (1 - 0.75
                              + 0.75 * tracks.token_count / NULLIF(corpus.average_length, 0))))
                        * query_terms.query_tf
                    ) AS bm25_score
                FROM query_terms
                JOIN music_catalog_terms AS terms ON terms.term = query_terms.term
                JOIN music_catalog_term_stats AS stats ON stats.term = terms.term
                JOIN music_catalog_tracks AS tracks ON tracks.id = terms.track_id
                CROSS JOIN corpus
                GROUP BY terms.track_id
            )
            SELECT tracks.id, tracks.artifact_filename AS filename, tracks.prompt, scores.bm25_score
            FROM scores JOIN music_catalog_tracks AS tracks ON tracks.id = scores.track_id
            ORDER BY scores.bm25_score DESC
            LIMIT ?
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()

    def _open_live_connection(self):
        raise NotImplementedError

    def _ensure_live_pool(self) -> None:
        """Create the live connection pool once, before serving requests."""
        with self._connection_lock:
            if self._live_pool is not None:
                return

            logger.info(
                "Preparing Cloud SQL PostgreSQL connection pool (max size=%d, connection timeout=%.1fs).",
                self._live_pool_size,
                self._live_connection_timeout,
            )
            pool = Queue(maxsize=self._live_pool_size)
            try:
                self._live_pool = pool
                self._live_pool_total = 0
                self._live_pool_closed = False
                self._ensure_tables_exist()
            except Exception:
                self._live_pool = None
                self._live_pool_total = 0
                raise

    def _checkout_live_connection(self) -> _ReusableConnection:
        create_connection = False
        with self._connection_lock:
            pool = self._live_pool
            if pool is None or self._live_pool_closed:
                raise DatabaseConnectionTimeout("Live database connection pool is unavailable.")
            try:
                conn = pool.get_nowait()
            except Empty:
                if self._live_pool_total < self._live_pool_size:
                    self._live_pool_total += 1
                    conn = None
                    create_connection = True
                else:
                    conn = None

        if create_connection:
            try:
                conn = self._open_live_connection()
            except Exception:
                with self._connection_lock:
                    self._live_pool_total = max(0, self._live_pool_total - 1)
                raise

        if conn is None:
            try:
                conn = pool.get(timeout=self._live_checkout_timeout)
            except Empty as exc:
                raise DatabaseConnectionTimeout(
                    f"Timed out after {self._live_checkout_timeout:.1f}s waiting for a live database connection."
                ) from exc

        lease = _ReusableConnection(
            conn,
            is_dict_cursor=True,
            operation_timeout=self._live_connection_timeout,
            sql_adapter=self._adapt_sql,
        )
        lease._release_callback = lambda returned: self._release_live_connection(lease, returned)
        lease._retry_cursor_factory = lambda: self._replace_live_cursor(lease)
        lease._timeout_callback = lambda: self._discard_live_connection(lease)
        return lease

    def _release_live_connection(self, lease: _ReusableConnection, conn) -> None:
        """Return a lease to its pool, or close it after shutdown."""
        should_close = False
        with self._connection_lock:
            pool = self._live_pool
            if getattr(lease, "_discard_connection", None) is conn:
                self._live_pool_total = max(0, self._live_pool_total - 1)
                should_close = True
            elif pool is not None and not self._live_pool_closed:
                try:
                    pool.put_nowait(conn)
                    return
                except Exception:
                    logger.warning("Failed to return live database connection to pool.", exc_info=True)
            else:
                should_close = True
        try:
            # Close only after the database call has returned. Closing from
            # the deadline timer races an active driver call.
            if should_close:
                conn.close()
        except Exception:
            pass

    def _discard_live_connection(self, lease: _ReusableConnection) -> None:
        """Mark a timed-out lease for disposal after its active call returns."""
        conn = lease._conn
        lease._discard_connection = conn
        logger.warning(
            "Live database operation exceeded %.1fs; discarding its connection after the operation returns.",
            self._live_connection_timeout,
        )

    def _replace_live_cursor(self, lease: _ReusableConnection):
        """Replace only the failed lease before retrying an idempotent read."""
        logger.warning("Retrying a failed Cloud SQL read with a new connection.")
        replacement = self._open_live_connection()
        old_connection = lease._conn
        lease._conn = replacement
        lease._discard_connection = None
        try:
            old_connection.close()
        except Exception:
            pass
        return replacement.cursor()

    def _ensure_tables_exist(self) -> None:
        if getattr(self, "_initializing_tables", False):
            return
        self._initializing_tables = True
        try:
            self._init_db()
        finally:
            self._initializing_tables = False

    def _init_db(self) -> None:
        """Initialize database schema from storage/schema/postgres.sql if tables do not exist."""
        schema_path = Path(__file__).resolve().parent / "schema" / "postgres.sql"
        if not schema_path.is_file():
            schema_path = Path(r"C:\Users\sidds\Documents\narratron-buddy\storage\schema\postgres.sql")

        schema_sql = schema_path.read_text(encoding="utf-8")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for statement in schema_sql.split(";"):
                statement = "\n".join(
                    line for line in statement.splitlines() if not line.lstrip().startswith("--")
                ).strip()
                if statement:
                    cursor.execute(statement)
            conn.commit()

    @staticmethod
    def _adapt_sql(sql: str) -> str:
        """Translate generic parameter placeholders and syntax for PostgreSQL."""
        translated = sql.replace("?", "%s")
        translated = translated.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "INSERT OR IGNORE INTO" in sql:
            translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return translated

    def register_user(self, username: str, email: str, password: str) -> Dict:
        """Register a new user account."""
        username_clean = username.strip()
        email_clean = email.strip().lower()
        if not username_clean or not email_clean or not password:
            raise ValueError("Username, email, and password are required.")

        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, salt, created_at, credits) VALUES (?, ?, ?, ?, ?, 0.0) RETURNING id",
                    (username_clean, email_clean, password_hash, salt, created_at)
                )
                user_id = cursor.fetchone()["id"]
                conn.commit()
                return {
                    "id": user_id,
                    "username": username_clean,
                    "email": email_clean,
                    "credits": 0.0,
                    "total_voice_minutes": 0.0,
                    "total_images_created": 0,
                    "total_music_created": 0,
                    "total_story_plans": 0,
                    "total_character_voiced_turns": 0,
                    "total_interactive_canvas_used": 0,
                    "mic_sensitivity": 0.5,
                    "created_at": created_at
                }
            except Exception as e:
                err_msg = str(e).lower()
                if "username" in err_msg and ("unique" in err_msg or "duplicate" in err_msg or "constraint" in err_msg):
                    raise ValueError("Username already exists.")
                elif "email" in err_msg and ("unique" in err_msg or "duplicate" in err_msg or "constraint" in err_msg):
                    raise ValueError("Email already registered.")
                elif "unique" in err_msg or "duplicate" in err_msg or "constraint" in err_msg:
                    raise ValueError("Username or email already exists.")
                raise

    def authenticate_user(self, username_or_email: str, password: str) -> Optional[Dict]:
        """Authenticate user credentials."""
        query_val = username_or_email.strip()
        if not query_val or not password:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, email, password_hash, salt, credits, total_voice_minutes, total_images_created, total_music_created, total_story_plans, total_character_voiced_turns, total_interactive_canvas_used, mic_sensitivity, created_at FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
                (query_val, query_val)
            )
            row = cursor.fetchone()
            if not row:
                return None

            user_dict = dict(row)
            expected_hash = hashlib.sha256((password + user_dict["salt"]).encode("utf-8")).hexdigest()
            if secrets.compare_digest(expected_hash, user_dict["password_hash"]):
                return {
                    "id": user_dict["id"],
                    "username": user_dict["username"],
                    "email": user_dict["email"],
                    "credits": user_dict.get("credits", 0.0),
                    "total_voice_minutes": user_dict.get("total_voice_minutes", 0.0),
                    "total_images_created": user_dict.get("total_images_created", 0),
                    "total_music_created": user_dict.get("total_music_created", 0),
                    "total_story_plans": user_dict.get("total_story_plans", 0),
                    "total_character_voiced_turns": user_dict.get("total_character_voiced_turns", 0),
                    "total_interactive_canvas_used": user_dict.get("total_interactive_canvas_used", 0),
                    "mic_sensitivity": user_dict.get("mic_sensitivity", 0.5),
                    "created_at": user_dict["created_at"]
                }
            return None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, credits, total_voice_minutes, total_images_created, total_music_created, total_story_plans, total_character_voiced_turns, total_interactive_canvas_used, mic_sensitivity, created_at FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_user(self, user_id: int) -> bool:
        """Permanently delete a user account, cascading deletion to all dependent records."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return bool(cursor.rowcount and cursor.rowcount > 0)

    def get_users_by_ids(self, user_ids: Iterable[int]) -> Dict[int, Dict]:
        """Return the requested users in one batched lookup, keyed by user ID."""
        ids = list(dict.fromkeys(user_id for user_id in user_ids if user_id is not None))
        if not ids:
            return {}

        users: Dict[int, Dict] = {}
        # Keep the batch safely bounded below driver variable limits.
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for start in range(0, len(ids), 900):
                batch = ids[start:start + 900]
                placeholders = ", ".join("?" for _ in batch)
                cursor.execute(
                    f"SELECT id, username FROM users WHERE id IN ({placeholders})",
                    tuple(batch),
                )
                users.update({row["id"]: dict(row) for row in cursor.fetchall()})
        return users

    def get_user_profile(self, username: str, viewer_user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Return a public profile, exposing stats only to its owner when private."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, bio, stats_visible, lifetime_credits_used, profile_color FROM users WHERE LOWER(username) = LOWER(?)",
                (username.strip(),),
            )
            user = cursor.fetchone()
            if not user:
                return None
            profile = dict(user)
            profile_user_id = profile.pop("id")
            is_owner = profile_user_id == viewer_user_id
            stats_visible = bool(profile.pop("stats_visible", False))
            profile["is_owner"] = is_owner
            profile["stats_visible"] = stats_visible
            if is_owner or stats_visible:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM theater_views views
                    JOIN theaters ON theaters.theater_id = views.theater_id
                    WHERE theaters.user_id = ?
                    """,
                    (profile_user_id,),
                )
                profile["stats"] = {
                    "lifetime_credits_used": profile.pop("lifetime_credits_used", 0.0),
                    "theater_views": cursor.fetchone()["count"],
                }
            else:
                profile.pop("lifetime_credits_used", None)
                profile["stats"] = None
            return profile

    def update_user_profile(self, user_id: int, bio: str, stats_visible: bool, profile_color: str) -> bool:
        """Update the owner-controlled public fields of a profile."""
        if len(bio) > 1_000:
            raise ValueError("Bio must be 1,000 characters or fewer.")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", profile_color):
            raise ValueError("Profile color must be a six-digit hex color.")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET bio = ?, stats_visible = ?, profile_color = ? WHERE id = ?",
                (bio.strip(), int(stats_visible), profile_color.lower(), user_id),
            )
            return cursor.rowcount > 0

    def create_credit_gift(self, sender_user_id: int, credits: float) -> Dict[str, Any]:
        """Create a single-use, seven-day credit gift without reserving funds."""
        try:
            credits = float(credits)
        except (TypeError, ValueError) as exc:
            raise ValueError("Gift amount must be a positive number.") from exc
        if credits <= 0 or not credits < float("inf"):
            raise ValueError("Gift amount must be a positive number.")

        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = now + datetime.timedelta(days=7)
        token = secrets.token_urlsafe(32)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (sender_user_id,))
            if not cursor.fetchone():
                raise ValueError("User not found.")
            cursor.execute(
                "INSERT INTO credit_referrals "
                "(token, sender_user_id, credits, created_at, expires_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (token, sender_user_id, credits, now.isoformat(), expires_at.isoformat()),
            )
            return {"token": token, "credits": credits, "expires_at": expires_at.isoformat()}

    def get_credit_gift(self, token: str) -> Optional[Dict[str, Any]]:
        """Return safe status data for a gift link without exposing either user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token, credits, expires_at, claimed_by_user_id, status "
                "FROM credit_referrals WHERE token = ?",
                (token,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            gift = dict(row)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if gift["status"] == "pending" and gift["expires_at"] <= now:
                cursor.execute("UPDATE credit_referrals SET status = 'expired' WHERE token = ?", (token,))
                gift["status"] = "expired"
            gift["claimed"] = bool(gift.pop("claimed_by_user_id"))
            gift.pop("token", None)
            return gift

    def claim_credit_gift(self, token: str, recipient_user_id: int) -> Dict[str, Any]:
        """Transfer a valid gift once, while ensuring its sender remains non-negative."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sender_user_id, credits, expires_at, status, claimed_by_user_id "
                "FROM credit_referrals WHERE token = ?",
                (token,),
            )
            gift = cursor.fetchone()
            if not gift:
                raise ValueError("Gift link not found.")
            gift = dict(gift)
            if gift["status"] != "pending" or gift["claimed_by_user_id"] is not None:
                raise ValueError("This gift has already been claimed.")
            if gift["expires_at"] <= now:
                cursor.execute("UPDATE credit_referrals SET status = 'expired' WHERE token = ?", (token,))
                raise ValueError("This gift link has expired.")
            if gift["sender_user_id"] == recipient_user_id:
                raise ValueError("You cannot claim your own gift.")

            # Claim the row first.  In the same transaction this serializes
            # competing claim attempts; any later failure rolls it back.
            cursor.execute(
                "UPDATE credit_referrals SET claimed_by_user_id = ?, claimed_at = ?, status = 'claimed' "
                "WHERE token = ? AND status = 'pending' AND claimed_by_user_id IS NULL AND expires_at > ?",
                (recipient_user_id, now, token, now),
            )
            if cursor.rowcount != 1:
                raise ValueError("This gift is no longer available.")
            cursor.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?",
                (gift["credits"], gift["sender_user_id"], gift["credits"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("The sender no longer has enough credits for this gift.")
            cursor.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (gift["credits"], recipient_user_id))
            if cursor.rowcount != 1:
                raise ValueError("Recipient account not found.")
            return {
                "credits": gift["credits"],
                "expires_at": gift["expires_at"],
                "sender_user_id": gift["sender_user_id"],
            }

    def create_auth_session(self, user_id: int, days_valid: int = 7) -> str:
        """Create a new session token for a user."""
        token = secrets.token_urlsafe(32)
        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(days=days_valid)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now.isoformat(), expires.isoformat())
            )
            conn.commit()
        return token

    def validate_session_token(self, token: str, record_activity: bool = True) -> Optional[Dict]:
        """Validate an active authentication token and return user information if valid."""
        if not token:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT u.id, u.username, u.email, u.credits, u.total_voice_minutes, u.total_images_created, u.total_music_created, u.total_story_plans, u.total_character_voiced_turns, u.total_interactive_canvas_used, u.mic_sensitivity, u.profile_color, u.created_at, s.expires_at
                FROM auth_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ?
                """,
                (token,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            res = dict(row)
            expires_at = datetime.datetime.fromisoformat(res["expires_at"])

            if datetime.datetime.now(datetime.timezone.utc) > expires_at:
                cursor.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
                conn.commit()
                return None

            if record_activity:
                self.record_user_activity(res["id"])
            return res

    def update_user_mic_sensitivity(self, user_id: int, mic_sensitivity: float) -> bool:
        """Update microphone sensitivity setting for a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET mic_sensitivity = ? WHERE id = ?",
                (mic_sensitivity, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def record_user_activity(self, user_id: int) -> bool:
        """Update last_active_at timestamp for user."""
        if not user_id:
            return False
        return True

    def record_theater_view(self, theater_id: str, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> bool:
        """Record a theater view event in database."""
        if not theater_id:
            return False
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO theater_views (theater_id, user_id, viewed_at, ip_address) VALUES (?, ?, ?, ?)",
                    (theater_id, user_id, now_iso, ip_address)
                )
                conn.commit()
                if user_id:
                    self.record_user_activity(user_id)
                return True
        except Exception as e:
            logger.error(f"Error recording theater view: {e}")
            return False

    def get_stats_summary(self) -> Dict[str, Any]:
        """Fetch stats summary including account counts, active users (7d), and theater views."""
        now = datetime.datetime.now(datetime.timezone.utc)
        seven_days_ago = (now - datetime.timedelta(days=7)).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Number of accounts
            cursor.execute("SELECT COUNT(*) as count FROM users")
            total_accounts = cursor.fetchone()["count"]

            # 2. Active users in the last 7 days
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) as count FROM (
                    SELECT id as user_id FROM users WHERE (last_active_at IS NOT NULL AND last_active_at >= ?) OR created_at >= ?
                    UNION
                    SELECT user_id FROM auth_sessions WHERE created_at >= ?
                    UNION
                    SELECT user_id FROM theaters WHERE created_at >= ?
                    UNION
                    SELECT user_id FROM theater_views WHERE user_id IS NOT NULL AND viewed_at >= ?
                )
            """, (seven_days_ago, seven_days_ago, seven_days_ago, seven_days_ago, seven_days_ago))
            active_users_7d = cursor.fetchone()["count"]

            # 3. Theater views
            cursor.execute("SELECT COUNT(*) as count FROM theater_views")
            total_theater_views = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM theater_views WHERE viewed_at >= ?", (seven_days_ago,))
            theater_views_7d = cursor.fetchone()["count"]

            # 4. Top viewed theaters
            cursor.execute("""
                SELECT v.theater_id as theater_id, COUNT(*) as views,
                       COALESCE(t.name, t.theater_id, v.theater_id) as name
                FROM theater_views v
                LEFT JOIN theaters t ON v.theater_id = t.theater_id
                GROUP BY v.theater_id, t.name, t.theater_id
                ORDER BY views DESC
                LIMIT 10
            """)
            top_viewed_theaters = [dict(r) for r in cursor.fetchall()]

            # 5. Daily theater views for past 7 days
            daily_views_7d = []
            for i in range(6, -1, -1):
                day_start = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + datetime.timedelta(days=1)
                day_str = day_start.strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT COUNT(*) as count FROM theater_views WHERE viewed_at >= ? AND viewed_at < ?",
                    (day_start.isoformat(), day_end.isoformat())
                )
                cnt = cursor.fetchone()["count"]
                daily_views_7d.append({"date": day_str, "views": cnt})

            # 6. Daily active users for past 7 days
            daily_active_users_7d = []
            for i in range(6, -1, -1):
                day_start = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + datetime.timedelta(days=1)
                day_str = day_start.strftime("%Y-%m-%d")
                cursor.execute("""
                    SELECT COUNT(DISTINCT user_id) as count FROM (
                        SELECT id as user_id FROM users WHERE (last_active_at IS NOT NULL AND last_active_at >= ? AND last_active_at < ?) OR (created_at >= ? AND created_at < ?)
                        UNION
                        SELECT user_id FROM auth_sessions WHERE created_at >= ? AND created_at < ?
                        UNION
                        SELECT user_id FROM theaters WHERE created_at >= ? AND created_at < ?
                        UNION
                        SELECT user_id FROM theater_views WHERE user_id IS NOT NULL AND viewed_at >= ? AND viewed_at < ?
                    )
                """, (
                    day_start.isoformat(), day_end.isoformat(),
                    day_start.isoformat(), day_end.isoformat(),
                    day_start.isoformat(), day_end.isoformat(),
                    day_start.isoformat(), day_end.isoformat(),
                    day_start.isoformat(), day_end.isoformat()
                ))
                cnt = cursor.fetchone()["count"]
                daily_active_users_7d.append({"date": day_str, "active_users": cnt})

            return {
                "total_accounts": total_accounts,
                "active_users_7d": active_users_7d,
                "total_theater_views": total_theater_views,
                "theater_views_7d": theater_views_7d,
                "top_viewed_theaters": top_viewed_theaters,
                "daily_views_7d": daily_views_7d,
                "daily_active_users_7d": daily_active_users_7d
            }

    def invalidate_session_token(self, token: str) -> bool:
        """Delete an auth session token on logout."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            conn.commit()
            return cursor.rowcount > 0

    def create_password_reset_token(self, email_or_username: str, minutes_valid: int = 30) -> Optional[tuple[str, Dict]]:
        """Create a single-use password reset token for a user identified by email or username."""
        query_val = email_or_username.strip()
        if not query_val:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, email FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
                (query_val, query_val)
            )
            row = cursor.fetchone()
            if not row:
                return None
            user_dict = dict(row)

        token = secrets.token_urlsafe(32)
        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(minutes=minutes_valid)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO password_reset_tokens (token, user_id, created_at, expires_at, used) VALUES (?, ?, ?, ?, 0)",
                (token, user_dict["id"], now.isoformat(), expires.isoformat())
            )
            conn.commit()
            return token, user_dict

    def validate_password_reset_token(self, token: str) -> Optional[Dict]:
        """Validate password reset token and return associated user."""
        if not token:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT u.id, u.username, u.email, t.expires_at, t.used
                FROM password_reset_tokens t
                JOIN users u ON t.user_id = u.id
                WHERE t.token = ?
                """,
                (token,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            res = dict(row)
            if res["used"]:
                return None

            expires_at = datetime.datetime.fromisoformat(res["expires_at"])
            if datetime.datetime.now(datetime.timezone.utc) > expires_at:
                return None

            return {"id": res["id"], "username": res["username"], "email": res["email"]}

    def reset_password_with_token(self, token: str, new_password: str) -> bool:
        """Reset user password using token."""
        user = self.validate_password_reset_token(token)
        if not user or not new_password:
            return False

        salt = secrets.token_hex(16)
        password_hash = hashlib.sha256((new_password + salt).encode("utf-8")).hexdigest()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (password_hash, salt, user["id"])
            )
            cursor.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))
            cursor.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user["id"],))
            conn.commit()
            return True

    def record_deployment(self, theater_id: str, user_id: int, join_key: str, cost: float = 0.0, is_persistent: bool = False, name: Optional[str] = None) -> bool:
        """Record deployment in database and deduct cost from user credits."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user or user["credits"] < cost:
                raise ValueError("Insufficient credits for theater deployment.")

            persistent_val = 1 if is_persistent else 0
            last_billed_val = now_iso if is_persistent else None
            # Claim the durable theater ID before debiting.  A retry with the
            # same ID hits the primary-key constraint before it can deduct.
            cursor.execute(
                "INSERT INTO theaters (theater_id, user_id, name, join_key, cost, created_at, is_persistent, last_billed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (theater_id, user_id, name, join_key, cost, now_iso, persistent_val, last_billed_val)
            )
            cursor.execute(
                "UPDATE users SET credits = credits - ?, lifetime_credits_used = lifetime_credits_used + ? WHERE id = ?",
                (cost, cost, user_id)
            )
            conn.commit()
            return True

    def add_user_credits(self, user_id: int, credits_amount: float, usd_amount: float, payment_method: str = "card_mock") -> Dict[str, Any]:
        """Add credits to a user's account and log payment transaction."""
        if credits_amount <= 0:
            raise ValueError("Credit amount must be positive.")
        
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET credits = credits + ? WHERE id = ?",
                (credits_amount, user_id)
            )
            if cursor.rowcount == 0:
                raise ValueError("User not found.")
            
            cursor.execute(
                """INSERT INTO payment_transactions (user_id, amount_usd, credits_added, payment_method, status, created_at)
                   VALUES (?, ?, ?, ?, 'completed', ?) RETURNING id""",
                (user_id, usd_amount, credits_amount, payment_method, now_iso)
            )
            tx_id = cursor.fetchone()["id"]
            
            cursor.execute("SELECT id, username, email, credits, total_voice_minutes, total_images_created, total_music_created, total_story_plans, total_character_voiced_turns, total_interactive_canvas_used, created_at FROM users WHERE id = ?", (user_id,))
            updated_user = dict(cursor.fetchone())
            conn.commit()
            return {
                "transaction_id": tx_id,
                "user": updated_user,
                "credits_added": credits_amount,
                "amount_usd": usd_amount,
                "created_at": now_iso
            }

    def add_stripe_session_credits(
        self, user_id: int, credits_amount: float, usd_amount: float, stripe_session_id: str,
        payment_method: str = "stripe_checkout"
    ) -> Dict[str, Any]:
        """Credit a paid Stripe Checkout session exactly once."""
        if not stripe_session_id:
            raise ValueError("Stripe Checkout session ID is required.")
        if credits_amount <= 0:
            raise ValueError("Credit amount must be positive.")

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
            if cursor.fetchone() is None:
                raise ValueError("User not found.")

            # Claim the Stripe session before changing the user's balance.  The
            # unique index makes this safe when a Checkout return and webhook
            # delivery (or webhook retries) arrive concurrently.
            cursor.execute(
                "INSERT INTO payment_transactions "
                "(user_id, amount_usd, credits_added, payment_method, status, created_at, stripe_session_id) "
                "VALUES (?, ?, ?, ?, 'completed', ?, ?) ON CONFLICT DO NOTHING RETURNING id",
                (user_id, usd_amount, credits_amount, payment_method, now_iso, stripe_session_id),
            )
            inserted = cursor.fetchone()
            credited = inserted is not None
            if credited:
                cursor.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (credits_amount, user_id))
            else:
                cursor.execute(
                    "SELECT id, created_at FROM payment_transactions WHERE stripe_session_id = ?",
                    (stripe_session_id,),
                )
                existing = cursor.fetchone()
                tx_id = existing["id"] if isinstance(existing, dict) else existing[0]
                created_at = existing["created_at"] if isinstance(existing, dict) else existing[1]
            cursor.execute(
                "SELECT id, username, email, credits, total_voice_minutes, total_images_created, total_music_created, total_story_plans, total_character_voiced_turns, total_interactive_canvas_used, created_at "
                "FROM users WHERE id = ?", (user_id,)
            )
            updated_user = dict(cursor.fetchone())
            if credited:
                tx_id = inserted["id"]
                created_at = now_iso
            return {"transaction_id": tx_id, "user": updated_user,
                    "credits_added": credits_amount if credited else 0.0,
                    "amount_usd": usd_amount, "created_at": created_at,
                    "already_credited": not credited}

    def record_user_usage(
        self,
        user_id: int,
        voice_minutes: float = 0.0,
        images_created: int = 0,
        music_created: int = 0,
        story_plans: int = 0,
        character_voiced_turns: int = 0,
        interactive_canvas_used: int = 0,
        layered_animations_created: int = 0,
        credit_cost: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record metered usage for a user while simultaneously updating credit balance.

        Credits are allowed to go negative per user settings/preferences.
        """
        layered_animations_created = 0 if layered_animations_created is None else layered_animations_created
        if (
            voice_minutes < 0
            or images_created < 0
            or music_created < 0
            or story_plans < 0
            or character_voiced_turns < 0
            or interactive_canvas_used < 0
            or layered_animations_created < 0
        ):
            raise ValueError(
                "Usage parameters (voice_minutes, images_created, music_created, story_plans, character_voiced_turns, interactive_canvas_used, layered_animations_created) must be non-negative."
            )

        if credit_cost is None:
            credit_cost = self.pricing_controller.calculate_usage_cost(
                voice_minutes=voice_minutes,
                images_created=images_created,
                music_created=music_created,
                story_plans=story_plans,
                character_voiced_turns=character_voiced_turns,
                interactive_canvas_used=interactive_canvas_used,
                layered_animations_created=layered_animations_created,
            )
        elif credit_cost < 0:
            raise ValueError("credit_cost must be non-negative.")
        event_key = idempotency_key or f"usage:{uuid.uuid4()}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
            if not cursor.fetchone():
                raise ValueError("User not found.")
            cursor.execute(
                "INSERT OR IGNORE INTO usage_events "
                "(idempotency_key, user_id, voice_minutes, images_created, music_created, story_plans, character_voiced_turns, interactive_canvas_used, layered_animations_created, credit_cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_key, user_id, voice_minutes, images_created, music_created, story_plans, character_voiced_turns, interactive_canvas_used, layered_animations_created, credit_cost, now_iso),
            )
            claimed = cursor.rowcount == 1
            if not claimed:
                cursor.execute(
                    "SELECT user_id, voice_minutes, images_created, music_created, story_plans, character_voiced_turns, interactive_canvas_used, layered_animations_created, credit_cost FROM usage_events WHERE idempotency_key = ?",
                    (event_key,),
                )
                existing = cursor.fetchone()
                existing_music = existing["music_created"] if (existing and "music_created" in existing) else 0
                existing_story_plans = existing["story_plans"] if (existing and "story_plans" in existing) else 0
                existing_character_voiced_turns = existing["character_voiced_turns"] if (existing and "character_voiced_turns" in existing) else 0
                existing_interactive_canvas_used = existing["interactive_canvas_used"] if (existing and "interactive_canvas_used" in existing) else 0
                existing_layered_animations = existing["layered_animations_created"] if (existing and "layered_animations_created" in existing) else 0
                if not existing or (
                    existing["user_id"] != user_id
                    or existing["voice_minutes"] != voice_minutes
                    or existing["images_created"] != images_created
                    or existing_music != music_created
                    or existing_story_plans != story_plans
                    or existing_character_voiced_turns != character_voiced_turns
                    or existing_interactive_canvas_used != interactive_canvas_used
                    or existing_layered_animations != layered_animations_created
                    or existing["credit_cost"] != credit_cost
                ):
                    raise ValueError("Usage idempotency key was already used for different usage.")
            if claimed:
                cursor.execute(
                    """
                    UPDATE users
                    SET total_voice_minutes = total_voice_minutes + ?,
                        total_images_created = total_images_created + ?,
                        total_music_created = total_music_created + ?,
                        total_story_plans = total_story_plans + ?,
                        total_character_voiced_turns = total_character_voiced_turns + ?,
                        total_interactive_canvas_used = total_interactive_canvas_used + ?,
                        credits = credits - ?,
                        lifetime_credits_used = lifetime_credits_used + ?
                    WHERE id = ?
                    """,
                    (voice_minutes, images_created, music_created, story_plans, character_voiced_turns, interactive_canvas_used, credit_cost, credit_cost, user_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError("User not found.")

            cursor.execute(
                "SELECT id, username, email, credits, total_voice_minutes, total_images_created, total_music_created, total_story_plans, total_character_voiced_turns, total_interactive_canvas_used, created_at FROM users WHERE id = ?",
                (user_id,),
            )
            updated_user = dict(cursor.fetchone())
            return updated_user

    def get_user_transactions(self, user_id: int) -> List[Dict[str, Any]]:
        """Retrieve payment transaction history for a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM payment_transactions WHERE user_id = ? ORDER BY id DESC",
                (user_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_deployment(self, theater_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM theaters WHERE theater_id = ?", (theater_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_deployments(self, theater_ids: Iterable[str]) -> Dict[str, Dict]:
        """Return deployments for theater IDs with batched queries, keyed by ID."""
        ids = list(dict.fromkeys(theater_id for theater_id in theater_ids if theater_id))
        if not ids:
            return {}

        deployments: Dict[str, Dict] = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for start in range(0, len(ids), 900):
                batch = ids[start:start + 900]
                placeholders = ", ".join("?" for _ in batch)
                cursor.execute(
                    f"SELECT * FROM theaters WHERE theater_id IN ({placeholders})",
                    tuple(batch),
                )
                for row in cursor.fetchall():
                    deployment = dict(row)
                    deployments[deployment["theater_id"]] = deployment
        return deployments

    def get_user_theater_records(self, user_id: int) -> List[Dict[str, Any]]:
        """Return a user's deploy-page theater data with one database query.

        The deploy page only displays theaters owned by the signed-in user.  Keeping
        this lookup user-scoped avoids scanning every deployment, export, and view
        record before the browser discards other users' theaters.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    theaters.theater_id,
                    theaters.join_key,
                    theaters.created_at,
                    theaters.last_billed_at,
                    theaters.name AS exported_name,
                    (
                        SELECT MAX(views.viewed_at)
                        FROM theater_views AS views
                        WHERE views.theater_id = theaters.theater_id
                    ) AS last_viewed_at
                FROM theaters
                WHERE theaters.user_id = ?
                """,
                (user_id,),
            )
            rows = cursor.fetchall()

        records: List[Dict[str, Any]] = []
        for row in rows:
            row = dict(row)
            theater_id = row["theater_id"]
            metadata = {
                "theater_id": theater_id,
                "name": row.get("exported_name") or theater_id,
                "status": "deployed",
                "join_key": row["join_key"],
                "created_at": row["created_at"],
                "mounted_references": [],
                "mounted_playlists": {},
                "config": {},
            }
            last_used_at = max(
                value for value in (row.get("created_at"), row.get("last_billed_at"), row.get("last_viewed_at"))
                if value
            ) if any((row.get("created_at"), row.get("last_billed_at"), row.get("last_viewed_at"))) else ""
            records.append({"theater_id": theater_id, "metadata": metadata, "last_used_at": last_used_at})
        return records


    def update_theater_name(self, theater_id: str, new_name: str, user_id: Optional[int] = None) -> bool:
        """Update the display name of a theater in theaters table."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO theaters (theater_id, user_id, name, exported_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(theater_id) DO UPDATE SET
                    name = excluded.name,
                    user_id = coalesce(excluded.user_id, theaters.user_id),
                    exported_at = excluded.exported_at
                """,
                (theater_id, user_id, new_name, now_iso)
            )
            conn.commit()
            return True

    def delete_deployment(self, theater_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM theaters WHERE theater_id = ?", (theater_id,))
            c1 = cursor.rowcount or 0
            conn.commit()
            return c1 > 0


    def set_theater_persistence(self, theater_id: str, is_persistent: bool) -> bool:
        """Set whether a theater session is marked as persistent."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_billed_at FROM theaters WHERE theater_id = ?", (theater_id,))
            dep = cursor.fetchone()
            if not dep:
                return False
            
            persistent_val = 1 if is_persistent else 0
            if is_persistent:
                last_billed = dep["last_billed_at"] if isinstance(dep, dict) else dep[0]
                if not last_billed:
                    last_billed = now_iso
                cursor.execute(
                    "UPDATE theaters SET is_persistent = ?, last_billed_at = ? WHERE theater_id = ?",
                    (persistent_val, last_billed, theater_id)
                )
            else:
                cursor.execute(
                    "UPDATE theaters SET is_persistent = ? WHERE theater_id = ?",
                    (persistent_val, theater_id)
                )
            conn.commit()
            return True

    def get_theater_persistence(self, theater_id: str) -> bool:
        """Check if a theater session is marked persistent."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_persistent FROM theaters WHERE theater_id = ?", (theater_id,))
            row = cursor.fetchone()
            if not row:
                return False
            val = row["is_persistent"] if isinstance(row, dict) else row[0]
            return bool(val)

    def storage_daemon(
        self,
        theater_repository: Any = None,
        theater_manager: Any = None,
        ttl_seconds: float = 604800.0,
        hourly_cost: float = 0.004167,
        current_time: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Alias for run_database_daemon: process non-persistent storage cleanup and persistent session billing."""
        return self.run_database_daemon(
            theater_repository=theater_repository,
            theater_manager=theater_manager,
            ttl_seconds=ttl_seconds,
            hourly_cost=hourly_cost,
            current_time=current_time,
        )

    def run_database_daemon(
        self,
        theater_repository: Any = None,
        theater_manager: Any = None,
        ttl_seconds: float = 604800.0,
        hourly_cost: float = 0.004167,
        current_time: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Database daemon logic to clean up expired non-persistent sessions and accrue charges for persistent sessions."""
        now = current_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        cleaned_up_sessions = []
        accrued_charges = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT theater_id, user_id, is_persistent, created_at, last_billed_at FROM theaters")
            rows = cursor.fetchall()
            deployments = [dict(r) if isinstance(r, dict) else dict(r) for r in rows]

            for dep in deployments:
                theater_id = dep["theater_id"]
                user_id = dep["user_id"]
                is_persistent = bool(dep.get("is_persistent", 0))
                created_at_str = dep.get("created_at")

                try:
                    created_at_dt = datetime.datetime.fromisoformat(created_at_str)
                    if created_at_dt.tzinfo is None:
                        created_at_dt = created_at_dt.replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    created_at_dt = now

                if not is_persistent:
                    # Clean up if older than ttl_seconds
                    age_seconds = (now - created_at_dt).total_seconds()
                    if age_seconds > ttl_seconds:
                        logger.info(f"[DatabaseDaemon] Auto-cleaning expired non-persistent theater_id={theater_id}")
                        cursor.execute("DELETE FROM theaters WHERE theater_id = ?", (theater_id,))
                        if theater_repository:
                            try:
                                if hasattr(theater_repository, "delete_theater"):
                                    theater_repository.delete_theater(theater_id)
                                elif hasattr(theater_repository, "destroy_theater"):
                                    theater_repository.destroy_theater(theater_id)
                            except Exception as e:
                                logger.warning(f"[DatabaseDaemon] Error deleting theater repository files for {theater_id}: {e}")
                        if theater_manager:
                            try:
                                theater_manager.destroy_theater(theater_id)
                            except Exception as e:
                                logger.warning(f"[DatabaseDaemon] Error destroying theater files for {theater_id}: {e}")
                        cleaned_up_sessions.append(theater_id)
                else:
                    # Accrue charges for persistent session
                    last_billed_str = dep.get("last_billed_at") or created_at_str
                    try:
                        last_billed_dt = datetime.datetime.fromisoformat(last_billed_str)
                        if last_billed_dt.tzinfo is None:
                            last_billed_dt = last_billed_dt.replace(tzinfo=datetime.timezone.utc)
                    except Exception:
                        last_billed_dt = created_at_dt

                    elapsed_seconds = (now - last_billed_dt).total_seconds()
                    elapsed_hours = elapsed_seconds / 3600.0

                    if elapsed_hours >= 1.0:
                        hours_to_bill = int(elapsed_hours)
                        charge_amount = round(hours_to_bill * hourly_cost, 4)

                        cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
                        user_row = cursor.fetchone()
                        user_credits = user_row["credits"] if user_row else 0.0

                        if user_credits >= charge_amount:
                            new_last_billed = (last_billed_dt + datetime.timedelta(hours=hours_to_bill)).isoformat()
                            # Advance the billing cursor conditionally before
                            # debiting.  A concurrent run or retry that already
                            # committed this interval sees rowcount == 0 and
                            # cannot charge it again.
                            cursor.execute(
                                "UPDATE theaters SET last_billed_at = ? "
                                "WHERE theater_id = ? AND COALESCE(last_billed_at, created_at) = ?",
                                (new_last_billed, theater_id, last_billed_str or created_at_str),
                            )
                            if cursor.rowcount:
                                cursor.execute(
                                    "UPDATE users SET credits = credits - ?, lifetime_credits_used = lifetime_credits_used + ? WHERE id = ?",
                                    (charge_amount, charge_amount, user_id),
                                )
                                accrued_charges.append({
                                    "theater_id": theater_id,
                                    "user_id": user_id,
                                    "amount": charge_amount,
                                    "hours": hours_to_bill,
                                })
                                logger.info(f"[DatabaseDaemon] Accrued {charge_amount} credits charge for persistent theater_id={theater_id}")
                        else:
                            logger.warning(f"[DatabaseDaemon] User user_id={user_id} has insufficient credits ({user_credits}) for persistent theater_id={theater_id}. Expiring session.")
                            cursor.execute("DELETE FROM theaters WHERE theater_id = ?", (theater_id,))
                            if theater_repository:
                                try:
                                    if hasattr(theater_repository, "delete_theater"):
                                        theater_repository.delete_theater(theater_id)
                                    elif hasattr(theater_repository, "destroy_theater"):
                                        theater_repository.destroy_theater(theater_id)
                                except Exception as e:
                                    logger.warning(f"[DatabaseDaemon] Error deleting theater repository files for {theater_id}: {e}")
                            if theater_manager:
                                try:
                                    theater_manager.destroy_theater(theater_id)
                                except Exception as e:
                                    logger.warning(f"[DatabaseDaemon] Error destroying theater files for {theater_id}: {e}")
                            cleaned_up_sessions.append(theater_id)

            conn.commit()

        return {
            "cleaned_up_sessions": cleaned_up_sessions,
            "accrued_charges": accrued_charges,
        }

    def get_theater_by_join_key(self, join_key: str) -> Optional[Dict]:
        clean_key = join_key.strip().upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM theaters WHERE UPPER(join_key) = ?", (clean_key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_exported_theater_ids(self) -> List[str]:
        """Get all distinct theater IDs stored in theaters table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT theater_id FROM theaters")
            return [row["theater_id"] for row in cursor.fetchall() if row["theater_id"]]

    def get_theaters_last_used(self) -> Dict[str, str]:
        """Return a mapping of theater_id to its most recent activity timestamp (ISO string)."""
        activity_map: Dict[str, str] = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. theaters created_at / last_billed_at / exported_at
            cursor.execute("SELECT theater_id, created_at, last_billed_at, exported_at FROM theaters")
            for row in cursor.fetchall():
                tid = row["theater_id"]
                if tid:
                    for field in ("last_billed_at", "created_at", "exported_at"):
                        ts = row[field]
                        if ts and (tid not in activity_map or ts > activity_map[tid]):
                            activity_map[tid] = ts

            # 2. theater_views viewed_at
            cursor.execute("SELECT theater_id, MAX(viewed_at) as last_viewed FROM theater_views GROUP BY theater_id")
            for row in cursor.fetchall():
                tid = row["theater_id"]
                ts = row["last_viewed"]
                if tid and ts and (tid not in activity_map or ts > activity_map[tid]):
                    activity_map[tid] = ts

        return activity_map


    async def record_theater_view_async(
        self, theater_id: str, user_id: Optional[int] = None, ip_address: Optional[str] = None
    ) -> bool:
        """Record theater view asynchronously."""
        try:
            return await asyncio.to_thread(
                self.record_theater_view, theater_id, user_id, ip_address
            )
        except Exception:
            logger.exception("Failed to record view for theater '%s'", theater_id)
            return False

    async def record_deployment_async(
        self, theater_id: str, user_id: int, join_key: str, cost: float = 0.0, is_persistent: bool = False, name: Optional[str] = None
    ) -> bool:
        """Record deployment asynchronously."""
        return await asyncio.to_thread(
            self.record_deployment, theater_id, user_id, join_key, cost, is_persistent, name
        )

    async def register_user_async(self, username: str, email: str, password: str) -> Dict:
        """Register user asynchronously."""
        return await asyncio.to_thread(self.register_user, username, email, password)

    async def create_auth_session_async(self, user_id: int, days_valid: int = 7) -> str:
        """Create auth session asynchronously."""
        return await asyncio.to_thread(self.create_auth_session, user_id, days_valid)

    async def invalidate_session_token_async(self, token: str) -> bool:
        """Invalidate session token asynchronously."""
        return await asyncio.to_thread(self.invalidate_session_token, token)

    async def add_user_credits_async(
        self, user_id: int, credits_amount: float, usd_amount: float, payment_method: str = "card_mock"
    ) -> Dict[str, Any]:
        """Add user credits asynchronously."""
        return await asyncio.to_thread(
            self.add_user_credits, user_id, credits_amount, usd_amount, payment_method
        )

    async def record_user_usage_async(
        self,
        user_id: int,
        voice_minutes: float = 0.0,
        images_created: int = 0,
        music_created: int = 0,
        story_plans: int = 0,
        character_voiced_turns: int = 0,
        interactive_canvas_used: int = 0,
        layered_animations_created: int = 0,
        credit_cost: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record user usage asynchronously."""
        return await asyncio.to_thread(
            self.record_user_usage,
            user_id=user_id,
            voice_minutes=voice_minutes,
            images_created=images_created,
            music_created=music_created,
            story_plans=story_plans,
            character_voiced_turns=character_voiced_turns,
            interactive_canvas_used=interactive_canvas_used,
            layered_animations_created=layered_animations_created,
            credit_cost=credit_cost,
            idempotency_key=idempotency_key,
        )

    async def reset_password_with_token_async(self, token: str, new_password: str) -> bool:
        """Reset password asynchronously."""
        return await asyncio.to_thread(self.reset_password_with_token, token, new_password)

    def get_theater_baton_state(self, theater_id: str) -> Optional[Dict[str, Any]]:
        dep = self.get_deployment(theater_id)
        if not dep:
            return None
        import json
        allowed_ids = json.loads(dep.get("allowed_orators") or "[]")
        active_orator_id = dep.get("active_orator_id") or dep["user_id"]
        baton_req_raw = dep.get("baton_request")
        baton_req = json.loads(baton_req_raw) if baton_req_raw else None
        
        if baton_req and "expires_at" in baton_req:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if now_iso > baton_req["expires_at"]:
                self.decline_baton(theater_id, baton_req.get("target_user_id"))
                baton_req = None

        users_by_id = self.get_users_by_ids(
            [dep["user_id"], active_orator_id, *allowed_ids]
        )
        owner_user = users_by_id.get(dep["user_id"])
        active_orator_user = users_by_id.get(active_orator_id)
        allowed_users = [
            {"id": user["id"], "username": user["username"]}
            for user_id in allowed_ids
            if (user := users_by_id.get(user_id))
        ]

        return {
            "theater_id": theater_id,
            "owner": {"id": owner_user["id"], "username": owner_user["username"]} if owner_user else None,
            "active_orator": {"id": active_orator_user["id"], "username": active_orator_user["username"]} if active_orator_user else None,
            "allowed_orators": allowed_users,
            "baton_request": baton_req,
        }

    def add_allowed_orator(self, theater_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(theater_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the theater owner can add allowed orators.")
        target_user = self.get_user_by_id(target_user_id)
        if not target_user:
            raise ValueError("Target user does not exist.")
        
        import json
        allowed_ids = json.loads(dep.get("allowed_orators") or "[]")
        if target_user_id not in allowed_ids:
            allowed_ids.append(target_user_id)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE theaters SET allowed_orators = ? WHERE theater_id = ?",
                    (json.dumps(allowed_ids), theater_id)
                )
                conn.commit()
        return self.get_theater_baton_state(theater_id)

    def remove_allowed_orator(self, theater_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(theater_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the theater owner can remove allowed orators.")
        
        import json
        allowed_ids = json.loads(dep.get("allowed_orators") or "[]")
        if target_user_id in allowed_ids:
            allowed_ids.remove(target_user_id)
        
        active_orator_id = dep.get("active_orator_id") or dep["user_id"]
        if active_orator_id == target_user_id:
            active_orator_id = owner_id
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE theaters SET allowed_orators = ?, active_orator_id = ? WHERE theater_id = ?",
                (json.dumps(allowed_ids), active_orator_id, theater_id)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    def request_baton(self, theater_id: str, owner_id: int, target_user_id: int, timeout_seconds: int = 30) -> Dict[str, Any]:
        dep = self.get_deployment(theater_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the theater owner can request passing the baton.")
        
        import json
        allowed_ids = json.loads(dep.get("allowed_orators") or "[]")
        if target_user_id not in allowed_ids and target_user_id != owner_id:
            raise ValueError("Target user is not in allowed orators.")
        
        target_user = self.get_user_by_id(target_user_id)
        if not target_user:
            raise ValueError("Target user does not exist.")
            
        now = datetime.datetime.now(datetime.timezone.utc)
        expires = now + datetime.timedelta(seconds=timeout_seconds)
        baton_req = {
            "target_user_id": target_user["id"],
            "target_username": target_user["username"],
            "requested_by_id": owner_id,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        }
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE theaters SET baton_request = ? WHERE theater_id = ?",
                (json.dumps(baton_req), theater_id)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    def accept_baton(self, theater_id: str, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(theater_id)
        if not dep:
            raise ValueError("Theater not found.")
        import json
        baton_req_raw = dep.get("baton_request")
        if not baton_req_raw:
            raise ValueError("No active baton request found.")
        baton_req = json.loads(baton_req_raw)
        if baton_req.get("target_user_id") != target_user_id:
            raise ValueError("Baton request is not for this user.")
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE theaters SET active_orator_id = ?, baton_request = NULL WHERE theater_id = ?",
                (target_user_id, theater_id)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    def decline_baton(self, theater_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE theaters SET baton_request = NULL WHERE theater_id = ?",
                (theater_id,)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    def take_back_baton(self, theater_id: str, owner_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(theater_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the theater owner can take back the baton.")
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE theaters SET active_orator_id = ?, baton_request = NULL WHERE theater_id = ?",
                (owner_id, theater_id)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    async def get_theater_baton_state_async(self, theater_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_theater_baton_state, theater_id)

    async def add_allowed_orator_async(self, theater_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.add_allowed_orator, theater_id, owner_id, target_user_id)

    async def remove_allowed_orator_async(self, theater_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.remove_allowed_orator, theater_id, owner_id, target_user_id)

    async def request_baton_async(self, theater_id: str, owner_id: int, target_user_id: int, timeout_seconds: int = 30) -> Dict[str, Any]:
        return await asyncio.to_thread(self.request_baton, theater_id, owner_id, target_user_id, timeout_seconds)

    async def accept_baton_async(self, theater_id: str, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.accept_baton, theater_id, target_user_id)

    async def decline_baton_async(self, theater_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self.decline_baton, theater_id, target_user_id)

    async def take_back_baton_async(self, theater_id: str, owner_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.take_back_baton, theater_id, owner_id)

    async def delete_user_async(self, user_id: int) -> bool:
        return await asyncio.to_thread(self.delete_user, user_id)


class LocalDatabaseManager(_DatabaseManagerBase):
    """Narratron storage backed by testing.postgresql, used for development and tests."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        pricing_controller: Optional[PricingController] = None,
    ):
        super().__init__(pricing_controller=pricing_controller)
        self.is_live = False
        self.db_path = db_path
        self._pg = None
        self._conn = None
        self._cached_db_path = None
        self._connection_lock = threading.RLock()

    def close(self) -> None:
        with self._connection_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            if self._pg is not None:
                try:
                    self._pg.stop()
                except Exception:
                    pass
                self._pg = None
            self._cached_db_path = None

    def _get_connection(self):
        with self._connection_lock:
            db_path = str(self.db_path) if self.db_path is not None else None
            if self._conn is not None and self._cached_db_path != db_path:
                self.close()
            if self._conn is None:
                import testing.postgresql
                self._pg = testing.postgresql.Postgresql(
                    base_dir=db_path if db_path and os.path.isdir(db_path) else None,
                    database=Path(db_path).stem if db_path and db_path != ":memory:" else "test",
                )
                raw_conn = self._pg.get_connection()
                self._conn = _ReusableConnection(
                    raw_conn,
                    is_dict_cursor=True,
                    lock=self._connection_lock,
                    sql_adapter=self._adapt_sql,
                )
                self._cached_db_path = db_path
                self._ensure_tables_exist()
                self._ensure_test_user()
            return self._conn

    def _ensure_test_user(self) -> None:
        """Ensure test mode user 'localtest' exists with password 'narratron' and 2000 credits."""
        if self._conn is None:
            return
        with self._conn as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, credits FROM users WHERE LOWER(username) = LOWER(?)", ("localtest",))
            row = cursor.fetchone()
            if not row:
                salt = secrets.token_hex(16)
                password_hash = hashlib.sha256(("narratron" + salt).encode("utf-8")).hexdigest()
                created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, salt, created_at, credits) "
                    "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                    ("localtest", "localtest@narratron.test", password_hash, salt, created_at, 2000.0),
                )
                conn.commit()
            else:
                cursor.execute(
                    "UPDATE users SET credits = ? WHERE LOWER(username) = LOWER(?)",
                    (2000.0, "localtest"),
                )
                conn.commit()


class CloudPostgresDatabaseManager(_DatabaseManagerBase):
    """Narratron storage backed by Cloud SQL PostgreSQL with IAM authentication."""

    def __init__(
        self,
        connection_name: Optional[str] = None,
        database: Optional[str] = None,
        iam_user: Optional[str] = None,
        pricing_controller: Optional[PricingController] = None,
        connection_timeout: Optional[float] = None,
        pool_size: Optional[int] = None,
        checkout_timeout: Optional[float] = None,
    ):
        super().__init__(pricing_controller=pricing_controller)
        self.is_live = True
        self.cloud_sql_instance = connection_name or os.environ.get("CLOUD_SQL_CONNECTION_NAME")
        self.cloud_sql_database = database or os.environ.get("CLOUD_SQL_DATABASE", "narratron-db")
        self.cloud_sql_iam_user = iam_user or os.environ.get("CLOUD_SQL_IAM_USER")
        self._cloud_sql_connector = None
        self._connection_lock = threading.RLock()
        self._live_connection_timeout = self._get_live_connection_timeout(connection_timeout)
        self._live_pool_size = self._get_live_pool_size(pool_size)
        self._live_checkout_timeout = self._get_live_checkout_timeout(checkout_timeout)
        self._live_pool: Optional[Queue] = None
        self._live_pool_total = 0
        self._live_pool_closed = False

    def close(self) -> None:
        with self._connection_lock:
            self._live_pool_closed = True
            pool, self._live_pool = self._live_pool, None
            self._live_pool_total = 0
            while pool is not None:
                try:
                    pool.get_nowait().close()
                except Empty:
                    break
            if self._cloud_sql_connector is not None:
                self._cloud_sql_connector.close()
                self._cloud_sql_connector = None

    def _get_connection(self):
        self._ensure_live_pool()
        return self._checkout_live_connection()

    def _open_live_connection(self):
        if not self.cloud_sql_instance or not self.cloud_sql_iam_user:
            raise ValueError("CLOUD_SQL_CONNECTION_NAME and CLOUD_SQL_IAM_USER are required for Cloud SQL IAM authentication.")
        try:
            from google.cloud.sql.connector import Connector
        except ImportError as exc:
            raise RuntimeError("Cloud SQL PostgreSQL requires cloud-sql-python-connector[pg8000].") from exc
        with self._connection_lock:
            if self._cloud_sql_connector is None:
                self._cloud_sql_connector = Connector()
            connector = self._cloud_sql_connector
        return connector.connect(
            self.cloud_sql_instance, "pg8000", user=self.cloud_sql_iam_user,
            db=self.cloud_sql_database, enable_iam_auth=True,
        )

    def _ensure_tables_exist(self) -> None:
        if self._live_pool is None:
            return
        try:
            with self._get_connection() as conn:
                conn.cursor().execute("SELECT 1 FROM users LIMIT 1")
        except Exception as exc:
            raise RuntimeError(
                "Cloud SQL schema is unavailable. Import storage/schema/postgres.sql before starting "
                f"the application. PostgreSQL reported: {exc}"
            ) from exc
