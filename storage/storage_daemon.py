"""Storage Daemon for Narratron Buddy.

Standalone background process that runs on a persistent schedule to:
1. Automatically purge non-persistent theater sessions older than the TTL (7 days default).
2. Accrue credit charges for persistent theater sessions.
3. Automatically expire persistent sessions if user credit balance is depleted.
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure project root is in python path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from storage.database import CloudPostgresDatabaseManager, LocalDatabaseManager
from storage.theater_repository import TheaterRepository
from components.theater_manager import TheaterManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("storage_daemon")


class StorageDaemon:
    """Daemon runner managing scheduled storage cleanup and billing cycles."""

    def __init__(
        self,
        db: Optional[Any] = None,
        theater_repository: Optional[TheaterRepository] = None,
        theater_manager: Optional[TheaterManager] = None,
        interval_seconds: float = 60.0,
        ttl_seconds: float = 604800.0,
        hourly_cost: float = 0.004167,
    ):
        self.interval_seconds = float(os.getenv("STORAGE_INTERVAL_SECONDS", str(interval_seconds)))
        self.ttl_seconds = float(os.getenv("STORAGE_TTL_SECONDS", str(ttl_seconds)))
        self.hourly_cost = float(os.getenv("STORAGE_HOURLY_COST", str(hourly_cost)))

        if db is not None:
            self.db = db
        else:
            use_live = os.getenv("USE_LIVE_DB", "false").lower() in ("true", "1", "yes")
            db_path = os.getenv("DB_PATH", "deployer.db")
            if use_live:
                logger.info("Initializing DatabaseManager in LIVE mode (Cloud SQL PostgreSQL).")
                self.db = CloudPostgresDatabaseManager()
            else:
                logger.info(f"Initializing DatabaseManager in LOCAL mode with db_path={db_path}.")
                self.db = LocalDatabaseManager(db_path)

        if theater_repository is not None:
            self.theater_repository = theater_repository
        else:
            theaters_dir = os.getenv("THEATERS_DIR")
            self.theater_repository = TheaterRepository(base_dir=theaters_dir)

        if theater_manager is not None:
            self.theater_manager = theater_manager
        else:
            ephemeral_dir = os.getenv("EPHEMERAL_DIR")
            self.theater_manager = TheaterManager(base_theaters_dir=ephemeral_dir)

        self._running = False

    def run_once(self) -> dict:
        """Perform a single iteration of the storage cleanup and billing cycle."""
        logger.info("Starting storage cleanup and billing cycle...")
        result = self.db.storage_daemon(
            theater_repository=self.theater_repository,
            theater_manager=self.theater_manager,
            ttl_seconds=self.ttl_seconds,
            hourly_cost=self.hourly_cost,
        )
        cleaned = result.get("cleaned_up_sessions", [])
        charges = result.get("accrued_charges", [])
        logger.info(
            f"Cycle finished. Cleaned sessions: {len(cleaned)} ({cleaned}), "
            f"Accrued charges: {len(charges)} ({charges})"
        )
        return result

    def start_loop(self):
        """Run the daemon in an infinite loop on a persistent schedule until interrupted."""
        self._running = True
        logger.info(
            f"Starting persistent storage_daemon loop (interval={self.interval_seconds}s, "
            f"ttl={self.ttl_seconds}s, hourly_cost={self.hourly_cost})."
        )

        def handle_signal(sig, frame):
            logger.info(f"Signal {sig} received. Shutting down storage_daemon gracefully...")
            self._running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        while self._running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error during storage cleanup cycle: {e}", exc_info=True)

            # Sleep in small increments for responsive shutdown signal handling
            elapsed = 0.0
            while self._running and elapsed < self.interval_seconds:
                time.sleep(min(1.0, self.interval_seconds - elapsed))
                elapsed += 1.0

        logger.info("storage_daemon stopped successfully.")
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description="Narratron Storage Daemon")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single storage cleanup cycle and exit.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="Sleep interval between cleanup cycles in seconds (default: 60.0).",
    )
    parser.add_argument(
        "--ttl",
        type=float,
        default=604800.0,
        help="TTL for non-persistent sessions in seconds (default: 604800.0 / 7 days).",
    )
    parser.add_argument(
        "--hourly-cost",
        type=float,
        default=0.004167,
        help="Hourly charge rate in credits for persistent sessions (default: 0.004167 = 0.1 Cr/day flat rate per PRICING.md).",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to local SQLite database file.",
    )
    parser.add_argument(
        "--use-live-db",
        action="store_true",
        help="Connect to live Cloud SQL PostgreSQL database.",
    )

    args = parser.parse_args()

    db = None
    if args.use_live_db:
        db = CloudPostgresDatabaseManager()
    elif args.db_path:
        db = LocalDatabaseManager(args.db_path)

    daemon = StorageDaemon(
        db=db,
        interval_seconds=args.interval,
        ttl_seconds=args.ttl,
        hourly_cost=args.hourly_cost,
    )

    if args.once:
        daemon.run_once()
    else:
        daemon.start_loop()


if __name__ == "__main__":
    main()
