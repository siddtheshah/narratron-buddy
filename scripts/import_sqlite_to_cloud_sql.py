"""One-time, idempotent import from a Narratron SQLite file to Cloud SQL.

Run only after the Cloud SQL IAM database user exists and has CREATE/INSERT
privileges. The source remains untouched. Re-running is safe while the target
has no newer writes because every insert conflicts on its primary key.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import CloudPostgresDatabaseManager


TABLES = (
    "users",
    "auth_sessions",
    "theaters",
    "theater_views",
    "payment_transactions",
    "usage_events",
    "password_reset_tokens",
)
IDENTITY_TABLES = ("users", "theater_views", "payment_transactions")


def apply_schema(connection) -> None:
    """Apply the PostgreSQL bootstrap schema using the migration principal."""
    schema_path = Path(__file__).parents[1] / "storage" / "schema" / "postgres.sql"
    cursor = connection.cursor()
    for statement in schema_path.read_text(encoding="utf-8").split(";"):
        statement = "\n".join(
            line for line in statement.splitlines() if not line.lstrip().startswith("--")
        ).strip()
        if statement:
            cursor.execute(statement)
    connection.commit()


def target_schema_exists(connection) -> bool:
    """Return whether the target already contains the Narratron base table."""
    cursor = connection.cursor()
    cursor.execute("SELECT to_regclass('public.users')")
    row = cursor.fetchone()
    return bool(row and row[0])


def import_database(source: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source}")

    source_conn = sqlite3.connect(source)
    source_conn.row_factory = sqlite3.Row
    target = CloudPostgresDatabaseManager()
    try:
        # Schema creation is an explicit migration action. The app runtime has
        # no CREATE permission and only verifies this schema exists.
        bootstrap_connection = target._open_live_connection()
        try:
            if target_schema_exists(bootstrap_connection):
                print("Target schema already exists; skipping schema bootstrap.")
            else:
                apply_schema(bootstrap_connection)
        finally:
            bootstrap_connection.close()
        target._ensure_live_pool()
        with target._get_connection() as destination:
            cursor = destination.cursor()
            for table in TABLES:
                rows = source_conn.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    print(f"{table}: 0 rows")
                    continue
                columns = rows[0].keys()
                column_sql = ", ".join(columns)
                value_sql = ", ".join("?" for _ in columns)
                statement = f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql}) ON CONFLICT DO NOTHING"
                for row in rows:
                    values = []
                    for column in columns:
                        value = row[column]
                        if (table, column) in {
                            ("users", "stats_visible"),
                            ("theaters", "is_persistent"),
                            ("password_reset_tokens", "used"),
                        } and value is not None:
                            value = bool(value)
                        values.append(value)
                    cursor.execute(statement, tuple(values))
                cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                print(f"{table}: {len(rows)} source rows; {cursor.fetchone()['count']} target rows")

            for table in IDENTITY_TABLES:
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
                    (table, "id"),
                )
    finally:
        source_conn.close()
        target.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(r"C:\Users\sidds\Downloads\narratron3.db"),
        help="Path to the downloaded SQLite database.",
    )
    args = parser.parse_args()
    import_database(args.source)
