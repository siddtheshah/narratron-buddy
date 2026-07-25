"""Centralized, flag-gated filesystem paths for session data."""

from pathlib import Path

from absl import flags


flags.DEFINE_boolean(
    "use_cloud_session_storage",
    False,
    "Store session files under /tmp/sessions instead of the workspace sessions directory.",
)

FLAGS = flags.FLAGS


def get_sessions_root() -> Path:
    """Return the session-data root for the selected runtime environment."""
    if FLAGS["use_cloud_session_storage"].value:
        return Path("/tmp/sessions")
    return Path(__file__).parent.parent / "sessions"


def ensure_sessions_root() -> Path:
    """Return and create the selected session-data root."""
    sessions_root = get_sessions_root().resolve()
    sessions_root.mkdir(parents=True, exist_ok=True)
    return sessions_root
