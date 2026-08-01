"""Database module for user management, authentication, and theater deployment tracking using SQLite."""

import asyncio
import datetime
import hashlib
import os
import json
from pathlib import Path
import sqlite3
import secrets
from typing import Any, Dict, List, Optional
import logging
import libsql
from dotenv import load_dotenv
from pricing.pricing_controller import PricingController

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

    def __init__(self, is_live: bool, db_path: Optional[str] = None, pricing_controller: Optional[PricingController] = None):
        self.is_live = is_live
        self.db_path = db_path
        self._conn = None
        self._cached_db_path = None
        self._cached_is_live = None
        self.pricing_controller = pricing_controller or PricingController.from_env()

    def get_pricing_rates(self) -> Dict[str, float]:
        """Return current pricing rates dictionary for polling."""
        return self.pricing_controller.get_rates()

    @classmethod
    def from_live(cls, pricing_controller: Optional[PricingController] = None) -> "DatabaseManager":
        return cls(is_live=True, db_path=None, pricing_controller=pricing_controller)

    @classmethod
    def from_local(cls, db_path: str, pricing_controller: Optional[PricingController] = None) -> "DatabaseManager":
        return cls(is_live=False, db_path=db_path, pricing_controller=pricing_controller)

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
                    credits REAL DEFAULT 25.0,
                    total_voice_minutes REAL DEFAULT 0.0,
                    total_images_created INTEGER DEFAULT 0,
                    mic_sensitivity REAL DEFAULT 0.5,
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
                    cursor.execute("ALTER TABLE users ADD COLUMN credits REAL DEFAULT 25.0")
                except Exception:
                    pass

            if "total_voice_minutes" not in user_cols:
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN total_voice_minutes REAL DEFAULT 0.0")
                except Exception:
                    pass

            if "total_images_created" not in user_cols:
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN total_images_created INTEGER DEFAULT 0")
                except Exception:
                    pass

            if "mic_sensitivity" not in user_cols:
                try:
                    cursor.execute("ALTER TABLE users ADD COLUMN mic_sensitivity REAL DEFAULT 0.5")
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
                    theater_id TEXT PRIMARY KEY,
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
                CREATE TABLE IF NOT EXISTS exported_theaters (
                    theater_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    name TEXT,
                    exported_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exported_theater_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theater_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    category TEXT NOT NULL,
                    image_data BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (theater_id) REFERENCES exported_theaters(theater_id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS theater_views (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theater_id TEXT NOT NULL,
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
            if "is_persistent" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN is_persistent INTEGER DEFAULT 0")
                except Exception:
                    pass
            if "last_billed_at" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN last_billed_at TEXT DEFAULT NULL")
                except Exception:
                    pass
            if "theater_config" not in cd_cols:
                try:
                    cursor.execute("ALTER TABLE canvas_deployments ADD COLUMN theater_config TEXT DEFAULT NULL")
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

            
            transaction_cols = set(_get_cols(cursor, "payment_transactions"))
            if "stripe_session_id" not in transaction_cols:
                cursor.execute("ALTER TABLE payment_transactions ADD COLUMN stripe_session_id TEXT")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_transactions_stripe_session "
                "ON payment_transactions(stripe_session_id) WHERE stripe_session_id IS NOT NULL"
            )

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
                    "INSERT INTO users (username, email, password_hash, salt, created_at, credits) VALUES (?, ?, ?, ?, ?, 25.0)",
                    (username_clean, email_clean, password_hash, salt, created_at)
                )
                user_id = cursor.lastrowid
                conn.commit()
                return {
                    "id": user_id,
                    "username": username_clean,
                    "email": email_clean,
                    "credits": 25.0,
                    "total_voice_minutes": 0.0,
                    "total_images_created": 0,
                    "mic_sensitivity": 0.5,
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
                    "credits": user_dict.get("credits", 25.0),
                    "total_voice_minutes": user_dict.get("total_voice_minutes", 0.0),
                    "total_images_created": user_dict.get("total_images_created", 0),
                    "mic_sensitivity": user_dict.get("mic_sensitivity", 0.5),
                    "created_at": user_dict["created_at"]
                }
            return None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, credits, total_voice_minutes, total_images_created, mic_sensitivity, created_at FROM users WHERE id = ?", (user_id,))
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
                SELECT u.id, u.username, u.email, u.credits, u.total_voice_minutes, u.total_images_created, u.mic_sensitivity, u.created_at, s.expires_at
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
                    SELECT user_id FROM canvas_deployments WHERE created_at >= ?
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
                       COALESCE(es.name, cd.theater_id, v.theater_id) as name
                FROM theater_views v
                LEFT JOIN exported_theaters es ON v.theater_id = es.theater_id
                LEFT JOIN canvas_deployments cd ON v.theater_id = cd.theater_id
                GROUP BY v.theater_id
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
                        SELECT user_id FROM canvas_deployments WHERE created_at >= ? AND created_at < ?
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

    def record_deployment(self, theater_id: str, user_id: int, join_key: str, cost: float = 0.0, is_persistent: bool = False, theater_config: Optional[Dict[str, Any]] = None) -> bool:
        """Record deployment in database and deduct cost from user credits."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        config_str = json.dumps(theater_config) if isinstance(theater_config, dict) else theater_config
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user or user["credits"] < cost:
                raise ValueError("Insufficient credits for theater deployment.")

            cursor.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ?",
                (cost, user_id)
            )
            persistent_val = 1 if is_persistent else 0
            last_billed_val = now_iso if is_persistent else None
            cursor.execute(
                "INSERT INTO canvas_deployments (theater_id, user_id, join_key, cost, created_at, is_persistent, last_billed_at, theater_config) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (theater_id, user_id, join_key, cost, now_iso, persistent_val, last_billed_val, config_str)
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
            
            cursor.execute("SELECT id, username, email, credits, total_voice_minutes, total_images_created, created_at FROM users WHERE id = ?", (user_id,))
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
            cursor.execute(
                "SELECT id FROM payment_transactions WHERE stripe_session_id = ?", (stripe_session_id,)
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "SELECT id, username, email, credits, total_voice_minutes, total_images_created, created_at "
                    "FROM users WHERE id = ?", (user_id,)
                )
                user = cursor.fetchone()
                return {"transaction_id": existing[0], "user": dict(user), "credits_added": 0.0,
                        "amount_usd": usd_amount, "created_at": now_iso, "already_credited": True}

            cursor.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (credits_amount, user_id))
            if cursor.rowcount == 0:
                raise ValueError("User not found.")
            cursor.execute(
                "INSERT INTO payment_transactions "
                "(user_id, amount_usd, credits_added, payment_method, status, created_at, stripe_session_id) "
                "VALUES (?, ?, ?, ?, 'completed', ?, ?)",
                (user_id, usd_amount, credits_amount, payment_method, now_iso, stripe_session_id),
            )
            tx_id = cursor.lastrowid
            cursor.execute(
                "SELECT id, username, email, credits, total_voice_minutes, total_images_created, created_at "
                "FROM users WHERE id = ?", (user_id,)
            )
            updated_user = dict(cursor.fetchone())
            conn.commit()
            return {"transaction_id": tx_id, "user": updated_user, "credits_added": credits_amount,
                    "amount_usd": usd_amount, "created_at": now_iso, "already_credited": False}

    def record_user_usage(
        self,
        user_id: int,
        voice_minutes: float = 0.0,
        images_created: int = 0,
        credit_cost: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record voice minutes used and images created for a user while simultaneously updating credit balance.

        Credits are allowed to go negative per user settings/preferences.
        """
        if voice_minutes < 0 or images_created < 0:
            raise ValueError("Usage parameters (voice_minutes, images_created) must be non-negative.")

        if credit_cost is None:
            credit_cost = self.pricing_controller.calculate_usage_cost(
                voice_minutes=voice_minutes, images_created=images_created
            )
        elif credit_cost < 0:
            raise ValueError("credit_cost must be non-negative.")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET total_voice_minutes = total_voice_minutes + ?,
                    total_images_created = total_images_created + ?,
                    credits = credits - ?
                WHERE id = ?
                """,
                (voice_minutes, images_created, credit_cost, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("User not found.")

            cursor.execute(
                "SELECT id, username, email, credits, total_voice_minutes, total_images_created, created_at FROM users WHERE id = ?",
                (user_id,),
            )
            updated_user = dict(cursor.fetchone())
            conn.commit()
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
            cursor.execute("SELECT * FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("theater_config") and isinstance(res["theater_config"], str):
                try:
                    res["theater_config"] = json.loads(res["theater_config"])
                except Exception:
                    pass
            return res

    def save_theater_config(self, theater_id: str, config_data: Dict[str, Any]) -> bool:
        """Update theater_config column for an existing deployment."""
        config_str = json.dumps(config_data) if isinstance(config_data, dict) else config_data
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE canvas_deployments SET theater_config = ? WHERE theater_id = ?",
                (config_str, theater_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_deployment(self, theater_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
            c1 = cursor.rowcount or 0
            cursor.execute("DELETE FROM exported_theaters WHERE theater_id = ?", (theater_id,))
            c2 = cursor.rowcount or 0
            conn.commit()
            return (c1 + c2) > 0


    def set_theater_persistence(self, theater_id: str, is_persistent: bool) -> bool:
        """Set whether a theater session is marked as persistent."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
            dep = cursor.fetchone()
            if not dep:
                return False
            
            persistent_val = 1 if is_persistent else 0
            if is_persistent:
                last_billed = (dep["last_billed_at"] if isinstance(dep, dict) else dep[9]) if "last_billed_at" in dep else now_iso
                if not last_billed:
                    last_billed = now_iso
                cursor.execute(
                    "UPDATE canvas_deployments SET is_persistent = ?, last_billed_at = ? WHERE theater_id = ?",
                    (persistent_val, last_billed, theater_id)
                )
            else:
                cursor.execute(
                    "UPDATE canvas_deployments SET is_persistent = ? WHERE theater_id = ?",
                    (persistent_val, theater_id)
                )
            conn.commit()
            return True

    def get_theater_persistence(self, theater_id: str) -> bool:
        """Check if a theater session is marked persistent."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_persistent FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
            row = cursor.fetchone()
            if not row:
                return False
            val = row["is_persistent"] if isinstance(row, dict) else row[0]
            return bool(val)

    def storage_daemon(
        self,
        local_deployer: Any = None,
        ttl_seconds: float = 604800.0,
        hourly_cost: float = 0.004167,
        current_time: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Alias for run_database_daemon: process non-persistent storage cleanup and persistent session billing."""
        return self.run_database_daemon(
            local_deployer=local_deployer,
            ttl_seconds=ttl_seconds,
            hourly_cost=hourly_cost,
            current_time=current_time,
        )

    def run_database_daemon(
        self,
        local_deployer: Any = None,
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
            cursor.execute("SELECT * FROM canvas_deployments")
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
                        cursor.execute("DELETE FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
                        cursor.execute("DELETE FROM exported_theaters WHERE theater_id = ?", (theater_id,))
                        if local_deployer:
                            try:
                                local_deployer.destroy_theater(theater_id)
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
                            cursor.execute("UPDATE users SET credits = credits - ? WHERE id = ?", (charge_amount, user_id))
                            cursor.execute("UPDATE canvas_deployments SET last_billed_at = ? WHERE theater_id = ?", (new_last_billed, theater_id))
                            accrued_charges.append({
                                "theater_id": theater_id,
                                "user_id": user_id,
                                "amount": charge_amount,
                                "hours": hours_to_bill,
                            })
                            logger.info(f"[DatabaseDaemon] Accrued {charge_amount} credits charge for persistent theater_id={theater_id}")
                        else:
                            logger.warning(f"[DatabaseDaemon] User user_id={user_id} has insufficient credits ({user_credits}) for persistent theater_id={theater_id}. Expiring session.")
                            cursor.execute("DELETE FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
                            cursor.execute("DELETE FROM exported_theaters WHERE theater_id = ?", (theater_id,))
                            if local_deployer:
                                try:
                                    local_deployer.destroy_theater(theater_id)
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
            cursor.execute("SELECT * FROM canvas_deployments WHERE UPPER(join_key) = ?", (clean_key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_exported_theater_ids(self) -> List[str]:
        """Get all distinct theater IDs stored in exported_theaters or canvas_deployments."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT theater_id FROM exported_theaters
                UNION
                SELECT theater_id FROM canvas_deployments
            """)
            return [row["theater_id"] for row in cursor.fetchall() if row["theater_id"]]

    def get_theaters_last_used(self) -> Dict[str, str]:
        """Return a mapping of theater_id to its most recent activity timestamp (ISO string)."""
        activity_map: Dict[str, str] = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. canvas_deployments created_at / last_billed_at
            cursor.execute("SELECT theater_id, created_at, last_billed_at FROM canvas_deployments")
            for row in cursor.fetchall():
                tid = row["theater_id"]
                if tid:
                    ts = row["last_billed_at"] or row["created_at"]
                    if ts and (tid not in activity_map or ts > activity_map[tid]):
                        activity_map[tid] = ts

            # 2. exported_theaters exported_at
            cursor.execute("SELECT theater_id, exported_at FROM exported_theaters")
            for row in cursor.fetchall():
                tid = row["theater_id"]
                ts = row["exported_at"]
                if tid and ts and (tid not in activity_map or ts > activity_map[tid]):
                    activity_map[tid] = ts

            # 3. theater_views viewed_at
            cursor.execute("SELECT theater_id, MAX(viewed_at) as last_viewed FROM theater_views GROUP BY theater_id")
            for row in cursor.fetchall():
                tid = row["theater_id"]
                ts = row["last_viewed"]
                if tid and ts and (tid not in activity_map or ts > activity_map[tid]):
                    activity_map[tid] = ts

        return activity_map

    def get_theater_metadata_from_db(self, theater_id: str) -> Optional[Dict]:
        """Extract metadata dictionary for a theater stored in database without reconstructing disk files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_theaters WHERE theater_id = ?", (theater_id,))
            theater_row = cursor.fetchone()
            
            cursor.execute("SELECT * FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
            dep_row = cursor.fetchone()

            if not theater_row and not dep_row:
                return None

            metadata = None
            if theater_row:
                try:
                    state_data = json.loads(theater_row["state_json"])
                    metadata = state_data.get("metadata")
                except Exception:
                    pass

            if not metadata:
                name = theater_row["name"] if theater_row else theater_id
                join_key = dep_row["join_key"] if dep_row else "KEY-DEFAULT"
                created_at = theater_row["exported_at"] if theater_row else (dep_row["created_at"] if dep_row else "")
                metadata = {
                    "theater_id": theater_id,
                    "name": name,
                    "status": "deployed",
                    "join_key": join_key,
                    "created_at": created_at,
                    "mounted_references": [],
                    "mounted_playlists": {},
                    "config": {}
                }
            elif metadata:
                metadata["theater_id"] = metadata.get("theater_id") or theater_id
            return metadata

    def export_theater_to_db(
        self,
        theater_id: str,
        state_data: Dict,
        image_files: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        name: Optional[str] = None
    ) -> bool:
        """Export theater metadata, state, and image blobs into SQLite database."""
        import json
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state_json = json.dumps(state_data)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO exported_theaters (theater_id, user_id, name, exported_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(theater_id) DO UPDATE SET
                    user_id = coalesce(excluded.user_id, exported_theaters.user_id),
                    name = coalesce(excluded.name, exported_theaters.name),
                    exported_at = excluded.exported_at,
                    state_json = excluded.state_json
                """,
                (theater_id, user_id, name or theater_id, now_iso, state_json)
            )

            # Clear previous images for this theater
            cursor.execute("DELETE FROM exported_theater_images WHERE theater_id = ?", (theater_id,))

            for img in image_files:
                cursor.execute(
                    """
                    INSERT INTO exported_theater_images (theater_id, filename, category, image_data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (theater_id, img["filename"], img.get("category", "output"), img["data"], now_iso)
                )
            conn.commit()
            return True

    def get_exported_theater(self, theater_id: str) -> Optional[Dict]:
        """Fetch exported theater record and list of image files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_theaters WHERE theater_id = ?", (theater_id,))
            theater_row = cursor.fetchone()
            if not theater_row:
                return None
            
            res = dict(theater_row)
            res["state"] = json.loads(res["state_json"])
            
            cursor.execute("SELECT id, filename, category, created_at FROM exported_theater_images WHERE theater_id = ?", (theater_id,))
            res["images"] = [dict(r) for r in cursor.fetchall()]
            return res

    def reconstruct_theater_from_db(self, theater_id: str, target_dir: Path) -> bool:
        """Reconstruct theater folder, theater metadata, state, and files from database if missing."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_theaters WHERE theater_id = ?", (theater_id,))
            theater_row = cursor.fetchone()
            if not theater_row:
                cursor.execute("SELECT * FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
                dep_row = cursor.fetchone()
                if not dep_row:
                    return False
                theater_row = None

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
            name = theater_id

            if theater_row:
                theater_dict = dict(theater_row)
                user_id = theater_dict.get("user_id")
                name = theater_dict.get("name") or theater_id
                state_json_str = theater_dict.get("state_json", "{}")
                try:
                    state_data = json.loads(state_json_str)
                except Exception:
                    state_data = {}

            metadata = state_data.get("metadata") or dict(state_data)
            if "theater_id" not in metadata:
                metadata["theater_id"] = theater_id
            if "name" not in metadata:
                metadata["name"] = name
            if "status" not in metadata:
                metadata["status"] = "deployed"
            if "join_key" not in metadata:
                metadata["join_key"] = "KEY-DEFAULT"
                cursor.execute("SELECT join_key FROM canvas_deployments WHERE theater_id = ?", (theater_id,))
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

            meta_file = target_dir / "theater.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            legacy_file = target_dir / "theater_state.json"
            if legacy_file.exists():
                try:
                    legacy_file.unlink()
                except Exception:
                    pass

            cursor.execute("SELECT filename, category, image_data FROM exported_theater_images WHERE theater_id = ?", (theater_id,))
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

    async def export_theater_to_db_async(
        self,
        theater_id: str,
        state_data: Dict,
        image_files: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        name: Optional[str] = None
    ) -> bool:
        """Export theater to database asynchronously."""
        try:
            return await asyncio.to_thread(
                self.export_theater_to_db, theater_id, state_data, image_files, user_id, name
            )
        except Exception:
            logger.exception("Failed to export theater '%s' to database", theater_id)
            return False

    async def persist_canvas_theater_async(
        self, canvas_states: Any, local_deployer: Any, theater_id: str, user_id: Optional[int], name: str
    ) -> bool:
        """Snapshot canvas state and save assets to database asynchronously."""
        try:
            def _export_and_save():
                theater_dir = local_deployer._get_theater_dir(theater_id)
                state_data, image_files = canvas_states.get(theater_id).export_theater_data(
                    theater_dir=theater_dir
                )
                return self.export_theater_to_db(
                    theater_id=theater_id,
                    state_data=state_data,
                    image_files=image_files,
                    user_id=user_id,
                    name=name,
                )
            success = await asyncio.to_thread(_export_and_save)
            if success:
                logger.info("Theater '%s' saved to database asynchronously.", theater_id)
            return success
        except Exception:
            logger.exception("Failed to save theater '%s' to database", theater_id)
            return False

    async def record_deployment_async(
        self, theater_id: str, user_id: int, join_key: str, cost: float = 0.0
    ) -> bool:
        """Record deployment asynchronously."""
        return await asyncio.to_thread(
            self.record_deployment, theater_id, user_id, join_key, cost
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
        credit_cost: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record user usage asynchronously."""
        return await asyncio.to_thread(
            self.record_user_usage, user_id, voice_minutes, images_created, credit_cost
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

        owner_user = self.get_user_by_id(dep["user_id"])
        active_orator_user = self.get_user_by_id(active_orator_id)
        
        allowed_users = []
        for uid in allowed_ids:
            u = self.get_user_by_id(uid)
            if u:
                allowed_users.append({"id": u["id"], "username": u["username"]})

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
                    "UPDATE canvas_deployments SET allowed_orators = ? WHERE theater_id = ?",
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
                "UPDATE canvas_deployments SET allowed_orators = ?, active_orator_id = ? WHERE theater_id = ?",
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
                "UPDATE canvas_deployments SET baton_request = ? WHERE theater_id = ?",
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
                "UPDATE canvas_deployments SET active_orator_id = ?, baton_request = NULL WHERE theater_id = ?",
                (target_user_id, theater_id)
            )
            conn.commit()
        return self.get_theater_baton_state(theater_id)

    def decline_baton(self, theater_id: str, target_user_id: Optional[int] = None) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE canvas_deployments SET baton_request = NULL WHERE theater_id = ?",
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
                "UPDATE canvas_deployments SET active_orator_id = ?, baton_request = NULL WHERE theater_id = ?",
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
