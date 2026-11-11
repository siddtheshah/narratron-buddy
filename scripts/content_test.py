"""Shared content workflows with real local Git and an in-memory GCS boundary."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable

import pytest

from scripts import content


@dataclass
class ContentWorkspace:
    project: Path
    root: Path
    repository: Path
    remote: Path
    transport_calls: list[tuple[str, ...]]
    kind: str
    declaration: str


def write_file(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


@pytest.fixture(params=("games", "adventures"))
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> ContentWorkspace:
    # Keep Git subprocesses local and independent of the user's identity/hooks.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "application"
    source = project / kind
    declaration = "game.yaml" if kind == "games" else "theater.yaml"
    root = tmp_path / "backups"
    repository = root / kind
    remote = tmp_path / "remote.git"
    repository.mkdir(parents=True)
    remote.mkdir()
    write_file(source / f"local-game/{declaration}", "name: Updated\n")
    write_file(source / "README.md", "Local collection documentation\n")
    write_file(source / "local-game/token.png", "binary payload\u0000\r\n")
    write_file(repository / f"local-game/{declaration}", "name: Original\n")
    write_file(repository / "local-game/obsolete.txt", "Remove this file\n")
    write_file(repository / f"cloud-only/{declaration}", "Keep this package\n")
    write_file(repository / "local-game/metadata.json", '{"id":"local-game","title":"local-game","created_at":"2026-01-01"}')
    write_file(repository / "cloud-only/metadata.json", '{"id":"cloud-only","title":"Cloud Only"}')
    write_file(repository / ".gitignore", ".env\n__pycache__/\n")
    write_file(repository / ".gitattributes", "* -text\n")
    content.run_git(repository, "init", "--initial-branch=main")
    content.run_git(repository, "config", "user.name", "Content Tests")
    content.run_git(repository, "config", "user.email", "backup-tests@example.invalid")
    content.run_git(repository, "config", "core.autocrlf", "false")
    content.run_git(repository, "remote", "add", "origin", f"gcs://{content.BUCKET}/git/{kind}")
    content.run_git(repository, "add", "--all")
    content.run_git(repository, "commit", "--quiet", "-m", "Cloud baseline")
    content.run_git(repository, "tag", "baseline")
    content.run_git(remote, "init", "--bare", "--initial-branch=main")
    content.run_git(repository, "push", str(remote), "main")
    content.run_git(repository, "update-ref", "refs/remotes/origin/main", content.run_git(repository, "rev-parse", "HEAD"))
    real_git = content.run_git
    transport_calls: list[tuple[str, ...]] = []

    def local_transport(path: Path, *arguments: str) -> str:
        # Substitute only the network boundary; all Git behavior stays real.
        if arguments[0] in ("pull", "push", "fetch"):
            transport_calls.append(arguments)
            arguments = tuple(str(remote) if arg == "origin" else arg for arg in arguments)
        return real_git(path, *arguments)

    monkeypatch.setattr(content, "run_git", local_transport)
    return ContentWorkspace(project, root, repository, remote, transport_calls, kind, declaration)


def test_upload_versions_deletions_retains_cloud_packages_and_can_restore(workspace: ContentWorkspace) -> None:
    source_before = snapshot(workspace.project)
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Update rules", False)
    repository = workspace.repository
    assert (repository / f"local-game/{workspace.declaration}").read_text() == "name: Updated\n"
    assert not (repository / "local-game/obsolete.txt").exists()
    assert (repository / f"cloud-only/{workspace.declaration}").read_text() == "Keep this package\n"
    assert (repository / "README.md").read_text() == "Local collection documentation\n"
    assert (repository / "local-game/token.png").read_bytes() == (workspace.project / workspace.kind / "local-game/token.png").read_bytes()
    assert snapshot(workspace.project) == source_before
    head = content.run_git(repository, "rev-parse", "HEAD")
    assert content.run_git(workspace.remote, "rev-parse", "main") == head
    assert content.run_git(repository, "log", "-1", "--format=%s") == f"Update rules ({workspace.kind})"
    assert not content.run_git(repository, "status", "--porcelain")
    content.run_git(repository, "restore", "--source=baseline", "--", "local-game/")
    assert (repository / "local-game/obsolete.txt").read_text() == "Remove this file\n"
    assert (repository / f"local-game/{workspace.declaration}").read_text() == "name: Original\n"
    assert not (repository / "local-game/token.png").exists()


def test_unchanged_upload_retries_push_without_creating_commit(workspace: ContentWorkspace) -> None:
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Snapshot", False)
    head = content.run_git(workspace.repository, "rev-parse", "HEAD")
    workspace.transport_calls.clear()
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "No changes", False)
    assert content.run_git(workspace.repository, "rev-parse", "HEAD") == head
    assert workspace.transport_calls == [("pull", "--ff-only", "origin", "main"), ("push", "origin", "main")]


def test_failed_upload_push_keeps_commit_and_can_be_retried(workspace: ContentWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = content.run_git
    remote_before = transport(workspace.remote, "rev-parse", "main")

    def fail_push(repository: Path, *arguments: str) -> str:
        if arguments[0] == "push":
            raise subprocess.CalledProcessError(1, ["git", *arguments], stderr="Simulated push failure")
        return transport(repository, *arguments)

    monkeypatch.setattr(content, "run_git", fail_push)
    with pytest.raises(subprocess.CalledProcessError):
        content.upload_collection(workspace.project, workspace.root, workspace.kind, "Snapshot", False)
    committed = transport(workspace.repository, "rev-parse", "HEAD")
    assert committed != remote_before
    assert transport(workspace.remote, "rev-parse", "main") == remote_before
    assert not transport(workspace.repository, "status", "--porcelain")
    monkeypatch.setattr(content, "run_git", transport)
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Retry", False)
    assert transport(workspace.repository, "rev-parse", "HEAD") == committed
    assert transport(workspace.remote, "rev-parse", "main") == committed


def test_dry_run_does_not_copy_commit_or_contact_remote(workspace: ContentWorkspace) -> None:
    before = snapshot(workspace.repository)
    head = content.run_git(workspace.repository, "rev-parse", "HEAD")
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Dry run", True)
    assert snapshot(workspace.repository) == before
    assert content.run_git(workspace.repository, "rev-parse", "HEAD") == head
    assert not workspace.transport_calls


def test_excludes_private_files_and_preserves_existing_caches(workspace: ContentWorkspace) -> None:
    source = workspace.project / workspace.kind / "local-game"
    for relative in (".git", ".env", ".ENV", ".env.local", "cache.pyc", "save.tmp", "edit~", "edit.swp", "__pycache__/cache.pyc", ".venv/secret"):
        write_file(source / relative, "Do not back up\n")
    cache = workspace.repository / "local-game/__pycache__/old.pyc"
    write_file(cache, "Keep excluded cache\n")
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Snapshot", False)
    assert cache.read_text() == "Keep excluded cache\n"
    assert not (workspace.repository / "local-game/.git").exists()
    tracked = content.run_git(workspace.repository, "ls-files").splitlines()
    assert not any(".env" in path.casefold() or "__pycache__" in path or ".venv" in path for path in tracked)


def test_mirror_handles_file_and_directory_replacements(workspace: ContentWorkspace) -> None:
    source = workspace.project / workspace.kind / "local-game"
    destination = workspace.repository / "local-game"
    write_file(source / "was-file/child.txt", "New directory\n")
    write_file(source / "was-directory", "New file\n")
    write_file(destination / "was-file", "Old file\n")
    write_file(destination / "was-directory/child.txt", "Old directory\n")
    content.run_git(workspace.repository, "add", "--all")
    content.run_git(workspace.repository, "commit", "--quiet", "-m", "Before replacements")
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Replace paths", False)
    assert (destination / "was-file/child.txt").read_text() == "New directory\n"
    assert (destination / "was-directory").read_text() == "New file\n"


@pytest.mark.parametrize("state", ("dirty", "wrong-branch", "wrong-remote", "empty-source"))
def test_rejects_invalid_uploads_before_mutation(workspace: ContentWorkspace, state: str) -> None:
    if state == "dirty":
        write_file(workspace.repository / "pending.txt", "Pending edit\n")
    elif state == "wrong-branch":
        content.run_git(workspace.repository, "switch", "-c", "codex/other-branch")
    elif state == "wrong-remote":
        content.run_git(workspace.repository, "remote", "set-url", "origin", "gcs://another-bucket/games")
    else:
        source = workspace.project / workspace.kind / "local-game"
        for path in source.iterdir():
            path.unlink()
        source.rmdir()
    before = snapshot(workspace.repository)
    with pytest.raises(ValueError):
        content.upload_collection(workspace.project, workspace.root, workspace.kind, "Invalid", False)
    assert snapshot(workspace.repository) == before
    assert not workspace.transport_calls


def test_rejects_repository_inside_application(workspace: ContentWorkspace) -> None:
    with pytest.raises(ValueError, match="outside the application"):
        content.upload_collection(workspace.project, workspace.project, workspace.kind, "Invalid", False)
    assert not workspace.transport_calls


def test_rejects_links_before_mirroring(workspace: ContentWorkspace) -> None:
    link = workspace.project / workspace.kind / "local-game/external"
    try:
        link.symlink_to(workspace.remote, target_is_directory=True)
    except OSError as error:
        if os.name != "nt":
            pytest.skip(f"Symlink creation unavailable: {error}")
        # Windows junctions exercise reparse-point protection without requiring
        # the privilege needed to create symbolic links.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(workspace.remote)],
            capture_output=True, text=True,
        )
        if result.returncode:
            pytest.skip(f"Junction creation unavailable: {result.stderr}")
    before = snapshot(workspace.repository)
    with pytest.raises(ValueError, match="must not contain links"):
        content.upload_collection(workspace.project, workspace.root, workspace.kind, "Invalid", False)
    assert snapshot(workspace.repository) == before
    assert not workspace.transport_calls


def test_help_includes_setup_backup_and_recovery_instructions() -> None:
    help_text = content.create_parser().format_help()
    assert "uv tool install git-remote-gcs==0.1.2" in help_text
    assert "download --content adventures --revision COMMIT_SHA --overwrite-local" in help_text
    assert "--dry-run" in help_text
    assert "--repository-root" in help_text


@dataclass
class MemoryBucket:
    name: str
    objects: dict[str, "MemoryBlob"]
    events: list[tuple[str, str]]
    fail_upload: str | None = None

    def list_blobs(self, prefix: str) -> Iterable["MemoryBlob"]:
        self.events.append(("list", prefix))
        return [blob for name, blob in self.objects.items() if name.startswith(prefix)]

    def blob(self, name: str) -> "MemoryBlob":
        return self.objects.get(name, MemoryBlob(self, name))

    def seed(self, name: str, data: bytes) -> "MemoryBlob":
        blob = MemoryBlob(self, name, data, 1)
        self.objects[name] = blob
        return blob


@dataclass
class MemoryBlob:
    bucket: MemoryBucket
    name: str
    data: bytes = b""
    generation: int | None = None
    checksum_available: bool = True

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def md5_hash(self) -> str | None:
        if not self.checksum_available:
            return None
        import base64
        import hashlib

        return base64.b64encode(hashlib.md5(self.data).digest()).decode("ascii")

    def upload_from_filename(self, filename: str, content_type: str, if_generation_match: int) -> None:
        assert content_type
        assert if_generation_match == (self.generation or 0)
        if self.name == self.bucket.fail_upload:
            raise OSError("Simulated upload failure")
        self.data = Path(filename).read_bytes()
        self.generation = (self.generation or 0) + 1
        self.bucket.objects[self.name] = self
        self.bucket.events.append(("upload", self.name))

    def delete(self, if_generation_match: int) -> None:
        assert if_generation_match == self.generation
        del self.bucket.objects[self.name]
        self.bucket.events.append(("delete", self.name))


@pytest.fixture
def bucket() -> MemoryBucket:
    return MemoryBucket("test-bucket", {}, [])


def upload(workspace: ContentWorkspace) -> str:
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "Snapshot", False)
    return content.run_git(workspace.repository, "rev-parse", "HEAD")


def commit_and_push(workspace: ContentWorkspace) -> None:
    content.run_git(workspace.repository, "add", "--all")
    content.run_git(workspace.repository, "commit", "--quiet", "-m", "External content change")
    content.run_git(workspace.repository, "push", "origin", "main")


def test_publish_reads_uploaded_commit_and_ignores_dirty_files(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    commit = upload(workspace)
    path = workspace.repository / f"local-game/{workspace.declaration}"
    path.write_text("name: Uncommitted\n", encoding="utf-8")
    write_file(workspace.repository / "local-game/private.txt", "Untracked\n")
    source_before = snapshot(workspace.project)
    repository_before = snapshot(workspace.repository)
    result = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    key = f"{workspace.kind}/local-game/{workspace.declaration}"
    assert result.commit == commit
    assert bucket.objects[key].data == b"name: Updated\n"
    assert bucket.objects[f"{workspace.kind}/local-game/token.png"].data == b"binary payload\0\r\n"
    assert not any(name.endswith("private.txt") for name in bucket.objects)
    assert snapshot(workspace.project) == source_before
    assert snapshot(workspace.repository) == repository_before
    assert workspace.transport_calls[-1][0] == "fetch"


def test_publish_can_roll_back_to_an_uploaded_commit(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    previous = upload(workspace)
    write_file(workspace.project / workspace.kind / f"local-game/{workspace.declaration}", "name: New version\n")
    upload(workspace)
    result = content.publish_collection(workspace.project, workspace.root, workspace.kind, revision=previous, bucket=bucket)
    assert result.commit == previous
    assert bucket.objects[f"{workspace.kind}/local-game/{workspace.declaration}"].data == b"name: Updated\n"


def test_publish_refuses_unpushed_commits(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    write_file(workspace.repository / "local-game/private.txt", "Unpushed\n")
    content.run_git(workspace.repository, "add", "--all")
    content.run_git(workspace.repository, "commit", "--quiet", "-m", "Not uploaded")
    with pytest.raises(subprocess.CalledProcessError):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, revision="HEAD", bucket=bucket)
    assert not bucket.events


def test_publish_diff_and_collection_pruning(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    bucket.seed(f"{workspace.kind}/deleted-package/old.txt", b"Removed package")
    bucket.seed(f"{workspace.kind}/local-game/obsolete.txt", b"Removed file")
    other_kind = "adventures" if workspace.kind == "games" else "games"
    untouched = bucket.seed(f"{other_kind}/another/file.txt", b"Other collection")
    first = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert first.uploaded > 0
    assert first.pruned == 2
    assert bucket.objects[untouched.name].data == untouched.data
    bucket.events.clear()
    second = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert second.uploaded == 0
    assert second.skipped == first.uploaded
    assert second.pruned == 0
    assert bucket.events == [("list", f"{workspace.kind}/")]


def test_publish_package_filter_limits_pruning(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    bucket.seed(f"{workspace.kind}/cloud-only/retain.txt", b"Retain")
    bucket.seed(f"{workspace.kind}/local-game/remove.txt", b"Remove")
    result = content.publish_collection(workspace.project, workspace.root, workspace.kind, package_filter="local-game", bucket=bucket)
    assert result.pruned == 1
    assert f"{workspace.kind}/cloud-only/retain.txt" in bucket.objects
    assert not any(name.startswith(f"{workspace.kind}/cloud-only/") for event, name in bucket.events if event == "upload")


def test_publish_never_serves_examples(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    example = content.COLLECTIONS[workspace.kind].example
    source = workspace.project / workspace.kind / example
    write_file(source / workspace.declaration, "name: Example\n")
    upload(workspace)
    assert content.run_git(workspace.repository, "ls-files", "--", example)
    content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert not any(example in name for name in bucket.objects)


def test_publish_without_checksums_reuploads_even_when_sizes_match(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    name = f"{workspace.kind}/local-game/{workspace.declaration}"
    bucket.objects[name].checksum_available = False
    result = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert result.uploaded == 1


def test_failed_publish_does_not_prune_old_content(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    orphan = f"{workspace.kind}/old-game/keep.txt"
    bucket.seed(orphan, b"Previous content")
    bucket.fail_upload = f"{workspace.kind}/local-game/{workspace.declaration}"
    with pytest.raises(OSError, match="Simulated upload failure"):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert orphan in bucket.objects
    assert not any(event == "delete" for event, _ in bucket.events)


@pytest.mark.parametrize("invalid", ("duplicate-yaml", "invalid-yaml", "unsafe-id", "duplicate-id", "missing-declaration"))
def test_publish_validates_every_package_before_live_access(workspace: ContentWorkspace, bucket: MemoryBucket, invalid: str) -> None:
    upload(workspace)
    package = workspace.repository / "local-game"
    if invalid == "duplicate-yaml":
        write_file(package / workspace.declaration, "key: 1\nkey: 2\n")
    elif invalid == "invalid-yaml":
        write_file(package / workspace.declaration, "key: [unclosed\n")
    elif invalid == "unsafe-id":
        write_file(package / "metadata.json", '{"id":"../escape"}')
    elif invalid == "duplicate-id":
        write_file(package / "metadata.json", '{"id":"cloud-only"}')
    else:
        (package / workspace.declaration).unlink()
    commit_and_push(workspace)
    with pytest.raises(ValueError):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert not bucket.events


def test_upload_rejects_invalid_yaml_without_copying_or_pushing(workspace: ContentWorkspace) -> None:
    write_file(workspace.project / workspace.kind / f"local-game/{workspace.declaration}", "duplicate: 1\nduplicate: 2\n")
    before = snapshot(workspace.repository)
    with pytest.raises(ValueError, match="duplicate key"):
        upload(workspace)
    assert snapshot(workspace.repository) == before
    assert not workspace.transport_calls


def test_publish_dry_run_never_contacts_git_or_gcs(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    content.run_git(workspace.repository, "update-ref", "refs/remotes/origin/main", content.run_git(workspace.repository, "rev-parse", "HEAD"))
    workspace.transport_calls.clear()
    before = snapshot(workspace.repository)
    content.publish_collection(workspace.project, workspace.root, workspace.kind, dry_run=True, bucket=bucket)
    assert not bucket.events
    assert not workspace.transport_calls
    assert snapshot(workspace.repository) == before


@pytest.mark.parametrize("prefix", ("git", "git/games", "../escape", "/", "games/*"))
def test_publish_rejects_unsafe_destination_prefixes(prefix: str) -> None:
    with pytest.raises(ValueError):
        content.served_prefix(prefix)


def test_download_restores_committed_content_and_deletions(workspace: ContentWorkspace) -> None:
    upload(workspace)
    target = workspace.project / workspace.kind
    source_before = snapshot(target)
    # A different author updates the remote while our local files are unchanged.
    write_file(workspace.repository / f"local-game/{workspace.declaration}", "name: Remote edit\n")
    (workspace.repository / "local-game/token.png").unlink()
    commit_and_push(workspace)
    content.run_git(workspace.repository, "reset", "--hard", "HEAD~1")
    content.download_collection(workspace.project, workspace.root, workspace.kind)
    assert (target / f"local-game/{workspace.declaration}").read_text() == "name: Remote edit\n"
    assert not (target / "local-game/token.png").exists()
    assert (target / f"cloud-only/{workspace.declaration}").read_text() == "Keep this package\n"
    assert snapshot(target) != source_before
    assert not content.run_git(workspace.repository, "status", "--porcelain")


def test_download_requires_explicit_overwrite_for_local_edits(workspace: ContentWorkspace) -> None:
    upload(workspace)
    path = workspace.project / workspace.kind / f"local-game/{workspace.declaration}"
    path.write_text("name: Unsaved local edit\n", encoding="utf-8")
    before = snapshot(workspace.project)
    with pytest.raises(ValueError, match="Local edits"):
        content.download_collection(workspace.project, workspace.root, workspace.kind)
    assert snapshot(workspace.project) == before
    content.download_collection(workspace.project, workspace.root, workspace.kind, overwrite_local=True)
    assert path.read_text() == "name: Updated\n"


def test_download_can_restore_an_older_revision_without_rewinding_main(workspace: ContentWorkspace) -> None:
    previous = upload(workspace)
    path = workspace.project / workspace.kind / f"local-game/{workspace.declaration}"
    path.write_text("name: New version\n", encoding="utf-8")
    latest = upload(workspace)
    content.download_collection(workspace.project, workspace.root, workspace.kind, revision=previous, overwrite_local=True)
    assert path.read_text() == "name: Updated\n"
    assert content.run_git(workspace.repository, "rev-parse", "HEAD") == latest


def test_download_dry_run_does_not_fetch_or_change_files(workspace: ContentWorkspace) -> None:
    commit = upload(workspace)
    content.run_git(workspace.repository, "update-ref", "refs/remotes/origin/main", commit)
    workspace.transport_calls.clear()
    before = snapshot(workspace.project)
    content.download_collection(workspace.project, workspace.root, workspace.kind, dry_run=True)
    assert snapshot(workspace.project) == before
    assert not workspace.transport_calls


def test_new_checkout_dry_run_creates_nothing(workspace: ContentWorkspace) -> None:
    missing = workspace.root.parent / "new-backups"
    assert not content.ensure_checkout(workspace.project, missing, workspace.kind, content.BUCKET, True)
    assert not missing.exists()
    assert not workspace.transport_calls


def test_cli_upload_uses_shared_workflow(workspace: ContentWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "PROJECT_ROOT", workspace.project)
    monkeypatch.setattr(sys, "argv", ["content.py", "upload", "--content", workspace.kind, "--repository-root", str(workspace.root), "--dry-run"])
    assert content.main() == 0
    assert not workspace.transport_calls


def test_cli_rejects_ambiguous_package_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["content.py", "publish", "--package", "local-game"])
    with pytest.raises(SystemExit) as error:
        content.main()
    assert error.value.code == 2


def test_selected_publish_still_rejects_colliding_package_ids(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    write_file(workspace.repository / "local-game/metadata.json", '{"id":"cloud-only"}')
    commit_and_push(workspace)
    with pytest.raises(ValueError, match="Duplicate package ID"):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, package_filter="local-game", bucket=bucket)
    assert not bucket.events


def test_publish_flags_preserve_orphans_and_can_force_upload(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    first = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    orphan = f"{workspace.kind}/removed/file.txt"
    bucket.seed(orphan, b"Keep when pruning is disabled")
    bucket.events.clear()
    result = content.publish_collection(workspace.project, workspace.root, workspace.kind, diff=False, prune=False, bucket=bucket)
    assert result.uploaded == first.uploaded
    assert result.skipped == result.pruned == 0
    assert orphan in bucket.objects
    uploads = [name for operation, name in bucket.events if operation == "upload"]
    assert uploads[-1] == f"{workspace.kind}/{content.CATALOG_SENTINEL_FILENAME}"
    assert all(name.endswith("metadata.json") for name in uploads[-3:-1])


def test_publish_rejects_committed_symlinks_on_every_platform(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    upload(workspace)
    blob = subprocess.run(
        ["git", "-C", str(workspace.repository), "hash-object", "-w", "--stdin"],
        input="../../outside", text=True, capture_output=True, check=True,
    ).stdout.strip()
    content.run_git(workspace.repository, "update-index", "--add", "--cacheinfo", f"120000,{blob},local-game/link")
    content.run_git(workspace.repository, "commit", "--quiet", "-m", "Committed link")
    content.run_git(workspace.repository, "push", "origin", "main")
    with pytest.raises(ValueError, match="Committed links"):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert not bucket.events


def test_upload_and_publish_can_select_by_metadata_id(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    source = workspace.project / workspace.kind / "local-game"
    write_file(source / "metadata.json", '{"id":"custom-id","title":"Custom"}')
    content.upload_collection(workspace.project, workspace.root, workspace.kind, "One package", False, package_filter="custom-id")
    content.publish_collection(workspace.project, workspace.root, workspace.kind, package_filter="custom-id", bucket=bucket)
    assert f"{workspace.kind}/custom-id/{workspace.declaration}" in bucket.objects
    assert not any(name.startswith(f"{workspace.kind}/cloud-only/") for name in bucket.objects)


def test_download_rejects_application_root_as_target(workspace: ContentWorkspace) -> None:
    with pytest.raises(ValueError, match="collection folders"):
        content.download_collection(workspace.project, workspace.root, workspace.kind, target_dir=workspace.project)
    assert not workspace.transport_calls


def test_missing_checkout_is_cloned_using_the_shared_transport(workspace: ContentWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = content.run_git

    def clone_locally(repository: Path, *arguments: str) -> str:
        remote_url = f"gcs://{content.BUCKET}/git/{workspace.kind}"
        if arguments[0] == "clone":
            arguments = tuple(str(workspace.remote) if arg == remote_url else arg for arg in arguments)
        return transport(repository, *arguments)

    monkeypatch.setattr(content, "run_git", clone_locally)
    missing = workspace.root.parent / "new-checkouts"
    assert content.ensure_checkout(workspace.project, missing, workspace.kind, content.BUCKET, False)
    clone = missing / workspace.kind
    assert content.run_git(clone, "branch", "--show-current") == "main"
    assert (clone / f"local-game/{workspace.declaration}").read_text() == "name: Original\n"


def test_publish_writes_and_updates_catalog_sentinel(workspace: ContentWorkspace, bucket: MemoryBucket) -> None:
    commit = upload(workspace)
    content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    sentinel_name = f"{workspace.kind}/{content.CATALOG_SENTINEL_FILENAME}"
    assert sentinel_name in bucket.objects
    sentinel_blob = bucket.objects[sentinel_name]
    assert sentinel_blob.generation == 1
    payload = json.loads(sentinel_blob.data.decode("utf-8"))
    assert payload["kind"] == workspace.kind
    assert payload["commit"] == commit

    # If the sentinel was deleted externally, a publish without package diffs still recreates it.
    del bucket.objects[sentinel_name]
    bucket.events.clear()
    second = content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert second.uploaded == 0
    assert second.pruned == 0
    assert sentinel_name in bucket.objects
    assert bucket.events == [("list", f"{workspace.kind}/"), ("upload", sentinel_name)]

    # A failed publish must not write or bump the sentinel.
    bucket.events.clear()
    bucket.fail_upload = f"{workspace.kind}/local-game/{workspace.declaration}"
    write_file(workspace.project / workspace.kind / f"local-game/{workspace.declaration}", "name: Broken\n")
    upload(workspace)
    with pytest.raises(OSError, match="Simulated upload failure"):
        content.publish_collection(workspace.project, workspace.root, workspace.kind, bucket=bucket)
    assert bucket.objects[sentinel_name].generation == 1

