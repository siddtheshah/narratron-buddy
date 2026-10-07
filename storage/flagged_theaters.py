"""Immutable, content-addressed evidence on the persistent storage volume."""

import hashlib
from pathlib import Path
import tempfile
import zipfile


def snapshot_theater(source: Path, destination: Path, theater_id: str) -> tuple[str, str]:
    """Archive exactly the bytes hashed, reusing unchanged evidence across reports."""
    if not theater_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in theater_id):
        raise ValueError("Invalid theater ID")
    if not source.is_dir():
        raise FileNotFoundError("Theater files are unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    # Build privately and publish atomically. Concurrent workers may build the
    # same archive, but only one content-addressed evidence file is retained.
    with tempfile.NamedTemporaryFile(dir=destination, suffix='.tmp', delete=False) as staging:
        staging_path = Path(staging.name)
    try:
        with zipfile.ZipFile(staging_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(source.rglob('*')):
                if file.is_symlink() or not file.is_file():
                    continue
                if not file.resolve().is_relative_to(source.resolve()):
                    continue
                name = file.relative_to(source).as_posix()
                content = file.read_bytes()
                digest.update(len(name.encode()).to_bytes(8, 'big'))
                digest.update(name.encode())
                digest.update(len(content).to_bytes(8, 'big'))
                digest.update(content)
                archive.writestr(name, content)
        content_hash = digest.hexdigest()
        target = destination / f'{theater_id}_{content_hash}.zip'
        if not target.exists():
            try:
                staging_path.replace(target)
            except OSError:
                # Windows can reject a simultaneous replace of the same target.
                # A completed archive from another worker is equivalent evidence.
                if not target.is_file():
                    raise
        return content_hash, str(target)
    finally:
        staging_path.unlink(missing_ok=True)
