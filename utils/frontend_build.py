"""Ensure compiled frontend TypeScript assets exist and are up to date locally."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EMIT_SUFFIXES = {".ts"}
_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
_BUILD_CONFIG_FILES = (
    "tsconfig.json",
    "tsconfig.build.json",
    "scripts/build_canvas.mjs",
)


def _is_declaration_file(path: Path) -> bool:
    return path.name.endswith(_DECLARATION_SUFFIXES)


def _is_build_input(path: Path) -> bool:
    """Match the production source includes/excludes in tsconfig.build.json."""
    return (
        path.is_file()
        and path.suffix.lower() in _EMIT_SUFFIXES
        and not path.name.endswith("_test.ts")
    )


def _collect_emitted_sources(frontend_dir: Path) -> list[Path]:
    """Return all TypeScript source files under frontend/ that emit JS."""
    if not frontend_dir.exists():
        return []
    sources: list[Path] = []
    for path in sorted(frontend_dir.rglob("*")):
        if not _is_build_input(path):
            continue
        if _is_declaration_file(path):
            continue
        if path.suffix.lower() in _EMIT_SUFFIXES:
            sources.append(path)
    return sources


def _latest_input_mtime_ns(project_root: Path, frontend_dir: Path) -> int:
    """Return the newest modification timestamp across frontend sources and TS build configs."""
    latest = 0
    for path in frontend_dir.rglob("*"):
        if _is_build_input(path):
            latest = max(latest, path.stat().st_mtime_ns)
    for rel_path in _BUILD_CONFIG_FILES:
        config_path = project_root / rel_path
        if config_path.exists():
            latest = max(latest, config_path.stat().st_mtime_ns)
    return latest


def _needs_build(project_root: Path) -> bool:
    """Return True when frontend/ sources exist and generated JS is missing or stale."""
    frontend_dir = project_root / "frontend"
    sources = _collect_emitted_sources(frontend_dir)
    if not sources:
        return False
    generated_dir = project_root / "static" / "generated"
    latest_input = _latest_input_mtime_ns(project_root, frontend_dir)
    for source_path in sources:
        output_path = generated_dir / source_path.relative_to(frontend_dir).with_suffix(".js")
        if not output_path.exists():
            return True
        if latest_input > output_path.stat().st_mtime_ns:
            return True
    return False


@contextmanager
def _acquire_build_lock(lock_path: Path) -> Iterator[None]:
    """Serialize local TypeScript builds across parallel processes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        if lock_path.stat().st_size == 0:
            lock_file.write(b"0")
            lock_file.flush()
        if sys.platform == "win32":
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_canvas_assets_built(project_root: Path = PROJECT_ROOT) -> None:
    """Build static/generated/ when local frontend/ sources are missing output or stale."""
    if not _needs_build(project_root):
        return
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    lock_path = Path(tempfile.gettempdir()) / f"narratron-canvas-build-{digest}.lock"
    with _acquire_build_lock(lock_path):
        if not _needs_build(project_root):
            return
        npm_executable = shutil.which("npm")
        if npm_executable is None:
            raise RuntimeError(
                "frontend TypeScript sources exist in frontend/ but static/generated/ "
                "is missing or stale, and 'npm' was not found on PATH. Run 'npm ci && npm run build'."
            )
        subprocess.run([npm_executable, "run", "build"], cwd=project_root, check=True)
