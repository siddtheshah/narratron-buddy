"""Application-wide objects and runtime flags.

Import this module when a component needs one of Narratron's shared services.
Keeping construction here ensures the HTTP routes, agent runtime, and test
configuration all operate on the same instances.
"""

import sys
from pathlib import Path

from absl import flags
from dotenv import load_dotenv
from fastapi import FastAPI

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from services.agent_manager import AgentSessionManager
from storage.database import DatabaseManager
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
flags.DEFINE_bool(
    "use_local_test_db",
    False,
    "Use the local SQLite database for test authentication and deployments.",
)
flags.DEFINE_boolean(
    "testing_use_local_database",
    False,
    "Legacy alias for --use_local_test_db.",
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

FLAGS = flags.FLAGS
sys.argv = FLAGS(sys.argv, known_only=True)

load_dotenv()
config = get_app_config()
app = FastAPI()
theater_manager = TheaterManager()
pricing_controller = PricingController.from_env()
db = (
    DatabaseManager.from_local("deployer.db", pricing_controller=pricing_controller)
    if FLAGS.use_local_test_db or FLAGS.testing_use_local_database
    else DatabaseManager.from_live(pricing_controller=pricing_controller)
)
canvas_states = CanvasStateService(theater_manager)
agent_manager = AgentSessionManager(
    app_name="narratron-combined",
    config=config,
    theater_manager=theater_manager,
    database_manager=db,
)
