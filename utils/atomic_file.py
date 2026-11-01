"""Replace a local file only after its complete new contents have been flushed."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


def atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(target: Path, content: str) -> None:
    atomic_write_bytes(target, content.encode("utf-8"))
