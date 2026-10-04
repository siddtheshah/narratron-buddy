#!/usr/bin/env python3
"""Download existing adventures from Google Cloud Storage to local directory.

This script scans a GCS bucket prefix (default: 'adventures') for premade
adventure packages and downloads them to a local target directory (default: 'adventures/'),
allowing users to inspect, modify, and re-upload them using upload_adventures_to_gcs.py.
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
from typing import Callable, Dict, List, Optional, Protocol, Tuple, Union

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_BUCKET = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "adventures"
DEFAULT_TARGET_DIR = "adventures"
EXCLUDED_ADVENTURES = {"example_adventure", "example-adventure"}


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


def is_adventure_excluded(name_or_slug: str) -> bool:
    """Check if an adventure name or slug should be excluded from GCS sync."""
    target = (name_or_slug or "").strip().lower()
    return target in EXCLUDED_ADVENTURES or slugify(target) in EXCLUDED_ADVENTURES


def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def compute_file_md5(file_path: Path) -> str:
    """Compute base64-encoded MD5 hash of a local file matching GCS md5_hash."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return base64.b64encode(hasher.digest()).decode("utf-8")


def compute_file_crc32c(file_path: Path) -> Optional[str]:
    """Compute base64-encoded CRC32c checksum matching GCS crc32c."""
    try:
        import google_crc32c

        hasher = google_crc32c.Checksum()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return base64.b64encode(hasher.digest()).decode("utf-8")
    except Exception:
        return None


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


def find_local_adventure_dirs(target_dir: Path, adventure_slug: str) -> List[Path]:
    """Find existing local adventure folder(s) matching the slug or name."""
    matching: List[Path] = []
    if target_dir.exists():
        for child in target_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                if child.name.lower() == adventure_slug.lower() or slugify(child.name) == adventure_slug:
                    matching.append(child)
    return matching


def group_blobs_by_adventure(
    blobs: List[DownloadBlobProtocol], gcs_prefix: str
) -> Dict[str, List[Tuple[DownloadBlobProtocol, str]]]:
    """Group GCS blobs by adventure slug.

    Args:
        blobs: List of GCS blob objects.
        gcs_prefix: The root prefix in GCS (e.g. 'adventures').

    Returns:
        Dict mapping adventure_slug -> List[(blob, relative_file_path_inside_adventure)].
    """
    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""

    grouped: Dict[str, List[Tuple[DownloadBlobProtocol, str]]] = {}
    for blob in blobs:
        blob_name = blob.name
        if not blob_name.startswith(prefix_str):
            continue

        rel_path = blob_name[len(prefix_str):] if prefix_str else blob_name
        parts = [p for p in rel_path.split("/") if p]
        if len(parts) < 2:
            # Skip top-level directory markers or single files directly under prefix
            continue

        adv_slug = parts[0]
        if is_adventure_excluded(adv_slug):
            continue

        file_rel_path = "/".join(parts[1:])
        if adv_slug not in grouped:
            grouped[adv_slug] = []
        grouped[adv_slug].append((blob, file_rel_path))

    return grouped


def download_adventure_from_gcs(
    adventure_slug: str,
    items: List[Tuple[DownloadBlobProtocol, str]],
    target_dir: Path,
    diff: bool = True,
    prune: bool = True,
    clear_existing: bool = False,
    overwrite: Optional[bool] = None,
    dry_run: bool = False,
) -> Dict[str, Union[str, int, List[str], bool]]:
    """Download files for a single adventure into target_dir/adventure_slug.

    Args:
        adventure_slug: The identifier/folder name of the adventure.
        items: List of (blob, relative_file_path_inside_adventure).
        target_dir: Base directory where adventure folder will be saved.
        diff: If True, only download changed or new files.
        prune: If True, delete local files that were removed in upstream GCS.
        clear_existing: If True, clear existing local adventure directory before downloading.
        overwrite: Optional override. If True and not diff, clears existing directory.
        dry_run: If True, simulate operations without writing files.

    Returns:
        Dictionary summarizing downloaded files count, bytes, title, etc.
    """
    adv_target_path = target_dir / adventure_slug
    title = adventure_slug.replace("-", " ").title()

    if is_adventure_excluded(adventure_slug):
        print(f"⚠️ Skipping '{adventure_slug}': example adventure is excluded from GCS download.")
        return {
            "id": adventure_slug,
            "title": title,
            "files_count": 0,
            "downloaded_files": [],
            "skipped_count": 0,
            "skipped_files": [],
            "skipped_bytes": 0,
            "pruned_count": 0,
            "cleared_count": 0,
            "total_bytes": 0,
            "target_path": str(adv_target_path),
            "excluded": True,
        }

    # Peek at metadata.json if present in items to extract actual title
    meta_item = next((b for b, rel in items if rel == "metadata.json"), None)
    if meta_item is not None:
        try:
            raw_meta = meta_item.download_as_bytes()
            meta_json = json.loads(raw_meta.decode("utf-8"))
            parsed_title = meta_json.get("title")
            if parsed_title is not None and str(parsed_title).strip() != "":
                title = str(parsed_title).strip()
        except Exception:
            pass

    print(f"\n📂 Downloading Adventure: '{title}' [{adventure_slug}] -> {adv_target_path}")

    existing_dirs = find_local_adventure_dirs(target_dir, adventure_slug)
    if adv_target_path not in existing_dirs and adv_target_path.exists():
        existing_dirs.append(adv_target_path)

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
        dest_file_path = adv_target_path / rel_file_path
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

    # Prune local files that were deleted in upstream GCS
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
        "id": adventure_slug,
        "title": title,
        "files_count": len(downloaded_files),
        "downloaded_files": downloaded_files,
        "skipped_count": len(skipped_files),
        "skipped_files": skipped_files,
        "skipped_bytes": skipped_bytes,
        "pruned_count": len(pruned_files),
        "cleared_count": cleared_count,
        "total_bytes": total_bytes,
        "target_path": str(adv_target_path),
    }


def download_all_adventures(
    bucket: DownloadBucketProtocol,
    gcs_prefix: str = DEFAULT_PREFIX,
    target_dir: Path = Path(DEFAULT_TARGET_DIR),
    adventure_filter: Optional[str] = None,
    diff: bool = True,
    prune: bool = True,
    clear_existing: bool = False,
    overwrite: Optional[bool] = None,
    dry_run: bool = False,
) -> List[Dict[str, Union[str, int, List[str], bool]]]:
    if adventure_filter and is_adventure_excluded(adventure_filter):
        print(f"⚠️ Adventure '{adventure_filter}' is an example template and is excluded from GCS download.")
        return []

    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""
    bucket_name = bucket.name

    print(f"🔍 Listing adventures in GCS: gs://{bucket_name}/{prefix_str}")

    blobs = list(bucket.list_blobs(prefix=prefix_str))
    if not blobs:
        print(f"⚠️ No files found under prefix gs://{bucket_name}/{prefix_str}")
        return []

    grouped = group_blobs_by_adventure(blobs, gcs_prefix)
    if not grouped:
        print(f"⚠️ No valid adventure subfolders found under prefix '{prefix_str}'")
        return []

    if adventure_filter:
        target = adventure_filter.lower().strip()
        if is_adventure_excluded(target):
            print(f"⚠️ Adventure '{adventure_filter}' is an example template and is excluded from GCS download.")
            return []
        slugified_target = slugify(target)
        filtered_grouped = {
            slug: items for slug, items in grouped.items()
            if (slug.lower() == target or slug.lower() == slugified_target)
            and not is_adventure_excluded(slug)
        }
        if not filtered_grouped:
            print(f"⚠️ Adventure filter '{adventure_filter}' did not match any available adventures: {list(grouped.keys())}")
            return []
        grouped = filtered_grouped

    print(f"🚀 Found {len(grouped)} adventure package(s) to download: {', '.join(grouped.keys())}")

    results: List[Dict[str, Union[str, int, List[str], bool]]] = []
    for slug, items in grouped.items():
        res = download_adventure_from_gcs(
            adventure_slug=slug,
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


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download premade adventures from GCS to local directory for editing."
    )
    parser.add_argument(
        "--target-dir",
        default=os.getenv("NARRATRON_ADVENTURES_DIR", DEFAULT_TARGET_DIR),
        help=f"Local target folder to save downloaded adventures (default: {DEFAULT_TARGET_DIR})",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_ADVENTURES_BUCKET", DEFAULT_BUCKET),
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--adventure",
        default=None,
        help="Specific adventure slug or title to download (e.g. 'lesovik-station' or 'the-trader')",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("GCS_ADVENTURES_PREFIX", DEFAULT_PREFIX),
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
        help="Clear existing local adventure directory before downloading (default: False)",
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

    client = None
    bucket: Optional[DownloadBucketProtocol] = None
    if not args.dry_run:
        try:
            from google.cloud import storage
            project = os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")
            client = storage.Client(project=project)
            real_bucket = client.bucket(args.bucket)
            if not real_bucket.exists():
                print(f"❌ Bucket '{args.bucket}' does not exist or cannot be accessed.", file=sys.stderr)
                sys.exit(1)
            bucket = real_bucket
        except Exception as e:
            print(f"❌ GCS Client Initialization Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        class MockBucket:
            name: str = args.bucket
            def list_blobs(self, prefix: str = "") -> List[DownloadBlobProtocol]:
                return []
        bucket = MockBucket()

    if bucket is not None:
        results = download_all_adventures(
            bucket=bucket,
            gcs_prefix=args.prefix,
            target_dir=target_path,
            adventure_filter=args.adventure,
            diff=args.diff,
            prune=args.prune,
            clear_existing=args.clear_existing,
            dry_run=args.dry_run,
        )

        if results:
            print("\n========================================================")
            print("✨ Adventure Download Summary:")
            print("========================================================")
            for r in results:
                raw_bytes = r.get("total_bytes")
                total_bytes_num = int(raw_bytes) if raw_bytes is not None and not isinstance(raw_bytes, list) and not isinstance(raw_bytes, bool) else 0
                mb = total_bytes_num / (1024 * 1024)
                skipped_count = r.get("skipped_count")
                skipped_str = f", {skipped_count} unchanged" if skipped_count else ""
                pruned_count = r.get("pruned_count")
                pruned_str = f", {pruned_count} pruned" if pruned_count else ""
                cleared_count = r.get("cleared_count")
                cleared_str = f" [cleared {cleared_count} old file(s)]" if cleared_count else ""
                print(f" • {r['title']} [{r['id']}] - {r['files_count']} downloaded ({mb:.2f} MB){skipped_str}{pruned_str}{cleared_str} -> {r['target_path']}")
            print("========================================================\n")


if __name__ == "__main__":
    main()
