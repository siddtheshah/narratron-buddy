"""Application-wide objects and runtime flags.

Import this module when a component needs one of Narratron's shared services.
Keeping construction here ensures the HTTP routes, agent runtime, and test
configuration all operate on the same instances.
"""

import atexit
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
import uuid

from absl import flags
from dotenv import load_dotenv
from fastapi import FastAPI

from components.canvas.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from services.adventure_service import AdventureService, CACHE_CHECK_INTERVAL_SECONDS, ensure_adventures_root
from services.live_agent_manager import LiveAgentSessionManager
from services.music_catalog import MusicCatalog
from services.suggestion_service import SuggestionService
from services.text_beautifier import TextBeautifier
from storage.database import CloudPostgresDatabaseManager, LocalDatabaseManager
from storage.stamp_storage import StampStorage, ensure_stamps_root
from storage.theater_repository import TheaterRepository
from utils.config_loader import get_app_config

# Repository-level paths are shared application constants, alongside the
# service instances below.  Route modules should import this from the registry
# rather than deriving their own location from ``__file__``.
PROJECT_ROOT = Path(__file__).resolve().parent
SERVER_RUN_ID = str(uuid.uuid4())


flags.DEFINE_boolean(
    "use_in_memory_artifacts",
    False,
    "Use PreloadedInMemoryArtifactService pre-loaded with test artifacts.",
)
if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, theater repository) for testing and development.",
    )
flags.DEFINE_boolean(
    "with_empty_db",
    False,
    "Replace the local development database before starting (requires --testing_use_local).",
)
flags.DEFINE_boolean(
    "allow_mock_payments",
    False,
    "Allow mock/simulated credit purchases when the live gateway is unconfigured.",
)
flags.DEFINE_string("host", "localhost", "Host to run the app on.")
flags.DEFINE_integer("port", 8000, "Port to run the app on.")
flags.DEFINE_string("log_prefixes", "", "Comma-separated substrings; only matching logs are shown.")
flags.DEFINE_bool("suppress_polling", True, "Suppress frequent polling logs.")
flags.DEFINE_float(
    "database_connection_timeout_seconds",
    5.0,
    "Maximum duration of a live database operation.",
)
flags.DEFINE_integer(
    "database_pool_size",
    8,
    "Maximum number of live database connections.",
)
flags.DEFINE_float(
    "database_pool_checkout_timeout_seconds",
    5.0,
    "Maximum duration a request waits for an idle database connection.",
)

FLAGS = flags.FLAGS
sys.argv = FLAGS(sys.argv, known_only=True)


def shutdown_database_connection() -> None:
    """Close active database connections when the server receives Ctrl+C / shutdown signal."""
    db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Route modules are loaded by the time the ASGI lifespan begins. Rebuild
    # from rendered pages so search always reflects the deployed docs.
    from api_server.pages import rebuild_docs_search_index

    rebuild_docs_search_index()
    # Finish the first load before accepting requests; subsequent checks never
    # put filesystem scans on the card-listing request path.
    await asyncio.to_thread(adventure_service.list_adventures, force_refresh=True)
    stop = asyncio.Event()
    monitor = asyncio.create_task(monitor_adventure_catalog(stop))
    try:
        yield
    finally:
        stop.set()
        try:
            await monitor
        finally:
            shutdown_database_connection()


async def monitor_adventure_catalog(stop: asyncio.Event) -> None:
    """Check shared adventure storage without blocking the server event loop."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=CACHE_CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            try:
                await asyncio.to_thread(adventure_service.refresh_if_changed)
            except Exception:
                logging.getLogger(__name__).exception("Adventure catalog refresh failed; retaining cached cards.")


load_dotenv()
config = get_app_config()
app = FastAPI(lifespan=lifespan, docs_url="/fastapi-docs")


def invalidate_theater_storage(theater_id: str) -> None:
    """Resolve the active database when a working copy or saved package changes."""
    db.invalidate_theater_storage(theater_id)


theater_manager = TheaterManager(storage_changed=invalidate_theater_storage)
theater_repository = TheaterRepository(storage_changed=invalidate_theater_storage)
pricing_controller = PricingController.from_env()
local_database_path = PROJECT_ROOT / "deployer.db"
if FLAGS.testing_use_local and FLAGS.with_empty_db:
    # This is deliberately opt-in: local mode is also used to resume a canvas
    # after restarting the server, so its database must survive by default.
    for database_file in (
        local_database_path,
        local_database_path.with_name(f"{local_database_path.name}-wal"),
        local_database_path.with_name(f"{local_database_path.name}-shm"),
    ):
        database_file.unlink(missing_ok=True)
db = (
    LocalDatabaseManager(local_database_path, pricing_controller=pricing_controller)
    if FLAGS.testing_use_local or "pytest" in sys.modules
    else CloudPostgresDatabaseManager(
        pricing_controller=pricing_controller,
        connection_timeout=FLAGS.database_connection_timeout_seconds,
        pool_size=FLAGS.database_pool_size,
        checkout_timeout=FLAGS.database_pool_checkout_timeout_seconds,
    )
)
canvas_states = CanvasStateService(theater_manager)
music_catalog = MusicCatalog.from_config(
    config=config,
    database_manager=db,
)
live_agent_manager = LiveAgentSessionManager(
    app_name="narratron-combined",
    config=config,
    theater_manager=theater_manager,
    database_manager=db,
    music_catalog=music_catalog,
    canvas_state_service=canvas_states,
)
suggestion_service = SuggestionService(config=config)
adventure_service = AdventureService(ensure_adventures_root())
text_beautifier = TextBeautifier(config=config)
stamp_storage = StampStorage(ensure_stamps_root())

atexit.register(shutdown_database_connection)
