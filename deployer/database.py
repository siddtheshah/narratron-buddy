"""Database module for user management, authentication, and session deployment tracking using SQLite."""

import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import secrets
from typing import Any, Dict, List, Optional

logger = None
import logging
logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database storage for users, authentication tokens, and deployments."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            self.db_path = (Path(__file__).parent / "deployer.db").resolve()
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if users table exists and has id column
            cursor.execute("PRAGMA table_info(users)")
            cols = [row["name"] for row in cursor.fetchall()]
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
                    created_at TEXT NOT NULL
                )
            """)

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
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    join_key TEXT NOT NULL,
                    cost REAL DEFAULT 5.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS exported_sessions (
                    session_id TEXT PRIMARY KEY,
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
                    session_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    category TEXT NOT NULL,
                    image_data BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES exported_sessions(session_id) ON DELETE CASCADE
                )
            """)
            conn.commit()


    @staticmethod
    def _hash_password(password: str, salt_bytes: Optional[bytes] = None) -> tuple[str, str]:
        if salt_bytes is None:
            salt_bytes = os.urandom(16)
        pwd_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_bytes,
            100000
        )
        return pwd_hash.hex(), salt_bytes.hex()

    def register_user(self, username: str, email: str, password: str) -> Dict:
        """Register a new user account with hashed password and default 100 credits."""
        username = username.strip()
        email = email.strip().lower()
        
        if not username or not email or not password:
            raise ValueError("Username, email, and password are required.")

        pwd_hash, salt_hex = self._hash_password(password)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
                    (username, email, pwd_hash, salt_hex, now_iso)
                )
                user_id = cursor.lastrowid
                conn.commit()
                return self.get_user_by_id(user_id)
        except sqlite3.IntegrityError:
            raise ValueError("Username or email already exists.")

    def authenticate_user(self, username_or_email: str, password: str) -> Optional[Dict]:
        """Authenticate user credentials and return user dict if valid."""
        query_val = username_or_email.strip()
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
            salt_bytes = bytes.fromhex(user_dict["salt"])
            computed_hash, _ = self._hash_password(password, salt_bytes)

            if secrets.compare_digest(computed_hash, user_dict["password_hash"]):
                return {
                    "id": user_dict["id"],
                    "username": user_dict["username"],
                    "email": user_dict["email"],
                    "credits": user_dict["credits"],
                    "created_at": user_dict["created_at"]
                }
            return None

    def create_auth_session(self, user_id: int, days_valid: int = 7) -> str:
        """Create a new session token for an authenticated user."""
        token = secrets.token_hex(32)
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

    def validate_session_token(self, token: str) -> Optional[Dict]:
        """Validate auth session token and return user profile if valid."""
        if not token:
            return None

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.id, u.username, u.email, u.credits, u.created_at, s.expires_at
                FROM auth_sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > ?
            """, (token, now_iso))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def invalidate_session_token(self, token: str) -> bool:
        """Delete an auth session token on logout."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            conn.commit()
            return cursor.rowcount > 0

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, credits, created_at FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def record_deployment(self, session_id: str, user_id: int, join_key: str, cost: float = 5.0) -> bool:
        """Record deployment in database and deduct cost from user credits."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Check user credits
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user or user["credits"] < cost:
                raise ValueError("Insufficient credits for session deployment.")

            cursor.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ?",
                (cost, user_id)
            )
            cursor.execute(
                "INSERT INTO canvas_deployments (session_id, user_id, join_key, cost, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, join_key, cost, now_iso)
            )
            conn.commit()
            return True

    def get_deployment(self, session_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM canvas_deployments WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_deployment(self, session_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM canvas_deployments WHERE session_id = ?", (session_id,))
            cursor.execute("DELETE FROM exported_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_session_by_join_key(self, join_key: str) -> Optional[Dict]:
        clean_key = join_key.strip().upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM canvas_deployments WHERE UPPER(join_key) = ?", (clean_key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_exported_session_ids(self) -> List[str]:
        """Get all distinct session IDs stored in exported_sessions or canvas_deployments."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id FROM exported_sessions
                UNION
                SELECT session_id FROM canvas_deployments
            """)
            return [row["session_id"] for row in cursor.fetchall() if row["session_id"]]

    def get_session_metadata_from_db(self, session_id: str) -> Optional[Dict]:
        """Extract metadata dictionary for a session stored in database without reconstructing disk files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE session_id = ?", (session_id,))
            session_row = cursor.fetchone()
            
            cursor.execute("SELECT * FROM canvas_deployments WHERE session_id = ?", (session_id,))
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
                name = session_row["name"] if session_row else session_id
                join_key = dep_row["join_key"] if dep_row else "KEY-DEFAULT"
                created_at = session_row["exported_at"] if session_row else (dep_row["created_at"] if dep_row else "")
                metadata = {
                    "session_id": session_id,
                    "name": name,
                    "status": "deployed",
                    "join_key": join_key,
                    "created_at": created_at,
                    "mounted_references": [],
                    "mounted_playlists": {},
                    "config": {}
                }
            return metadata

    def export_session_to_db(
        self,
        session_id: str,
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
                INSERT INTO exported_sessions (session_id, user_id, name, exported_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id = coalesce(excluded.user_id, exported_sessions.user_id),
                    name = coalesce(excluded.name, exported_sessions.name),
                    exported_at = excluded.exported_at,
                    state_json = excluded.state_json
                """,
                (session_id, user_id, name or session_id, now_iso, state_json)
            )

            # Clear previous images for this session
            cursor.execute("DELETE FROM exported_session_images WHERE session_id = ?", (session_id,))

            for img in image_files:
                cursor.execute(
                    """
                    INSERT INTO exported_session_images (session_id, filename, category, image_data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, img["filename"], img.get("category", "output"), img["data"], now_iso)
                )
            conn.commit()
            return True

    def get_exported_session(self, session_id: str) -> Optional[Dict]:
        """Fetch exported session record and list of image files."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE session_id = ?", (session_id,))
            session_row = cursor.fetchone()
            if not session_row:
                return None
            
            res = dict(session_row)
            res["state"] = json.loads(res["state_json"])
            
            cursor.execute("SELECT id, filename, category, created_at FROM exported_session_images WHERE session_id = ?", (session_id,))
            res["images"] = [dict(r) for r in cursor.fetchall()]
            return res

    def reconstruct_session_from_db(self, session_id: str, target_dir: Path) -> bool:
        """Reconstruct session folder, session metadata, state, and files from database if missing."""
        import json
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM exported_sessions WHERE session_id = ?", (session_id,))
            session_row = cursor.fetchone()
            if not session_row:
                # Check if deployment exists even if not exported yet
                cursor.execute("SELECT * FROM canvas_deployments WHERE session_id = ?", (session_id,))
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
            name = session_id

            if session_row:
                session_dict = dict(session_row)
                user_id = session_dict.get("user_id")
                name = session_dict.get("name") or session_id
                state_json_str = session_dict.get("state_json", "{}")
                try:
                    state_data = json.loads(state_json_str)
                except Exception:
                    state_data = {}

            # Restore or write single session.json containing metadata and canvas_state
            metadata = state_data.get("metadata") or dict(state_data)
            if "session_id" not in metadata:
                metadata["session_id"] = session_id
            if "name" not in metadata:
                metadata["name"] = name
            if "status" not in metadata:
                metadata["status"] = "deployed"
            if "join_key" not in metadata:
                metadata["join_key"] = "KEY-DEFAULT"
                cursor.execute("SELECT join_key FROM canvas_deployments WHERE session_id = ?", (session_id,))
                dep_row = cursor.fetchone()
                if dep_row and dep_row["join_key"]:
                    metadata["join_key"] = dep_row["join_key"]
            if "created_at" not in metadata:
                metadata["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # Ensure canvas_state is included if state_data has canvas_state or UI properties
            if "canvas_state" not in metadata and "canvas_state" in state_data:
                metadata["canvas_state"] = state_data["canvas_state"]
            elif "canvas_state" not in metadata:
                c_fields = ["current_image_basename", "shown_image_path", "shown_image_prompt", "shown_images_history", "current_playlist", "current_playlist_tracks", "music_paused", "doodles", "chat_messages"]
                c_dict = {k: state_data[k] for k in c_fields if k in state_data}
                if c_dict:
                    metadata["canvas_state"] = c_dict

            meta_file = target_dir / "session.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            # Clean up legacy session_state.json if it exists
            legacy_file = target_dir / "session_state.json"
            if legacy_file.exists():
                try:
                    legacy_file.unlink()
                except Exception:
                    pass

            # Restore exported images/files
            cursor.execute("SELECT filename, category, image_data FROM exported_session_images WHERE session_id = ?", (session_id,))
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

