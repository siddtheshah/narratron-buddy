"""Failed writes must leave the preceding durable document readable."""
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.atomic_file import atomic_write_bytes, atomic_write_text


def test_atomic_write_replaces_the_complete_document_and_cleans_its_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "session" / "theater.json"
    atomic_write_text(target, '{"text":"old"}')
    atomic_write_text(target, '{"text":"new — 完成"}')
    assert target.read_text(encoding="utf-8") == '{"text":"new — 完成"}'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failure_keeps_the_previous_document(tmp_path: Path, operation: str) -> None:
    target = tmp_path / "theater.json"
    target.write_bytes(b"old")
    with patch(f"utils.atomic_file.os.{operation}", side_effect=OSError("storage failure")):
        with pytest.raises(OSError, match="storage failure"):
            atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]
