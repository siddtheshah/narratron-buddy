"""Centralized, flag-gated filesystem paths for theater data."""

from pathlib import Path

from absl import flags


flags.DEFINE_boolean(
    "use_cloud_theater_storage",
    False,
    "Store theater files under /tmp/theaters instead of the workspace theaters directory.",
)

FLAGS = flags.FLAGS


def get_theaters_root() -> Path:
    """Return the theater-data root for the selected runtime environment."""
    if FLAGS["use_cloud_theater_storage"].value:
        return Path("/tmp/theaters")
    return Path(__file__).parent.parent / "theaters"


def ensure_theaters_root() -> Path:
    """Return and create the selected theater-data root."""
    theaters_root = get_theaters_root().resolve()
    theaters_root.mkdir(parents=True, exist_ok=True)
    return theaters_root
