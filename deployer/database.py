"""Database module for user management, authentication, and session deployment tracking using SQLite."""

import asyncio
import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import secrets
from typing import Any, Dict, List, Optional
import logging
import libsql
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class _DictCursor:
    """Wraps a DB-API cursor so fetchone/fetchall return dicts keyed by column name.

    libsql does not support row_factory, so this uses cursor.description
    to map tuple positions to column names after each query.
    """

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        self._cursor.execute(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def fetchall(self):
        return [self._row_to_dict(r) for r in self._cursor.fetchall()]

    def _row_to_dict(self, row):
        if self._cursor.description is None:
            return row
        cols = [col[0] for col in self._cursor.description]
        return dict(zip(cols, row))

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ReusableConnection:
    """Wraps a DB-API connection so context managers commit or rollback without closing the underlying connection."""

    def __init__(self, conn, is_dict_cursor: bool = False):
        self._conn = conn
        self._is_dict_cursor = is_dict_cursor

    def cursor(self):
        if self._is_dict_cursor:
            return _DictCursor(self._conn.cursor())
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


class DatabaseManager:
    """Manages SQLite database storage for users, authentication tokens, and deployments."""

    def __init__(self, is_live: bool, db_path: Optional[str] = None):
        self.is_live = is_live
        self.db_path = db_path
        self._conn = None
        self._cached_db_path = None
        self._cached_is_live = None

    @classmethod
    def from_live(cls) -> "DatabaseManager":
        return cls(is_live=True, db_path=None)

    @classmethod
    def from_local(cls, db_path: str) -> "DatabaseManager":
        return cls(is_live=False, db_path=db_path)

    def close(self) -> None:
        """Close the active cached database connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._cached_db_path = None
        self._cached_is_live = None

    def _get_connection(self):
        str_db_path = str(self.db_path) if self.db_path is not None else None
        if (
            self._conn is not None
            and (self._cached_db_path != str_db_path or self._cached_is_live != self.is_live)
        ):
            self.close()

        if self._conn is None:
            if not self.is_live:
                db_file = str(self.db_path or "deployer.db")
                conn = sqlite3.connect(db_file, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                self._conn = _ReusableConnection(conn, is_dict_cursor=False)
                self._ensure_tables_exist()
            else:
                try:
                    turso_url = os.environ.get("TURSO_DATABASE_URL")
                    turso_token = os.environ.get("TURSO_DB_TOKEN")
                    if not turso_url or not turso_token:
                        raise ValueError("Missing Turso database credentials.")
                    conn = libsql.connect(
                        database=turso_url,
                        auth_token=turso_token
                    )
                    self._conn = _ReusableConnection(conn, is_dict_cursor=True)
                    self._ensure_tables_exist()
                except BaseException as e:
                    logger.warning("Live database connection unavailable (%s), falling back to local SQLite.", e)
                    db_file = str(self.db_path or "deployer.db")
                    conn = sqlite3.connect(db_file, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL")
                    self._conn = _ReusableConnection(conn, is_dict_cursor=False)
                    self._ensure_tables_exist()
            self._cached_db_path = str_db_path
            self._cached_is_live = self.is_live

        return self._conn

    def _ensure_tables_exist(self) -> None:
        if getattr(self, "_initializing_tables", False):
            return
        self._initializing_tables = True
        try:
            self._init_db()
        finally:
            self._initializing_tables = False

    def _init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        def _get_cols(cursor, table_name: str) -> List[str]:
            cursor.execute(f"PRAGMA table_info({table_name})")
            rows = cursor.fetchall()
            cols = []
            for r in rows:
                if isinstance(r, dict):
                    cols.append(r.get("name"))
                elif hasattr(r, "keys"):
                    cols.append(r["name"])
                elif isinstance(r, (tuple, list)):
                    cols.append(r[1])
            return cols

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if users table exists and has id column
            cols = _get_cols(cursor, "users")
            if cols and "id" not in cols:
                cursor.execute("DROP TABLE IF EXISTS auth_sessions")
                cursor.execute("DROP TABLE IF EXISTS canvas_deployments")
                cursor.execute("DROP TABLE IF EXISTS users")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    credits REAL DEFAULT 100.0,
                    created_at TEXT NOT NULL,
                    last_active_at TEXT
                )
            """)

            user_cols = _get_cols(cursor, "users")
            if "last_active_at" not in user_cols:
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN last_active_at TEXT")
                except Exception:
                    pass

            if "credits" not in user_cols:
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN credits REAL DEFAULT 100.0")
                except Exception:
                    pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS canvas_deployments (
                    narratron_session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    join_key TEXT NOT NULL,
                    cost REAL DEFAULT 5.0,
                    created_at TEXT NOT NULL,
                    allowed_orators TEXT DEFAULT '[]',
                    active_orator_id INTEGER DEFAULT NULL,
                    baton_request TEXT DEFAULT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exported_sessions (
                    narratron_session_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    name TEXT,
                    exported_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exported_session_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    narratron_session_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    category TEXT NOT NULL,
                    image_data BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (narratron_session_id) REFERENCES exported_sessions(narratron_session_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_views (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    narratron_session_id TEXT NOT NULL,
                    user_id INTEGER,
                    viewed_at TEXT NOT NULL,
                    ip_address TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                )
            """)

            cd_cols = _get_cols(cursor, "canvas_deployments")
            if "allowed_orators" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN allowed_orators TEXT DEFAULT '[]'")
                except Exception:
                    pass
            if "active_orator_id" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN active_orator_id INTEGER DEFAULT NULL")
                except Exception:
                    pass
            if "baton_request" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN baton_request TEXT DEFAULT NULL")
                except Exception:
                    pass

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payment_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_usd REAL NOT NULL,
                    credits_added REAL NOT NULL,
                    payment_method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            conn.commit()

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
                    "INSERT INTO users (username, email, password_hash, salt, created_at, credits) VALUES (?, ?, ?, ?, ?, 100.0)",
                    (username_clean, email_clean, password_hash, salt, created_at)
                )
                user_id = cursor.lastrowid
                conn.commit()
                return {
                    "id": user_id,
                    "username": username_clean,
                    "email": email_clean,
                    "credits": 100.0,
                    "created_at": created_at
                }
            except sqlite3.IntegrityError as e:
                err_msg = str(e).lower()
                if "username" in err_msg:
                    raise ValueError("Username already exists.")
                elif "email" in err_msg:
                    raise ValueError("Email already registered.")
                else:
                    raise ValueError("Username or email already exists.")

    def authenticate_user(self, username_or_email: str, password: str) -> Optional[Dict]:
        """Authenticate user credentials."""
        query_val = username_or_email.strip()
        if not query_val or not password:
            return None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
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
                    "credits": user_dict.get("credits", 100.0),
                    "created_at": user_dict["created_at"]
                }
            return None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, credits, created_at FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

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
                SELECT u.id, u.username, u.email, u.credits, u.created_at, s.expires_at
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

    def record_user_activity(self, user_id: int) -> bool:
        """Update last_active_at timestamp for user."""
        if not user_id:
            return False
        return True

    def record_session_view(self, narratron_session_id: str, user_id: Optional[int] = None, ip_address: Optional[str] = None) -> bool:
        """Record a session view event in database."""
        if not narratron_session_id:
            return False
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO session_views (narratron_session_id, user_id, viewed_at, ip_address) VALUES (?, ?, ?, ?)",
                    (narratron_session_id, user_id, now_iso, ip_address)
                )
                conn.commit()
                if user_id:
                    self.record_user_activity(user_id)
                return True
        except Exception as e:
            logger.error(f"Error recording session view: {e}")
            return False

    def get_stats_summary(self) -> Dict[str, Any]:
        """Fetch stats summary including account counts, active users (7d), and session views."""
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
                    SELECT user_id FROM canvas_deployments WHERE created_at >= ?
                    UNION
                    SELECT user_id FROM session_views WHERE user_id IS NOT NULL AND viewed_at >= ?
                )
            """, (seven_days_ago, seven_days_ago, seven_days_ago, seven_days_ago, seven_days_ago))
            active_users_7d = cursor.fetchone()["count"]

            # 3. Session views
            cursor.execute("SELECT COUNT(*) as count FROM session_views")
            total_session_views = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM session_views WHERE viewed_at >= ?", (seven_days_ago,))
            session_views_7d = cursor.fetchone()["count"]

            # 4. Top viewed sessions
            cursor.execute("""
                SELECT v.narratron_session_id as narratron_session_id, COUNT(*) as views,
                       COALESCE(es.name, cd.narratron_session_id, v.narratron_session_id) as name
                FROM session_views v
                LEFT JOIN exported_sessions es ON v.narratron_session_id = es.narratron_session_id
                LEFT JOIN canvas_deployments cd ON v.narratron_session_id = cd.narratron_session_id
                GROUP BY v.narratron_session_id
                ORDER BY views DESC
                LIMIT 10
            """)
            top_viewed_sessions = [dict(r) for r in cursor.fetchall()]

            # 5. Daily session views for past 7 days
            daily_views_7d = []
            for i in range(6, -1, -1):
                day_start = (now - datetime.timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + datetime.timedelta(days=1)
                day_str = day_start.strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT COUNT(*) as count FROM session_views WHERE viewed_at >= ? AND viewed_at < ?",
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
                        SELECT user_id FROM canvas_deployments WHERE created_at >= ? AND created_at < ?
                        UNION
                        SELECT user_id FROM session_views WHERE user_id IS NOT NULL AND viewed_at >= ? AND viewed_at < ?
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
                "total_session_views": total_session_views,
                "session_views_7d": session_views_7d,
                "top_viewed_sessions": top_viewed_sessions,
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

    def record_deployment(self, narratron_session_id: str, user_id: int, join_key: str, cost: float = 5.0) -> bool:
        """Record deployment in database and deduct cost from user credits."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user or user["credits"] < cost:
                raise ValueError("Insufficient credits for session deployment.")

            cursor.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ?",
                (cost, user_id)
            )
            cursor.execute(
                "INSERT INTO canvas_deployments (narratron_session_id, user_id, join_key, cost, created_at) VALUES (?, ?, ?, ?, ?)",
                (narratron_session_id, user_id, join_key, cost, now_iso)
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
                   VALUES (?, ?, ?, ?, 'completed', ?)""",
                (user_id, usd_amount, credits_amount, payment_method, now_iso)
            )
            tx_id = cursor.lastrowid
            
            cursor.execute("SELECT id, username, email, credits FROM users WHERE id = ?", (user_id,))
            updated_user = dict(cursor.fetchone())
            conn.commit()
            return {
                "transaction_id": tx_id,
                "user": updated_user,
                "credits_added": credits_amount,
                "amount_usd": usd_amount,
                "created_at": now_iso
            }

    def get_user_transactions(self, user_id: int) -> List[Dict[str, Any]]:
        """Retrieve payment transaction history for a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM payment_transactions WHERE user_id = ? ORDER BY id DESC",
                (user_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_deployment(self, narratron_session_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM canvas_deployments WHERE narratron_session_id = ?", (narratron_session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_deployment(self, narratron_session_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM canvas_deployments WHERE narratron_session_id = ?", (narratron_session_id,))
            cursor.execute("DELETE FROM exported_sessions WHERE narratron_session_id = ?", (narratron_session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_session_by_join_key(self, join_key: str) -> Optional[Dict]:
        clean_key = join_key.strip().upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM canvas_deployments WHERE UPPER(join_key) = ?", (clean_key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_exported_narratron_session_ids(self) -> List[str]:
        """Get all distinct session IDs stored in exported_sessions or canvas_deployments."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT narratron_session_id FROM exported_sessions
                UNION
                SELECT narratron_session_id FROM canvas_deployments
            """)
            return [row["narratron_session_id"] for row in cursor.fetchall() if row["narratron_session_id"]]

    def get_session_metadata_from_db(self, narratron_session_id: str) -> Optional[Dict]:
        """Extract metadata dictionary for a session stored in database without reconstructing disk files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE narratron_session_id = ?", (narratron_session_id,))
            session_row = cursor.fetchone()
            
            cursor.execute("SELECT * FROM canvas_deployments WHERE narratron_session_id = ?", (narratron_session_id,))
            dep_row = cursor.fetchone()

            if not session_row and not dep_row:
                return None

            metadata = None
            if session_row:
                try:
                    state_data = json.loads(session_row["state_json"])
                    metadata = state_data.get("metadata")
                except Exception:
                    pass

            if not metadata:
                name = session_row["name"] if session_row else narratron_session_id
                join_key = dep_row["join_key"] if dep_row else "KEY-DEFAULT"
                created_at = session_row["exported_at"] if session_row else (dep_row["created_at"] if dep_row else "")
                metadata = {
                    "narratron_session_id": narratron_session_id,
                    "name": name,
                    "status": "deployed",
                    "join_key": join_key,
                    "created_at": created_at,
                    "mounted_references": [],
                    "mounted_playlists": {},
                    "config": {}
                }
            elif metadata:
                metadata["narratron_session_id"] = metadata.get("narratron_session_id") or narratron_session_id
            return metadata

    def export_session_to_db(
        self,
        narratron_session_id: str,
        state_data: Dict,
        image_files: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        name: Optional[str] = None
    ) -> bool:
        """Export session metadata, state, and image blobs into SQLite database."""
        import json
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state_json = json.dumps(state_data)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO exported_sessions (narratron_session_id, user_id, name, exported_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(narratron_session_id) DO UPDATE SET
                    user_id = coalesce(excluded.user_id, exported_sessions.user_id),
                    name = coalesce(excluded.name, exported_sessions.name),
                    exported_at = excluded.exported_at,
                    state_json = excluded.state_json
                """,
                (narratron_session_id, user_id, name or narratron_session_id, now_iso, state_json)
            )

            # Clear previous images for this session
            cursor.execute("DELETE FROM exported_session_images WHERE narratron_session_id = ?", (narratron_session_id,))

            for img in image_files:
                cursor.execute(
                    """
                    INSERT INTO exported_session_images (narratron_session_id, filename, category, image_data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (narratron_session_id, img["filename"], img.get("category", "output"), img["data"], now_iso)
                )
            conn.commit()
            return True

    def get_exported_session(self, narratron_session_id: str) -> Optional[Dict]:
        """Fetch exported session record and list of image files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE narratron_session_id = ?", (narratron_session_id,))
            session_row = cursor.fetchone()
            if not session_row:
                return None
            
            res = dict(session_row)
            res["state"] = json.loads(res["state_json"])
            
            cursor.execute("SELECT id, filename, category, created_at FROM exported_session_images WHERE narratron_session_id = ?", (narratron_session_id,))
            res["images"] = [dict(r) for r in cursor.fetchall()]
            return res

    def reconstruct_session_from_db(self, narratron_session_id: str, target_dir: Path) -> bool:
        """Reconstruct session folder, session metadata, state, and files from database if missing."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE narratron_session_id = ?", (narratron_session_id,))
            session_row = cursor.fetchone()
            if not session_row:
                cursor.execute("SELECT * FROM canvas_deployments WHERE narratron_session_id = ?", (narratron_session_id,))
                dep_row = cursor.fetchone()
                if not dep_row:
                    return False
                session_row = None

            target_dir = Path(target_dir).resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            out_dir = target_dir / "output"
            ref_dir = target_dir / "references"
            pl_dir = target_dir / "playlists"
            out_dir.mkdir(parents=True, exist_ok=True)
            ref_dir.mkdir(parents=True, exist_ok=True)
            pl_dir.mkdir(parents=True, exist_ok=True)

            state_data = {}
            user_id = None
            name = narratron_session_id

            if session_row:
                session_dict = dict(session_row)
                user_id = session_dict.get("user_id")
                name = session_dict.get("name") or narratron_session_id
                state_json_str = session_dict.get("state_json", "{}")
                try:
                    state_data = json.loads(state_json_str)
                except Exception:
                    state_data = {}

            metadata = state_data.get("metadata") or dict(state_data)
            if "narratron_session_id" not in metadata:
                metadata["narratron_session_id"] = narratron_session_id
            if "name" not in metadata:
                metadata["name"] = name
            if "status" not in metadata:
                metadata["status"] = "deployed"
            if "join_key" not in metadata:
                metadata["join_key"] = "KEY-DEFAULT"
                cursor.execute("SELECT join_key FROM canvas_deployments WHERE narratron_session_id = ?", (narratron_session_id,))
                dep_row = cursor.fetchone()
                if dep_row and dep_row["join_key"]:
                    metadata["join_key"] = dep_row["join_key"]
            if "created_at" not in metadata:
                metadata["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            if "canvas_state" not in metadata and "canvas_state" in state_data:
                metadata["canvas_state"] = state_data["canvas_state"]
            elif "canvas_state" not in metadata:
                c_fields = ["current_image_basename", "shown_image_path", "shown_image_prompt", "shown_images_history", "shown_image_transition", "current_playlist", "current_playlist_tracks", "music_paused", "doodles", "doodles_enabled", "chat_messages"]
                c_dict = {k: state_data[k] for k in c_fields if k in state_data}
                if c_dict:
                    metadata["canvas_state"] = c_dict

            meta_file = target_dir / "session.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            legacy_file = target_dir / "session_state.json"
            if legacy_file.exists():
                try:
                    legacy_file.unlink()
                except Exception:
                    pass

            cursor.execute("SELECT filename, category, image_data FROM exported_session_images WHERE narratron_session_id = ?", (narratron_session_id,))
            image_rows = cursor.fetchall()

            for row in image_rows:
                cat = row["category"]
                fn = row["filename"]
                data = row["image_data"]
                
                if cat == "reference" or cat == "references":
                    dest_file = ref_dir / fn
                elif cat == "output":
                    dest_file = out_dir / fn
                elif cat.startswith("references/"):
                    dest_file = target_dir / cat if cat.endswith(fn) else target_dir / cat / fn
                elif cat.startswith("output/") or cat.startswith("playlists/") or cat.startswith("chats/"):
                    dest_file = target_dir / cat if cat.endswith(fn) else target_dir / cat / fn
                else:
                    dest_file = target_dir / cat / fn

                dest_file.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_file, "wb") as f:
                    f.write(data)

            return True

    # ========================================
    # Async Methods (Write & Non-Blocking Operations)
    # ========================================

    async def record_session_view_async(
        self, narratron_session_id: str, user_id: Optional[int] = None, ip_address: Optional[str] = None
    ) -> bool:
        """Record session view asynchronously."""
        try:
            return await asyncio.to_thread(
                self.record_session_view, narratron_session_id, user_id, ip_address
            )
        except Exception:
            logger.exception("Failed to record view for session '%s'", narratron_session_id)
            return False

    async def export_session_to_db_async(
        self,
        narratron_session_id: str,
        state_data: Dict,
        image_files: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        name: Optional[str] = None
    ) -> bool:
        """Export session to database asynchronously."""
        try:
            return await asyncio.to_thread(
                self.export_session_to_db, narratron_session_id, state_data, image_files, user_id, name
            )
        except Exception:
            logger.exception("Failed to export session '%s' to database", narratron_session_id)
            return False

    async def persist_canvas_session_async(
        self, canvas_states: Any, local_deployer: Any, narratron_session_id: str, user_id: Optional[int], name: str
    ) -> bool:
        """Snapshot canvas state and save assets to database asynchronously."""
        try:
            def _export_and_save():
                session_dir = local_deployer._get_session_dir(narratron_session_id)
                state_data, image_files = canvas_states.get(narratron_session_id).export_session_data(
                    session_dir=session_dir
                )
                return self.export_session_to_db(
                    narratron_session_id=narratron_session_id,
                    state_data=state_data,
                    image_files=image_files,
                    user_id=user_id,
                    name=name,
                )
            success = await asyncio.to_thread(_export_and_save)
            if success:
                logger.info("Session '%s' saved to database asynchronously.", narratron_session_id)
            return success
        except Exception:
            logger.exception("Failed to save session '%s' to database", narratron_session_id)
            return False

    async def record_deployment_async(
        self, narratron_session_id: str, user_id: int, join_key: str, cost: float = 5.0
    ) -> bool:
        """Record deployment asynchronously."""
        return await asyncio.to_thread(
            self.record_deployment, narratron_session_id, user_id, join_key, cost
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

    async def reset_password_with_token_async(self, token: str, new_password: str) -> bool:
        """Reset password asynchronously."""
        return await asyncio.to_thread(self.reset_password_with_token, token, new_password)

    def get_session_baton_state(self, narratron_session_id: str) -> Optional[Dict[str, Any]]:
        dep = self.get_deployment(narratron_session_id)
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
                self.decline_baton(narratron_session_id, baton_req.get("target_user_id"))
                baton_req = None

        owner_user = self.get_user_by_id(dep["user_id"])
        active_orator_user = self.get_user_by_id(active_orator_id)
        
        allowed_users = []
        for uid in allowed_ids:
            u = self.get_user_by_id(uid)
            if u:
                allowed_users.append({"id": u["id"], "username": u["username"]})

        return {
            "narratron_session_id": narratron_session_id,
            "owner": {"id": owner_user["id"], "username": owner_user["username"]} if owner_user else None,
            "active_orator": {"id": active_orator_user["id"], "username": active_orator_user["username"]} if active_orator_user else None,
            "allowed_orators": allowed_users,
            "baton_request": baton_req,
        }

    def add_allowed_orator(self, narratron_session_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(narratron_session_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the session owner can add allowed orators.")
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
                    "UPDATE canvas_deployments SET allowed_orators = ? WHERE narratron_session_id = ?",
                    (json.dumps(allowed_ids), narratron_session_id)
                )
                conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    def remove_allowed_orator(self, narratron_session_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(narratron_session_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the session owner can remove allowed orators.")
        
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
                "UPDATE canvas_deployments SET allowed_orators = ?, active_orator_id = ? WHERE narratron_session_id = ?",
                (json.dumps(allowed_ids), active_orator_id, narratron_session_id)
            )
            conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    def request_baton(self, narratron_session_id: str, owner_id: int, target_user_id: int, timeout_seconds: int = 30) -> Dict[str, Any]:
        dep = self.get_deployment(narratron_session_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the session owner can request passing the baton.")
        
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
                "UPDATE canvas_deployments SET baton_request = ? WHERE narratron_session_id = ?",
                (json.dumps(baton_req), narratron_session_id)
            )
            conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    def accept_baton(self, narratron_session_id: str, target_user_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(narratron_session_id)
        if not dep:
            raise ValueError("Session not found.")
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
                "UPDATE canvas_deployments SET active_orator_id = ?, baton_request = NULL WHERE narratron_session_id = ?",
                (target_user_id, narratron_session_id)
            )
            conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    def decline_baton(self, narratron_session_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE canvas_deployments SET baton_request = NULL WHERE narratron_session_id = ?",
                (narratron_session_id,)
            )
            conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    def take_back_baton(self, narratron_session_id: str, owner_id: int) -> Dict[str, Any]:
        dep = self.get_deployment(narratron_session_id)
        if not dep or dep["user_id"] != owner_id:
            raise ValueError("Only the session owner can take back the baton.")
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE canvas_deployments SET active_orator_id = ?, baton_request = NULL WHERE narratron_session_id = ?",
                (owner_id, narratron_session_id)
            )
            conn.commit()
        return self.get_session_baton_state(narratron_session_id)

    async def get_session_baton_state_async(self, narratron_session_id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_session_baton_state, narratron_session_id)

    async def add_allowed_orator_async(self, narratron_session_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.add_allowed_orator, narratron_session_id, owner_id, target_user_id)

    async def remove_allowed_orator_async(self, narratron_session_id: str, owner_id: int, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.remove_allowed_orator, narratron_session_id, owner_id, target_user_id)

    async def request_baton_async(self, narratron_session_id: str, owner_id: int, target_user_id: int, timeout_seconds: int = 30) -> Dict[str, Any]:
        return await asyncio.to_thread(self.request_baton, narratron_session_id, owner_id, target_user_id, timeout_seconds)

    async def accept_baton_async(self, narratron_session_id: str, target_user_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.accept_baton, narratron_session_id, target_user_id)

    async def decline_baton_async(self, narratron_session_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self.decline_baton, narratron_session_id, target_user_id)

    async def take_back_baton_async(self, narratron_session_id: str, owner_id: int) -> Dict[str, Any]:
        return await asyncio.to_thread(self.take_back_baton, narratron_session_id, owner_id)
