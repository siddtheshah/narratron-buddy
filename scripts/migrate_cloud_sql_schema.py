"""Apply idempotent Narratron PostgreSQL schema migrations to Cloud SQL.

Run this after deploying a release that adds columns to storage/schema/postgres.sql.
It uses the configured Cloud SQL IAM connection and only executes the additive
column migrations needed by an already-bootstrapped application database. It
must be run as the owning Cloud SQL role (not the application IAM user).
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.database import CloudPostgresDatabaseManager


MIGRATIONS = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_character_voiced_turns INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS character_voiced_turns INTEGER NOT NULL DEFAULT 0",
)


def apply_schema_migrations(connection) -> None:
    cursor = connection.cursor()
    for statement in MIGRATIONS:
        cursor.execute(statement)
    connection.commit()


if __name__ == "__main__":
    database = CloudPostgresDatabaseManager()
    connection = database._open_live_connection()
    try:
        apply_schema_migrations(connection)
        print("Cloud SQL schema migrations applied successfully.")
    finally:
        connection.close()
        database.close()
