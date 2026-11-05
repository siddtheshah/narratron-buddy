#!/usr/bin/env python3
"""Download existing games from Google Cloud Storage to local directory.

This script scans a GCS bucket prefix (default: 'games') for pre-defined
game packages and downloads them to a local target directory (default: 'games/'),
allowing users to inspect, modify, and re-upload them using upload_games_to_gcs.py.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Callable, Dict, List, Optional, Protocol, Tuple, TypedDict

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_BUCKET = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "games"
DEFAULT_TARGET_DIR = "games"
EXCLUDED_GAMES = {"example_game", "example-game"}


class DownloadBlobProtocol(Protocol):
    name: str
    size: Optional[int]
    md5_hash: Optional[str]
    crc32c: Optional[str]

    def download_to_filename(self, filename: str) -> None: ...
    def download_as_bytes(self) -> bytes: ...


class DownloadBucketProtocol(Protocol):
    name: str

    def list_blobs(self, prefix: str = "") -> List[DownloadBlobProtocol]: ...


class DownloadGameResult(TypedDict, total=False):
    id: str
    title: str
    files_count: int
    downloaded_files: List[str]
    skipped_count: int
    skipped_files: List[str]
    skipped_bytes: int
    pruned_count: int
    cleared_count: int
    total_bytes: int
    target_path: str
    excluded: bool


def _remove_readonly(func: Callable[[str], None], path: str, _exc: BaseException) -> None:
    """Helper to clear read-only file attributes on Windows before retrying deletion."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_dir_tree(dir_path: Path) -> None:
    """Recursively remove a directory, handling read-only files on Windows."""
    shutil.rmtree(dir_path, onexc=_remove_readonly)


def remove_empty_subdirs(dir_path: Path) -> None:
    """Remove empty subdirectories within dir_path (bottom-up)."""
    if not dir_path.exists():
        return
    for current_dir in sorted(dir_path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if current_dir.is_dir() and not any(current_dir.iterdir()):
            current_dir.rmdir()


def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def is_game_excluded(name_or_slug: str) -> bool:
    """Check if a game name or slug should be excluded from GCS sync."""
    target = (name_or_slug or "").strip().lower()
    return target in EXCLUDED_GAMES or slugify(target) in EXCLUDED_GAMES


def compute_file_md5(file_path: Path) -> str:
    """Compute base64-encoded MD5 hash of a local file matching GCS md5_hash."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return base64.b64encode(hasher.digest()).decode("utf-8")


def compute_file_crc32c(file_path: Path) -> Optional[str]:
    """Compute base64-encoded CRC32c checksum matching GCS crc32c."""
    import google_crc32c

    hasher = google_crc32c.Checksum()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return base64.b64encode(hasher.digest()).decode("utf-8")


def is_file_changed(file_path: Path, remote_blob: Optional[DownloadBlobProtocol]) -> bool:
    """Determine if a local file differs from a remote GCS blob."""
    if remote_blob is None:
        return True
    if not file_path.exists():
        return True

    local_size = file_path.stat().st_size
    remote_size = remote_blob.size

    if remote_size is not None and local_size != remote_size:
        return True

    remote_md5 = remote_blob.md5_hash
    if remote_md5 is not None and remote_md5 != "":
        local_md5 = compute_file_md5(file_path)
        return local_md5 != remote_md5

    remote_crc32c = remote_blob.crc32c
    if remote_crc32c is not None and remote_crc32c != "":
        local_crc32c = compute_file_crc32c(file_path)
        if local_crc32c is not None:
            return local_crc32c != remote_crc32c

    if remote_size is not None and local_size == remote_size:
        return False

    return True


def find_local_game_dirs(target_dir: Path, game_slug: str) -> List[Path]:
    """Find existing local game folder(s) matching the slug or name."""
    matching: List[Path] = []
    if target_dir.exists():
        for child in target_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                if child.name.lower() == game_slug.lower() or slugify(child.name) == game_slug:
                    matching.append(child)
    return matching


def group_blobs_by_game(
    blobs: List[DownloadBlobProtocol], gcs_prefix: str
) -> Dict[str, List[Tuple[DownloadBlobProtocol, str]]]:
    """Group GCS blobs by game slug.

    Args:
        blobs: List of GCS blob objects.
        gcs_prefix: The root prefix in GCS (e.g. 'games').

    Returns:
        Dict mapping game_slug -> List[(blob, relative_file_path_inside_game)].
    """
    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""

    grouped: Dict[str, List[Tuple[DownloadBlobProtocol, str]]] = {}
    for blob in blobs:
        blob_name = blob.name
        if not blob_name.startswith(prefix_str):
            continue

        rel_path = blob_name[len(prefix_str) :] if prefix_str else blob_name
        parts = [p for p in rel_path.split("/") if p]
        if len(parts) < 2:
            continue

        game_slug = parts[0]
        if is_game_excluded(game_slug):
            continue

        file_rel_path = "/".join(parts[1:])
        if game_slug not in grouped:
            grouped[game_slug] = []
        grouped[game_slug].append((blob, file_rel_path))

    return grouped


def download_game_from_gcs(
    game_slug: str,
    items: List[Tuple[DownloadBlobProtocol, str]],
    target_dir: Path,
    diff: bool = True,
    prune: bool = True,
    clear_existing: bool = False,
    overwrite: Optional[bool] = None,
    dry_run: bool = False,
) -> DownloadGameResult:
    """Download files for a single game into target_dir/game_slug."""
    game_target_path = target_dir / game_slug
    title = game_slug.replace("-", " ").title()

    if is_game_excluded(game_slug):
        print(f"⚠️ Skipping '{game_slug}': example game is excluded from GCS download.")
        return {
            "id": game_slug,
            "title": title,
            "files_count": 0,
            "downloaded_files": [],
            "skipped_count": 0,
            "skipped_files": [],
            "skipped_bytes": 0,
            "pruned_count": 0,
            "cleared_count": 0,
            "total_bytes": 0,
            "target_path": str(game_target_path),
            "excluded": True,
        }

    meta_item = next((b for b, rel in items if rel == "metadata.json"), None)
    if meta_item is not None:
        raw_meta = meta_item.download_as_bytes()
        if raw_meta:
            meta_json = json.loads(raw_meta.decode("utf-8"))
            parsed_title = meta_json.get("title")
            if parsed_title is not None and str(parsed_title).strip() != "":
                title = str(parsed_title).strip()

    print(f"\n📂 Downloading Game: '{title}' [{game_slug}] -> {game_target_path}")

    existing_dirs = find_local_game_dirs(target_dir, game_slug)
    if game_target_path not in existing_dirs and game_target_path.exists():
        existing_dirs.append(game_target_path)

    should_clear = clear_existing or (overwrite is True and not diff)
    cleared_count = 0
    if should_clear:
        for existing_dir in existing_dirs:
            if dry_run:
                print(f"  [DRY-RUN] Would clear existing local folder: {existing_dir}")
            else:
                print(f"  🧹 Clearing existing local folder: {existing_dir}")
                remove_dir_tree(existing_dir)
                cleared_count += 1

    total_bytes = 0
    skipped_bytes = 0
    downloaded_files: List[str] = []
    skipped_files: List[str] = []
    pruned_files: List[str] = []

    for blob, rel_file_path in items:
        dest_file_path = game_target_path / rel_file_path
        blob_size = blob.size if blob.size is not None else 0

        if diff and not should_clear and dest_file_path.exists():
            if not is_file_changed(dest_file_path, blob):
                skipped_files.append(rel_file_path)
                skipped_bytes += blob_size
                continue

        change_type = "modified" if dest_file_path.exists() else "new"
        total_bytes += blob_size

        if dry_run:
            print(f"  [DRY-RUN] Would download ({change_type}): {rel_file_path} -> {dest_file_path}")
        else:
            dest_file_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(dest_file_path))

            if not dest_file_path.exists():
                content = blob.download_as_bytes()
                dest_file_path.write_bytes(content)

            file_actual_size = dest_file_path.stat().st_size if dest_file_path.exists() else blob_size
            print(f"  ✓ Downloaded ({change_type}): {rel_file_path} ({file_actual_size} bytes)")

        downloaded_files.append(rel_file_path)

    if prune and not should_clear:
        upstream_rel_paths = {rel for _, rel in items}
        for existing_dir in existing_dirs:
            if existing_dir.exists():
                for local_file in existing_dir.rglob("*"):
                    if local_file.is_file() and not local_file.name.startswith("."):
                        rel_path = local_file.relative_to(existing_dir).as_posix()
                        if rel_path not in upstream_rel_paths:
                            if dry_run:
                                print(f"  [DRY-RUN] Would prune local file deleted upstream: {rel_path}")
                            else:
                                local_file.unlink()
                                print(f"  🗑️ Pruned local file deleted upstream: {rel_path}")
                            pruned_files.append(rel_path)
                remove_empty_subdirs(existing_dir)

    if skipped_files:
        skipped_mb = skipped_bytes / (1024 * 1024)
        print(f"  ⚡ Skipped {len(skipped_files)} unchanged file(s) ({skipped_mb:.2f} MB)")

    return {
        "id": game_slug,
        "title": title,
        "files_count": len(downloaded_files),
        "downloaded_files": downloaded_files,
        "skipped_count": len(skipped_files),
        "skipped_files": skipped_files,
        "skipped_bytes": skipped_bytes,
        "pruned_count": len(pruned_files),
        "cleared_count": cleared_count,
        "total_bytes": total_bytes,
        "target_path": str(game_target_path),
    }


def download_all_games(
    bucket: DownloadBucketProtocol,
    gcs_prefix: str = DEFAULT_PREFIX,
    target_dir: Path = Path(DEFAULT_TARGET_DIR),
    game_filter: Optional[str] = None,
    diff: bool = True,
    prune: bool = True,
    clear_existing: bool = False,
    overwrite: Optional[bool] = None,
    dry_run: bool = False,
) -> List[DownloadGameResult]:
    if game_filter and is_game_excluded(game_filter):
        print(f"⚠️ Game '{game_filter}' is an example template and is excluded from GCS download.")
        return []

    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""
    bucket_name = bucket.name

    print(f"🔍 Listing games in GCS: gs://{bucket_name}/{prefix_str}")

    blobs = list(bucket.list_blobs(prefix=prefix_str))
    if not blobs:
        print(f"⚠️ No files found under prefix gs://{bucket_name}/{prefix_str}")
        return []

    grouped = group_blobs_by_game(blobs, gcs_prefix)
    if not grouped:
        print(f"⚠️ No valid game subfolders found under prefix '{prefix_str}'")
        return []

    if game_filter:
        target = game_filter.lower().strip()
        if is_game_excluded(target):
            print(f"⚠️ Game '{game_filter}' is an example template and is excluded from GCS download.")
            return []
        slugified_target = slugify(target)
        filtered_grouped = {
            slug: items
            for slug, items in grouped.items()
            if (slug.lower() == target or slug.lower() == slugified_target)
            and not is_game_excluded(slug)
        }
        if not filtered_grouped:
            print(f"⚠️ Game filter '{game_filter}' did not match any available games: {list(grouped.keys())}")
            return []
        grouped = filtered_grouped

    print(f"🚀 Found {len(grouped)} game package(s) to download: {', '.join(grouped.keys())}")

    results: List[DownloadGameResult] = []
    for slug, items in grouped.items():
        res = download_game_from_gcs(
            game_slug=slug,
            items=items,
            target_dir=target_dir,
            diff=diff,
            prune=prune,
            clear_existing=clear_existing,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        results.append(res)

    return results


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download pre-defined games from GCS to local directory for editing."
    )
    parser.add_argument(
        "--target-dir",
        default=os.getenv("NARRATRON_GAMES_DIR", DEFAULT_TARGET_DIR),
        help=f"Local target folder to save downloaded games (default: {DEFAULT_TARGET_DIR})",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", DEFAULT_BUCKET),
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--game",
        default=None,
        help="Specific game slug or title to download (e.g. 'wacky-chess' or 'the-trader')",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("GCS_GAMES_PREFIX", DEFAULT_PREFIX),
        help=f"GCS source prefix/folder (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--diff",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Diff remote files against local and only download changed/new files (default: True)",
    )
    parser.add_argument(
        "--prune",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete local files that no longer exist upstream (default: True)",
    )
    parser.add_argument(
        "--clear-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Clear existing local game directory before downloading (default: False)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate download without writing files to local disk",
    )
    args = parser.parse_args()

    target_path = Path(args.target_dir)

    print(f"☁️ Source GCS Bucket: gs://{args.bucket}/{args.prefix.strip('/')}")
    print(f"📁 Local Destination Directory: {target_path.resolve()}")
    if args.clear_existing:
        print("🧹 Clear existing local folder: Enabled")
    elif args.diff:
        prune_str = " (pruning enabled)" if args.prune else ""
        print(f"⚡ Incremental diff mode: Enabled{prune_str}")

    bucket: Optional[DownloadBucketProtocol] = None
    if not args.dry_run:
        from google.cloud import storage

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")
        client = storage.Client(project=project)
        real_bucket = client.bucket(args.bucket)
        if not real_bucket.exists():
            print(f"❌ Bucket '{args.bucket}' does not exist or cannot be accessed.", file=sys.stderr)
            sys.exit(1)
        bucket = real_bucket
    else:
        class MockBucket:
            name: str = args.bucket

            def list_blobs(self, prefix: str = "") -> List[DownloadBlobProtocol]:
                return []

        bucket = MockBucket()

    if bucket is not None:
        results = download_all_games(
            bucket=bucket,
            gcs_prefix=args.prefix,
            target_dir=target_path,
            game_filter=args.game,
            diff=args.diff,
            prune=args.prune,
            clear_existing=args.clear_existing,
            dry_run=args.dry_run,
        )

        if results:
            print("\n========================================================")
            print("✨ Game Download Summary:")
            print("========================================================")
            for r in results:
                total_bytes_num = r.get("total_bytes", 0)
                mb = total_bytes_num / (1024 * 1024)
                skipped_count = r.get("skipped_count", 0)
                skipped_str = f", {skipped_count} unchanged" if skipped_count else ""
                pruned_count = r.get("pruned_count", 0)
                pruned_str = f", {pruned_count} pruned" if pruned_count else ""
                cleared_count = r.get("cleared_count", 0)
                cleared_str = f" [cleared {cleared_count} old file(s)]" if cleared_count else ""
                print(
                    f" • {r.get('title', '')} [{r.get('id', '')}] - {r.get('files_count', 0)} downloaded "
                    f"({mb:.2f} MB){skipped_str}{pruned_str}{cleared_str} -> {r.get('target_path', '')}"
                )
            print("========================================================\n")


if __name__ == "__main__":
    main()
