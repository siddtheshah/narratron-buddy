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
_EXCLUDED_FRONTEND_DIRS = frozenset({"static", "templates"})
_BUILD_CONFIG_FILES = (
    "tsconfig.json",
    "tsconfig.build.json",
    "scripts/build_canvas.mjs",
)
_TESTLAB_BUILD_CONFIG_FILES = (
    *_BUILD_CONFIG_FILES,
    "testlab/tsconfig.json",
)


def _is_declaration_file(path: Path) -> bool:
    return path.name.endswith(_DECLARATION_SUFFIXES)


def _is_build_input(path: Path, source_root: Path) -> bool:
    """Match the production source includes/excludes in tsconfig build files."""
    rel_parts = path.relative_to(source_root).parts
    if rel_parts and rel_parts[0] in _EXCLUDED_FRONTEND_DIRS:
        return False
    return (
        path.is_file()
        and path.suffix.lower() in _EMIT_SUFFIXES
        and not path.name.endswith("_test.ts")
    )


def _collect_emitted_sources(source_root: Path) -> list[Path]:
    """Return all TypeScript source files under source_root that emit JS."""
    if not source_root.exists():
        return []
    sources: list[Path] = []
    for path in sorted(source_root.rglob("*")):
        if not _is_build_input(path, source_root):
            continue
        if _is_declaration_file(path):
            continue
        if path.suffix.lower() in _EMIT_SUFFIXES:
            sources.append(path)
    return sources


def _latest_input_mtime_ns(
    project_root: Path,
    source_root: Path,
    config_files: tuple[str, ...],
) -> int:
    """Return the newest modification timestamp across sources and TS build configs."""
    latest = 0
    for path in source_root.rglob("*"):
        if _is_build_input(path, source_root):
            latest = max(latest, path.stat().st_mtime_ns)
    for rel_path in config_files:
        config_path = project_root / rel_path
        if config_path.exists():
            latest = max(latest, config_path.stat().st_mtime_ns)
    return latest


def _root_needs_build(
    project_root: Path,
    source_root: Path,
    generated_dir: Path,
    config_files: tuple[str, ...],
) -> bool:
    """Return True when source_root has TS sources and generated JS is missing or stale."""
    sources = _collect_emitted_sources(source_root)
    if not sources:
        return False
    latest_input = _latest_input_mtime_ns(project_root, source_root, config_files)
    for source_path in sources:
        output_path = generated_dir / source_path.relative_to(source_root).with_suffix(".js")
        if not output_path.exists():
            return True
        if latest_input > output_path.stat().st_mtime_ns:
            return True
    return False


def _needs_build(project_root: Path, *, include_testlab: bool = False) -> bool:
    """Return True when frontend (or testlab frontend) generated JS is missing or stale."""
    frontend_dir = project_root / "frontend"
    if _root_needs_build(
        project_root,
        frontend_dir,
        frontend_dir / "static" / "generated",
        _BUILD_CONFIG_FILES,
    ):
        return True
    if include_testlab:
        testlab_frontend_dir = project_root / "testlab" / "frontend"
        if _root_needs_build(
            project_root,
            testlab_frontend_dir,
            testlab_frontend_dir / "static" / "generated",
            _TESTLAB_BUILD_CONFIG_FILES,
        ):
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


def ensure_canvas_assets_built(
    project_root: Path = PROJECT_ROOT,
    *,
    include_testlab: bool = False,
) -> None:
    """Build generated JS when local frontend sources are missing output or stale."""
    if not _needs_build(project_root, include_testlab=include_testlab):
        return
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]
    lock_path = Path(tempfile.gettempdir()) / f"narratron-canvas-build-{digest}.lock"
    with _acquire_build_lock(lock_path):
        if not _needs_build(project_root, include_testlab=include_testlab):
            return
        npm_executable = shutil.which("npm")
        if npm_executable is None:
            raise RuntimeError(
                "TypeScript sources exist but generated browser assets are missing or stale, "
                "and 'npm' was not found on PATH. Run 'npm ci && npm run build'."
            )
        subprocess.run([npm_executable, "run", "build"], cwd=project_root, check=True)
