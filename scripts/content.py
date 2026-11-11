#!/usr/bin/env python3
"""Version, download, and publish games and adventures
===================================================

One workflow serves both content collections:

  uv run python scripts/content.py upload --message "Update content"
  uv run python scripts/content.py download --content games
  uv run python scripts/content.py publish --content adventures --dry-run

UPLOAD snapshots local packages, commits changes, and pushes to Git in GCS.
It never publishes to the app. DOWNLOAD pulls the Git repository and restores
committed packages to the local collection. PUBLISH exports an uploaded Git
commit and copies its files to the GCS prefixes served by Narratron. Dirty
checkout files and untracked files are never included in a publication.

Use --content games, --content adventures, or the default all. --package selects
one folder name or metadata ID and requires one collection. This command replaces
the former per-collection upload/download scripts and the backup script.

Repositories and served content
-------------------------------
Git history is stored separately in:

  gcs://narratron-buddy-app-storage/git/games
  gcs://narratron-buddy-app-storage/git/adventures

The app reads the published gs://narratron-buddy-app-storage/games/ and
gs://narratron-buddy-app-storage/adventures/ prefixes. --bucket selects a bucket;
--prefix overrides the served prefix for a single collection during publishing.
Publishing cannot target git/. Ordinary Git versions text and media without LFS.

Local repositories live beside the application, in ../narratron-content/games
and ../narratron-content/adventures. --repository-root overrides that directory.
Local editing folders default to the application's games/ and adventures/.
--source-dir (upload) and --target-dir (download) override a single collection.

Upload preserves packages missing locally, since a local collection may be
incomplete, and records deletions inside present packages. It normalizes package
metadata in the Git checkout before committing, without changing local sources.
Git metadata, caches, virtual environments, .env files, and temporary files are
excluded. Commit or restore pending edits in the Git checkout before upload or
download. Missing Git checkouts are cloned automatically.

Download refuses to overwrite local edits that differ from the previous Git
checkout. Use --overwrite-local to deliberately restore a revision over them.
Unrelated local packages are retained. --revision defaults to origin/main for
download and publish; a commit or tag reachable from uploaded main can be chosen
for recovery or rollback. Dirty files in a publishing checkout are ignored.

Publish validates every selected package before transferring anything, skips
unchanged files by checksum, and prunes deleted files and packages from the
served collection. --package limits pruning to that package; --no-prune retains
old served files. Example templates are versioned but never published. Publish
does not modify the source or Git working tree. Upload, download, and publish
run on demand, and each collection is handled independently.

Every operation supports --dry-run: inspect local state without remote access,
file changes, commits, or pushes. Publish and download use the locally known
origin/main in dry runs; run download or upload first if no checkout exists.

Setup on Windows, macOS, or Linux
--------------------------------
Install Git, uv, and the Google Cloud CLI, then install the existing remote helper:
https://github.com/Vicguin65/git-remote-gcs

  uv tool install git-remote-gcs==0.1.2
  gcloud auth application-default login

Ensure git-remote-gcs is on PATH. The account needs object read/list permissions
to download, and create/delete permissions to upload or publish. No Git server
is needed. To clone manually from beside the application checkout:

  mkdir narratron-content
  git clone --config core.autocrlf=false gcs://narratron-buddy-app-storage/git/games narratron-content/games
  git clone --config core.autocrlf=false gcs://narratron-buddy-app-storage/git/adventures narratron-content/adventures

Inspect and recover
-------------------
Use git log and git diff in either checkout to inspect history. The
cloud-baseline-2026-11-10 tag preserves published content from initial setup;
earlier overwritten history cannot be reconstructed. Both main branches have
the helper's protection against force pushes and branch deletion.

Restore a chosen uploaded version locally:

  uv run python scripts/content.py download --content adventures --revision COMMIT_SHA --overwrite-local

Publish an uploaded version to roll back the app:

  uv run python scripts/content.py publish --content adventures --revision COMMIT_SHA

Replace COMMIT_SHA with a commit or tag. To record a restoration as new history,
download it locally and then upload with a descriptive --message. Whole-package
removals can be committed with git rm -r in the content checkout, then git push;
a subsequent full publication prunes those packages from the app-served prefix.

The third-party helper transfers full-history bundles for changed branches and
tags. Prefer regular commits for checkpoints and tags for meaningful releases.
Initial setup verified fresh clones byte-for-byte, git fsck, and baseline restores
for both collections. Concurrent writers and interrupted helper uploads have not
been stress-tested.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import os
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from typing import Iterable, Protocol

from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPIError
from pydantic import JsonValue, TypeAdapter
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUCKET = "narratron-buddy-app-storage"
EXCLUDED_NAMES = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
EXCLUDED_FILES = ("*.pyc", "*.pyo", ".env", ".env.*", "*.tmp", "*~", "*.swp")


@dataclass(frozen=True)
class Collection:
    kind: str
    genre: str
    tags: tuple[str, ...]
    players: str
    manifests: tuple[str, ...]
    example: str


COLLECTIONS = {
    "games": Collection("games", "Board Game", ("Game", "Board Game"), "1-2",
                        ("game/game.yaml", "game/game.yml", "game/game.json", "game.yaml", "game.yml", "game.json"), "example-game"),
    "adventures": Collection("adventures", "Adventure", ("Adventure", "Story"), "1-4",
                             ("theater.yaml", "theater.yml"), "example-adventure"),
}
METADATA = TypeAdapter(dict[str, JsonValue])
STRING = TypeAdapter(str)


def slugify(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.lower())
    return "-".join(part for part in cleaned.split("-") if part)


def safe_name(value: str) -> str:
    if not value.strip() or value != value.strip() or value.endswith(".") or value.startswith(".") or any(c in value for c in '/\\:*?"<>|\0'):
        raise ValueError(f"Unsafe package name: {value!r}")
    return value


def content_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.iterdir()):
        if excluded(path) or path.name.startswith((".", "~")):
            continue
        if path.is_dir():
            files.extend(content_files(path))
        else:
            files.append(path)
    return files


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, JsonValue]:
        self.flatten_mapping(node)
        mapping: dict[str, JsonValue] = {}
        for key_node, value_node in node.value:
            key = str(self.construct_object(key_node, deep=deep))
            if key in mapping:
                raise yaml.constructor.ConstructorError(None, None, f"duplicate key '{key}'", key_node.start_mark)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def validate_yaml(package: Path) -> None:
    for path in content_files(package):
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                list(yaml.load_all(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader))
            except (yaml.YAMLError, UnicodeError) as error:
                raise ValueError(f"Invalid YAML in {package.name}/{path.relative_to(package)}: {error}") from error


def package_metadata(package: Path, kind: str, previous: Path | None = None) -> dict[str, JsonValue]:
    spec = COLLECTIONS[kind]
    path = package / "metadata.json"
    metadata = METADATA.validate_json(path.read_bytes()) if path.exists() else {}
    old_path = previous / "metadata.json" if previous is not None else None
    old = METADATA.validate_json(old_path.read_bytes()) if old_path is not None and old_path.exists() else {}
    metadata = {
        "id": slugify(package.name), "title": package.name,
        "description": f"Pre-defined {kind} package for {package.name}.",
        "author": "Narratron Creator", "genre": spec.genre, "tags": list(spec.tags),
        "difficulty": "Medium", "recommended_players": spec.players,
        **metadata,
    }
    metadata["id"] = safe_name(STRING.validate_python(metadata.get("id") or slugify(package.name), strict=True))
    metadata["title"] = STRING.validate_python(metadata.get("title") or package.name, strict=True)
    metadata["created_at"] = STRING.validate_python(
        metadata.get("created_at") or old.get("created_at") or datetime.now(timezone.utc).isoformat(), strict=True,
    )
    if not metadata.get("cover_image"):
        images = [path for path in content_files(package) if path.suffix.lower() in (".svg", ".png", ".jpg", ".jpeg", ".webp")]
        crest = package / "game/crest.svg"
        cover = crest if kind == "games" and crest.is_file() else next(iter(images), None)
        metadata["cover_image"] = cover.relative_to(package).as_posix() if cover is not None else ""
    return metadata


def package_directories(root: Path, kind: str, package_filter: str | None = None) -> list[Path]:
    spec = COLLECTIONS[kind]
    candidates = [root] if (root / "metadata.json").is_file() or any((root / name).is_file() for name in spec.manifests) else sorted(
        path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".") and not excluded(path)
        and ((path / "metadata.json").is_file() or any((path / name).is_file() for name in spec.manifests))
    )
    if package_filter:
        matches: list[Path] = []
        for package in candidates:
            path = package / "metadata.json"
            metadata = METADATA.validate_json(path.read_bytes()) if path.exists() else {}
            package_id = STRING.validate_python(metadata.get("id") or slugify(package.name), strict=True)
            if package_filter.casefold() in (package.name.casefold(), package_id.casefold(), slugify(package.name)):
                matches.append(package)
        candidates = matches
    if not candidates:
        raise ValueError(f"No matching {kind} packages found in {root}")
    return candidates


def validate_repository(project_root: Path, repository_root: Path, kind: str, bucket: str = BUCKET, clean: bool = True) -> Path:
    if kind not in COLLECTIONS:
        raise ValueError(f"Unknown content collection: {kind}")
    project_root = project_root.resolve(strict=True)
    reject_link(repository_root)
    repository_root = repository_root.resolve(strict=True)
    if repository_root.is_relative_to(project_root):
        raise ValueError("Content backup repositories must be outside the application repository.")
    repository = repository_root / kind
    if clean:
        reject_links(repository)
    else:
        reject_link(repository)
    repository = repository.resolve(strict=True)
    if repository.parent != repository_root or project_root.is_relative_to(repository):
        raise ValueError(f"Unsafe backup repository: {repository}")
    if Path(run_git(repository, "rev-parse", "--show-toplevel")).resolve() != repository:
        raise ValueError(f"Unexpected repository root: {repository}")
    if run_git(repository, "remote", "get-url", "origin") != f"gcs://{bucket}/git/{kind}":
        raise ValueError(f"Unexpected remote for {kind}")
    if clean:
        if run_git(repository, "branch", "--show-current") != "main":
            raise ValueError(f"Check out main in {repository} before transferring content.")
        if run_git(repository, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError(f"Commit or restore pending changes in {repository} before transferring content.")
    return repository


def run_git(repository: Path, *arguments: str) -> str:
    """Run Git directly, without a platform-specific shell."""
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def reject_link(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or (
        os.name == "nt"
        and metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError(f"Source and backup trees must not contain links: {path}")


def reject_links(root: Path) -> None:
    """Reject symlinks and Windows junctions before traversing a tree."""
    pending = [root]
    while pending:
        path = pending.pop()
        reject_link(path)
        if path.is_dir():
            pending.extend(path.iterdir())


def excluded(path: Path) -> bool:
    """Use the same exclusion rules on every operating system."""
    return path.name.casefold() in EXCLUDED_NAMES or (
        not path.is_dir() and any(fnmatchcase(path.name.casefold(), pattern) for pattern in EXCLUDED_FILES)
    )


def remove_content_path(path: Path, repository: Path) -> None:
    """Limit every recursive removal to descendants of the backup repository."""
    resolved = path.resolve(strict=True)
    if resolved == repository or not resolved.is_relative_to(repository):
        raise ValueError(f"Unsafe mirror target: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def mirror_directory(source: Path, destination: Path, repository: Path) -> None:
    """Mirror a prevalidated package while preserving excluded backup files."""
    if destination.exists() and not destination.is_dir():
        remove_content_path(destination, repository)
    destination.mkdir(parents=True, exist_ok=True)
    for existing in destination.iterdir():
        if excluded(existing):
            continue
        incoming = source / existing.name
        if incoming.exists() and excluded(incoming):
            continue
        if not incoming.exists() or existing.is_dir() != incoming.is_dir():
            remove_content_path(existing, repository)
    for incoming in source.iterdir():
        if excluded(incoming):
            continue
        target = destination / incoming.name
        if incoming.is_dir():
            mirror_directory(incoming, target, repository)
        else:
            shutil.copy2(incoming, target)


def upload_collection(
    project_root: Path, repository_root: Path, kind: str, message: str, dry_run: bool,
    bucket: str = BUCKET, source_dir: Path | None = None, package_filter: str | None = None,
) -> None:
    """Snapshot, commit, and push content without touching the served catalog."""
    repository = validate_repository(project_root, repository_root, kind, bucket)
    source = source_dir if source_dir is not None else project_root / kind
    reject_links(source)
    source = source.resolve(strict=True)
    if source.is_relative_to(repository) or repository.is_relative_to(source):
        raise ValueError("Upload sources must be separate from their Git checkout.")
    packages = package_directories(source, kind, package_filter)
    prepared: list[tuple[Path, dict[str, JsonValue]]] = []
    identities: set[str] = set()
    for package in packages:
        validate_yaml(package)
        metadata = package_metadata(package, kind, repository / package.name)
        identity = STRING.validate_python(metadata["id"], strict=True)
        if identity in identities:
            raise ValueError(f"Duplicate package ID: {identity}")
        identities.add(identity)
        prepared.append((package, metadata))
    if not dry_run:
        run_git(repository, "pull", "--ff-only", "origin", "main")
        reject_links(repository)
    for package, metadata in prepared:
        destination = repository / safe_name(package.name)
        if destination.resolve().parent != repository:
            raise ValueError(f"Unsafe mirror target: {destination}")
        if not dry_run:
            mirror_directory(package, destination, repository)
            (destination / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{kind}: {package.name}")
    if dry_run:
        print(f"Dry run: would commit and push {kind} to gcs://{bucket}/git/{kind}")
        return
    readme = source / "README.md"
    if source not in packages and package_filter is None and readme.is_file():
        shutil.copy2(readme, repository / "README.md")
    run_git(repository, "add", "--all")
    if run_git(repository, "diff", "--cached", "--name-only"):
        run_git(repository, "commit", "--quiet", "-m", f"{message} ({kind})")
    run_git(repository, "push", "origin", "main")
    print(run_git(repository, "log", "-1", "--format=%h %s"))


def ensure_checkout(project: Path, root: Path, kind: str, bucket: str, dry_run: bool) -> bool:
    resolved = root.resolve()
    if resolved.is_relative_to(project.resolve()):
        raise ValueError("Content backup repositories must be outside the application repository.")
    if root.exists():
        reject_link(root)
    repository = root / kind
    if repository.exists():
        return True
    remote = f"gcs://{bucket}/git/{kind}"
    if dry_run:
        print(f"Dry run: would clone {remote} to {repository}")
        return False
    root.mkdir(parents=True, exist_ok=True)
    run_git(root, "clone", "--config", "core.autocrlf=false", "--branch", "main", remote, str(repository.resolve()))
    return True


def fetch_revision(repository: Path, revision: str, dry_run: bool) -> str:
    if not dry_run:
        run_git(repository, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    commit = run_git(repository, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    run_git(repository, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main")
    return commit


def export_commit(repository: Path, commit: str, destination: Path) -> None:
    """Export only Git objects, rejecting archive links and path traversal."""
    archive_path = destination / "snapshot.tar"
    with archive_path.open("wb") as output:
        subprocess.run(["git", "-C", str(repository), "archive", "--format=tar", commit], stdout=output, stderr=subprocess.PIPE, check=True)
    tree = destination / "tree"
    tree.mkdir()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or any(part in (".", "..", ".git") or "\\" in part or ":" in part for part in relative.parts):
                raise ValueError(f"Unsafe committed path: {member.name}")
            target = tree.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(tree.resolve()):
                raise ValueError(f"Unsafe committed path: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Unreadable committed file: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError(f"Committed links are not supported: {member.name}")


def same_package(left: Path, right: Path, kind: str) -> bool:
    if not right.is_dir():
        return False
    left_files = {path.relative_to(left): path for path in content_files(left)}
    right_files = {path.relative_to(right): path for path in content_files(right)}
    if left_files.keys() == right_files.keys() and all(
        path.read_bytes() == right_files[relative].read_bytes() for relative, path in left_files.items()
    ):
        return True
    # Upload commits normalized metadata while preserving local authoring files.
    left_files.pop(Path("metadata.json"), None)
    right_files.pop(Path("metadata.json"), None)
    return left_files.keys() == right_files.keys() and all(
        path.read_bytes() == right_files[relative].read_bytes() for relative, path in left_files.items()
    ) and package_metadata(left, kind, right) == package_metadata(right, kind, right)


def download_collection(
    project: Path, root: Path, kind: str, bucket: str = BUCKET,
    target_dir: Path | None = None, package_filter: str | None = None,
    revision: str = "origin/main", overwrite_local: bool = False, dry_run: bool = False,
) -> None:
    repository = validate_repository(project, root, kind, bucket)
    target = target_dir if target_dir is not None else project / kind
    if target.exists():
        reject_links(target)
    target = target.resolve()
    if project.resolve().is_relative_to(target):
        raise ValueError("Download targets must be collection folders, not the application root or its parents.")
    if target.is_relative_to(root.resolve()) or root.resolve().is_relative_to(target):
        raise ValueError("Download targets must be separate from the Git repositories.")
    with TemporaryDirectory(prefix="narratron-download-") as temporary:
        staging = Path(temporary)
        before = staging / "before"
        before.mkdir()
        export_commit(repository, run_git(repository, "rev-parse", "HEAD"), before)
        commit = fetch_revision(repository, revision, dry_run)
        after = staging / "after"
        after.mkdir()
        export_commit(repository, commit, after)
        packages = package_directories(after / "tree", kind, package_filter)
        for package in packages:
            existing = target / package.name
            if existing.exists() and not overwrite_local and not same_package(existing, before / "tree" / package.name, kind):
                raise ValueError(f"Local edits in {existing}; upload first or use --overwrite-local to restore deliberately.")
        readme = after / "tree/README.md"
        local_readme = target / "README.md"
        old_readme = before / "tree/README.md"
        if package_filter is None and readme.is_file() and local_readme.exists() and not overwrite_local:
            if not old_readme.is_file() or local_readme.read_bytes() != old_readme.read_bytes():
                raise ValueError(f"Local edits in {local_readme}; upload first or use --overwrite-local.")
        if dry_run:
            print(f"Dry run: would download {kind} at {commit} to {target}")
            return
        run_git(repository, "merge", "--ff-only", "refs/remotes/origin/main")
        target.mkdir(parents=True, exist_ok=True)
        for package in packages:
            destination = target / safe_name(package.name)
            if destination.resolve().parent != target:
                raise ValueError(f"Unsafe download target: {destination}")
            mirror_directory(package, destination, target)
        if package_filter is None and readme.is_file():
            shutil.copy2(readme, local_readme)
        print(f"Downloaded {kind} at {commit} to {target}")


class StorageBlob(Protocol):
    name: str
    size: int | None
    md5_hash: str | None
    generation: int | None

    def upload_from_filename(self, filename: str, content_type: str, if_generation_match: int) -> None: ...
    def delete(self, if_generation_match: int) -> None: ...


class StorageBucket(Protocol):
    name: str

    def list_blobs(self, prefix: str) -> Iterable[StorageBlob]: ...
    def blob(self, name: str) -> StorageBlob: ...


@dataclass(frozen=True)
class Publication:
    commit: str
    uploaded: int
    skipped: int
    pruned: int


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def served_prefix(prefix: str) -> str:
    parts = prefix.strip("/").split("/")
    if not parts or parts[0].casefold() == "git":
        raise ValueError("Publish must target an app-served prefix, never git/.")
    for part in parts:
        safe_name(part)
    return "/".join(parts) + "/"


def publication_files(tree: Path, kind: str, prefix: str, package_filter: str | None) -> tuple[dict[str, Path], set[str]]:
    packages = package_directories(tree, kind)
    selected = set(package_directories(tree, kind, package_filter))
    files: dict[str, Path] = {}
    slugs: set[str] = set()
    spec = COLLECTIONS[kind]
    identities: set[str] = set()
    for package in packages:
        path = package / "metadata.json"
        metadata = METADATA.validate_json(path.read_bytes()) if path.exists() else {}
        identity = safe_name(STRING.validate_python(metadata.get("id") or slugify(package.name), strict=True))
        if spec.example in (slugify(package.name), slugify(identity)):
            continue
        if identity in identities:
            raise ValueError(f"Duplicate package ID: {identity}")
        identities.add(identity)
        if package not in selected:
            continue
        if not path.is_file():
            raise ValueError(f"{package.name} has no committed metadata.json; upload it first.")
        if not any((package / name).is_file() for name in spec.manifests):
            raise ValueError(f"{package.name} has no {kind} declaration.")
        validate_yaml(package)
        slugs.add(identity)
        for local in content_files(package):
            files[f"{prefix}{identity}/{local.relative_to(package).as_posix()}"] = local
    if not files:
        raise ValueError(f"No publishable {kind} packages selected; refusing to clear served content.")
    return files, slugs


def publish_tree(
    tree: Path, kind: str, prefix: str, commit: str, bucket: StorageBucket | None,
    package_filter: str | None = None, diff: bool = True, prune: bool = True,
    dry_run: bool = False,
) -> Publication:
    """Publish a validated committed tree with checksum diffing and scoped pruning."""
    prefix = served_prefix(prefix)
    files, slugs = publication_files(tree, kind, prefix, package_filter)
    if dry_run:
        print(f"Dry run: would publish {len(files)} {kind} files at {commit} under {prefix}")
        return Publication(commit, 0, 0, 0)
    if bucket is None:
        raise ValueError("Publishing requires a storage bucket.")
    remote: dict[str, StorageBlob] = {}
    for blob in bucket.list_blobs(prefix=prefix):
        if not blob.name.startswith(prefix):
            raise ValueError(f"Unexpected object outside publication prefix: {blob.name}")
        relative = blob.name.removeprefix(prefix)
        parts = relative.split("/")
        if len(parts) < 2 or not parts[-1]:
            continue
        identity = safe_name(parts[0])
        if package_filter is not None and identity not in slugs:
            continue
        if slugify(identity) == COLLECTIONS[kind].example:
            continue
        if blob.generation is None:
            raise ValueError(f"Missing generation for {blob.name}; cannot safely publish.")
        remote[blob.name] = blob
    uploaded = skipped = 0
    # Catalog metadata goes last; deletion starts only after every upload succeeds.
    for name, path in sorted(files.items(), key=lambda item: (item[1].name == "metadata.json", item[0])):
        existing = remote.get(name)
        if diff and existing is not None and existing.size == path.stat().st_size and existing.md5_hash == file_md5(path):
            skipped += 1
            continue
        generation = existing.generation if existing is not None else 0
        if generation is None:
            raise ValueError(f"Missing generation for {name}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in (".yaml", ".yml"):
            content_type = "text/yaml"
        bucket.blob(name).upload_from_filename(str(path), content_type=content_type, if_generation_match=generation)
        uploaded += 1
    pruned = 0
    if prune:
        for name, blob in remote.items():
            if name not in files:
                if blob.generation is None:
                    raise ValueError(f"Missing generation for {name}")
                blob.delete(if_generation_match=blob.generation)
                pruned += 1
    print(f"Published {kind} at {commit}: {uploaded} uploaded, {skipped} unchanged, {pruned} pruned")
    return Publication(commit, uploaded, skipped, pruned)


def publish_collection(
    project: Path, root: Path, kind: str, bucket_name: str = BUCKET,
    prefix: str | None = None, package_filter: str | None = None,
    revision: str = "origin/main", diff: bool = True, prune: bool = True,
    dry_run: bool = False, bucket: StorageBucket | None = None,
) -> Publication:
    prefix = served_prefix(prefix if prefix is not None else kind)
    repository = validate_repository(project, root, kind, bucket_name, clean=False)
    commit = fetch_revision(repository, revision, dry_run)
    with TemporaryDirectory(prefix="narratron-publish-") as temporary:
        staging = Path(temporary)
        export_commit(repository, commit, staging)
        # Validate before constructing a live client or listing app-served objects.
        publication_files(staging / "tree", kind, prefix, package_filter)
        if bucket is None and not dry_run:
            from google.cloud import storage

            bucket = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")).bucket(bucket_name)
        return publish_tree(staging / "tree", kind, prefix, commit, bucket, package_filter, diff, prune, dry_run)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload versioned content, download it, or publish a committed revision to the app.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("upload", "download", "publish"):
        command = commands.add_parser(operation, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        command.add_argument("--content", choices=("all", "adventures", "games"), default="all")
        command.add_argument("--package", help="One package folder or metadata ID")
        command.add_argument("--bucket", default=os.getenv("GCS_BUCKET", BUCKET))
        command.add_argument("--repository-root", type=Path, default=PROJECT_ROOT.parent / "narratron-content")
        command.add_argument("--dry-run", action="store_true", help="Inspect local state without remote access or changes")
        if operation == "upload":
            command.add_argument("--message", default="Update content")
            command.add_argument("--source-dir", type=Path)
        else:
            command.add_argument("--revision", default="origin/main", help="Uploaded commit or tag (default: origin/main)")
        if operation == "download":
            command.add_argument("--target-dir", type=Path)
            command.add_argument("--overwrite-local", action="store_true")
        if operation == "publish":
            command.add_argument("--prefix", help="Override the app-served prefix for one collection")
            command.add_argument("--diff", action=argparse.BooleanOptionalAction, default=True)
            command.add_argument("--prune", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main() -> int:
    load_dotenv()
    parser = create_parser()
    args = parser.parse_args()
    if args.content == "all" and (
        args.package or (args.operation == "upload" and args.source_dir)
        or (args.operation == "download" and args.target_dir)
        or (args.operation == "publish" and args.prefix)
    ):
        parser.error("--package, --source-dir, --target-dir, and --prefix require one --content collection")
    kinds = ("adventures", "games") if args.content == "all" else (args.content,)
    try:
        for kind in kinds:
            if not ensure_checkout(PROJECT_ROOT, args.repository_root, kind, args.bucket, args.dry_run):
                continue
            if args.operation == "upload":
                upload_collection(PROJECT_ROOT, args.repository_root, kind, args.message, args.dry_run,
                                  args.bucket, args.source_dir, args.package)
            elif args.operation == "download":
                download_collection(PROJECT_ROOT, args.repository_root, kind, args.bucket, args.target_dir,
                                    args.package, args.revision, args.overwrite_local, args.dry_run)
            else:
                prefix = args.prefix if args.prefix is not None else os.getenv(f"GCS_{kind.upper()}_PREFIX", kind)
                publish_collection(PROJECT_ROOT, args.repository_root, kind, args.bucket, prefix,
                                   args.package, args.revision, args.diff, args.prune, args.dry_run)
    except subprocess.CalledProcessError as error:
        print(f"Git failed: {error.stderr or error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, GoogleAPIError) as error:
        print(f"Content transfer failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


