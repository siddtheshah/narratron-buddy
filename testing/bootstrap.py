"""Construct application singletons without credentials or developer-owned data."""

import atexit
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from providers.text_response_provider import TextResponseProvider


def initialize_test_registry() -> None:
    """Isolate import-time services; restore factories for provider unit tests."""
    workspace = TemporaryDirectory(prefix="narratron-tests-")
    # Register before importing the registry so its database closes first at exit.
    atexit.register(workspace.cleanup)
    root = Path(workspace.name)
    # SDKs must not discover credentials from the invoking shell or gcloud login.
    integration_prefixes = (
        "GOOGLE_", "GCP_", "GEMINI_", "VERTEX_", "OPENAI_", "OPEN_API_",
        "FAL_", "LYRIA_", "STRIPE_", "SMTP_", "STMP_", "CLOUD_SQL_",
        "GCS_", "TIKTOK_",
    )
    runtime_settings = {
        "DB_PATH", "THEATERS_DIR", "EPHEMERAL_DIR", "USE_LIVE_DB",
        "PUBLIC_BASE_URL", "LIVE_AGENT_TOOL_CALL_CREDIT_RATE",
        "THEATER_EDITOR_ASSISTANT_CREDIT_RATE",
    }
    for name in tuple(os.environ):
        if name.startswith(integration_prefixes) or name in runtime_settings:
            os.environ.pop(name)
    os.environ["CLOUDSDK_CONFIG"] = str(root / "gcloud")
    provider = MagicMock(spec=TextResponseProvider)
    provider.generate.side_effect = AssertionError(
        "Configure a text provider mock before generating text in a test."
    )
    with (
        patch("components.theater_manager.get_ephemeral_root", return_value=root / "ephemeral"),
        patch("storage.theater_repository.get_theaters_root", return_value=root / "theaters"),
        patch("storage.stamp_storage.get_stamps_root", return_value=root / "stamps"),
        patch("services.adventure_service.get_adventures_root", return_value=root / "adventures"),
        patch("services.music_catalog.get_music_catalog_root", return_value=root / "music_catalog"),
        patch("services.music_catalog.get_text_response_provider", return_value=provider),
        patch("services.text_beautifier.get_text_response_provider", return_value=provider),
        patch("storage.stamp_storage.StampStorage._init_gcs_client", return_value=None),
    ):
        import object_registry
        import utils.email_service  # noqa: F401

        object_registry.db.db_path = str(root / "deployer.db")
