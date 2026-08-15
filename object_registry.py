"""Application-wide objects and runtime flags.

Import this module when a component needs one of Narratron's shared services.
Keeping construction here ensures the HTTP routes, agent runtime, and test
configuration all operate on the same instances.
"""

import atexit
from contextlib import asynccontextmanager
from pathlib import Path
import sys

from absl import flags
from dotenv import load_dotenv
from fastapi import FastAPI

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from services.agent_manager import AgentSessionManager
from services.quirk_service import QuirkGeneratorService
from services.suggestion_service import SuggestionService
from storage.database import CloudPostgresDatabaseManager, LocalDatabaseManager
from utils.config_loader import get_app_config


# Repository-level paths are shared application constants, alongside the
# service instances below.  Route modules should import this from the registry
# rather than deriving their own location from ``__file__``.
PROJECT_ROOT = Path(__file__).resolve().parent


flags.DEFINE_boolean(
    "use_in_memory_artifacts",
    False,
    "Use PreloadedInMemoryArtifactService pre-loaded with test artifacts.",
)
flags.DEFINE_boolean(
    "testing_use_local_database",
    False,
    "Use local test database.",
)
flags.DEFINE_boolean(
    "allow_mock_payments",
    False,
    "Allow mock/simulated credit purchases when the live gateway is unconfigured.",
)
flags.DEFINE_string("host", "localhost", "Host to run the app on.")
flags.DEFINE_integer("port", 8000, "Port to run the app on.")
flags.DEFINE_string("log_prefix", "", "Only show logs containing this substring.")
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
async def lifespan(app: FastAPI):
    yield
    shutdown_database_connection()


load_dotenv()
config = get_app_config()
app = FastAPI(lifespan=lifespan)
theater_manager = TheaterManager()
pricing_controller = PricingController.from_env()
db = (
    LocalDatabaseManager("deployer.db", pricing_controller=pricing_controller)
    if FLAGS.testing_use_local_database or "pytest" in sys.modules
    else CloudPostgresDatabaseManager(
        pricing_controller=pricing_controller,
        connection_timeout=FLAGS.database_connection_timeout_seconds,
        pool_size=FLAGS.database_pool_size,
        checkout_timeout=FLAGS.database_pool_checkout_timeout_seconds,
    )
)
canvas_states = CanvasStateService(theater_manager)
agent_manager = AgentSessionManager(
    app_name="narratron-combined",
    config=config,
    theater_manager=theater_manager,
    database_manager=db,
)
suggestion_service = SuggestionService(config=config)

atexit.register(shutdown_database_connection)

