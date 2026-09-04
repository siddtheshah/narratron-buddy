"""Ephemeral PostgreSQL database manager for tests.

Provides a SQLite-backed adapter compatible with PostgreSQL schemas and queries.
"""

import math
import os
import shutil
import sqlite3
import re
import tempfile
import threading
from typing import Any, Dict, Optional


def _translate_pg_to_sqlite(sql: str) -> str:
    """Translate PostgreSQL DDL and DML constructs to SQLite for testing environments."""
    s = sql.strip()
    if not s:
        return ""

    # Remove postgres comments
    s = "\n".join(line for line in s.splitlines() if not line.lstrip().startswith("--")).strip()
    if not s:
        return ""

    # Translate parameter placeholders
    s = s.replace("%s", "?")

    # Types translation
    s = re.sub(r"\bBIGINT\s+GENERATED\s+BY\s+DEFAULT\s+AS\s+IDENTITY\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", s, flags=re.IGNORECASE)
    s = re.sub(r"\bBIGINT\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDOUBLE\s+PRECISION\b", "REAL", s, flags=re.IGNORECASE)
    s = re.sub(r"\bBOOLEAN\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bTRUE\b", "1", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFALSE\b", "0", s, flags=re.IGNORECASE)

    # Remove PostgreSQL-specific cast syntax like ::TEXT or ::DOUBLE PRECISION
    s = re.sub(r"::\w+(?:\s+\w+)?", "", s)

    # Translate ON CONFLICT (col) DO NOTHING -> ON CONFLICT DO NOTHING
    s = re.sub(r"\bON\s+CONFLICT\s*\([^)]+\)\s*DO\s+NOTHING\b", "ON CONFLICT DO NOTHING", s, flags=re.IGNORECASE)

    # Handle ALTER TABLE ADD COLUMN IF NOT EXISTS
    m_alter = re.match(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+(.+)", s, flags=re.IGNORECASE)
    if m_alter:
        table, col, col_def = m_alter.group(1), m_alter.group(2), m_alter.group(3)
        return f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"

    return s


class _PostgresTestCursor:
    """Cursor wrapper that supports PostgreSQL RETURNING semantics and standard DB-API tuple rows."""

    def __init__(self, raw_cursor):
        self._raw = raw_cursor
        self._returning_rows = None
        self._returning_desc = None

    def execute(self, sql: str, params: Any = ()):
        translated = _translate_pg_to_sqlite(sql)
        if not translated:
            return self

        # Check for RETURNING clause
        returning_match = re.search(r"\bRETURNING\s+([a-zA-Z0-9_,\s]+)$", translated, flags=re.IGNORECASE)
        returning_cols = None
        if returning_match:
            returning_cols = [c.strip() for c in returning_match.group(1).split(",")]
            translated = translated[:returning_match.start()].strip()

        # Check for ALTER TABLE failure if column already exists
        if translated.upper().startswith("ALTER TABLE") and "ADD COLUMN" in translated.upper():
            try:
                self._raw.execute(translated, params)
            except Exception as exc:
                if "duplicate column" in str(exc).lower():
                    return self
                raise
            return self

        self._raw.execute(translated, params)

        if returning_cols:
            self._returning_desc = [(col, None) for col in returning_cols]
            if "id" in returning_cols and self._raw.rowcount > 0 and self._raw.lastrowid is not None:
                self._returning_rows = [(self._raw.lastrowid,)]
            else:
                self._returning_rows = []
        else:
            self._returning_rows = None
            self._returning_desc = None

        return self

    def fetchone(self):
        if self._returning_rows is not None:
            if self._returning_rows:
                return self._returning_rows.pop(0)
            return None
        row = self._raw.fetchone()
        if row is None:
            return None
        return tuple(row)

    def fetchall(self):
        if self._returning_rows is not None:
            rows = self._returning_rows
            self._returning_rows = []
            return rows
        return [tuple(r) for r in self._raw.fetchall()]

    @property
    def description(self):
        if self._returning_desc is not None:
            return self._returning_desc
        return self._raw.description

    @property
    def rowcount(self):
        return self._raw.rowcount

    @property
    def lastrowid(self):
        return self._raw.lastrowid

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _PostgresTestConnection:
    """Connection wrapper mimicking PostgreSQL connection for testing."""

    def __init__(self, sqlite_conn):
        self._conn = sqlite_conn

    def cursor(self):
        return _PostgresTestCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

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


class Postgresql:
    """Ephemeral PostgreSQL test instance container."""

    def __init__(self, base_dir: Optional[str] = None, database: str = "test", **kwargs):
        self.database = database
        self.base_dir = base_dir or tempfile.mkdtemp(prefix="pg_test_")
        self._is_temp = base_dir is None
        self._db_file = os.path.join(self.base_dir, f"{database}.db")
        self._conns = []
        self._lock = threading.Lock()

    def dsn(self) -> Dict[str, Any]:
        return {
            "database": self.database,
            "host": "127.0.0.1",
            "port": 5432,
            "user": "postgres",
        }

    def url(self) -> str:
        return f"postgresql://postgres@127.0.0.1:5432/{self.database}"

    def get_connection(self) -> _PostgresTestConnection:
        with self._lock:
            conn = sqlite3.connect(self._db_file, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.create_function("LN", 1, math.log)
            conn.execute("PRAGMA foreign_keys = ON")
            wrapped = _PostgresTestConnection(conn)
            self._conns.append(conn)
            return wrapped

    def stop(self) -> None:
        with self._lock:
            for conn in self._conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._conns.clear()
            if self._is_temp and os.path.exists(self.base_dir):
                shutil.rmtree(self.base_dir, ignore_errors=True)

    def cleanup(self) -> None:
        self.stop()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class PostgresqlFactory:
    """Factory creating Postgresql instances."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __call__(self) -> Postgresql:
        return Postgresql(**self.kwargs)

    def clear_cache(self) -> None:
        pass
