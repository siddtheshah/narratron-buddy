"""Storage quota invalidation at runtime artifact write boundaries."""

from pathlib import Path
from unittest.mock import Mock, patch

from google.genai import types
import pytest

from services.disk_artifact_service import DiskArtifactService


@pytest.mark.asyncio
async def test_artifact_writes_and_deletes_invalidate_storage_but_reads_do_not(tmp_path: Path) -> None:
    changed = Mock()
    service = DiskArtifactService(tmp_path, storage_changed=changed)
    await service.save_artifact(app_name="app", user_id="user", filename="notes.txt",
                                artifact=types.Part(text="hello"))
    changed.assert_called_once_with()
    changed.reset_mock()
    loaded = await service.load_artifact(app_name="app", user_id="user", filename="notes.txt")
    assert loaded.text == "hello"
    changed.assert_not_called()
    await service.delete_artifact(app_name="app", user_id="user", filename="notes.txt")
    changed.assert_called_once_with()
    assert not (tmp_path / "notes.txt").exists()


@pytest.mark.asyncio
async def test_failed_artifact_write_still_invalidates_storage(tmp_path: Path) -> None:
    changed = Mock()
    service = DiskArtifactService(tmp_path, storage_changed=changed)
    with patch.object(Path, "write_text", side_effect=OSError("Disk full")):
        with pytest.raises(OSError, match="Disk full"):
            await service.save_artifact(app_name="app", user_id="user", filename="notes.txt",
                                        artifact=types.Part(text="hello"))
    changed.assert_called_once_with()
