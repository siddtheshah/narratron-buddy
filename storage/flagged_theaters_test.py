from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zipfile

import pytest

from storage.flagged_theaters import snapshot_theater


def test_snapshot_reuses_identical_content_and_preserves_changed_evidence(tmp_path: Path) -> None:
    source = tmp_path / 'theater'
    source.mkdir()
    (source / 'theater.yaml').write_text('name: Original')
    (source / 'output').mkdir()
    (source / 'output' / 'image.png').write_bytes(b'image')
    destination = tmp_path / 'flagged_theaters'
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(snapshot_theater, source, destination, 'stage') for _ in range(4)]
        snapshots = [future.result() for future in futures]
    assert len(set(snapshots)) == 1
    assert len(list(destination.glob('*.zip'))) == 1
    first_hash, first_path = snapshots[0]
    (source / 'theater.yaml').write_text('name: Changed')
    second_hash, _ = snapshot_theater(source, destination, 'stage')
    assert first_hash != second_hash
    assert len(list(destination.glob('*.zip'))) == 2
    assert not list(destination.glob('*.tmp'))
    with zipfile.ZipFile(first_path) as archive:
        assert archive.read('theater.yaml') == b'name: Original'
        assert archive.read('output/image.png') == b'image'
    with pytest.raises(ValueError):
        snapshot_theater(source, destination, '../escape')


def test_missing_source_cannot_create_empty_evidence(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        snapshot_theater(tmp_path / 'missing', tmp_path / 'flags', 'stage')
