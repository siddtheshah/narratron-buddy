#!/usr/bin/env python3
"""Upload premade adventures from local directory to Google Cloud Storage.

This script scans a directory of premade adventure folders (e.g. adventures),
generates or updates adventure metadata.json if missing, and uploads all assets
(theater.yaml, metadata.json, lore, references, playlists) to a shared GCS prefix.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# Ensure mimetypes knows common audio and yaml extensions
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/yaml", ".yml")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")

DEFAULT_BUCKET = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "adventures"
DEFAULT_SOURCE_DIR = "adventures"

def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def create_or_load_metadata(adventure_dir: Path) -> Dict[str, Any]:
    """Load existing metadata.json or generate from adventure folder."""
    meta_path = adventure_dir / "metadata.json"
    existing_meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            existing_meta = {}

    folder_name = adventure_dir.name
    defaults = {
        "id": slugify(folder_name),
        "title": folder_name,
        "description": f"Premade adventure package for {folder_name}.",
        "author": "Narratron Creator",
        "genre": "Adventure",
        "tags": ["Adventure", "Story"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cover_image": "",
        "difficulty": "Medium",
        "recommended_players": "1-4",
    }

    merged = {**defaults, **existing_meta}
    if not merged.get("id"):
        merged["id"] = slugify(folder_name)
    if not merged.get("created_at"):
        merged["created_at"] = datetime.now(timezone.utc).isoformat()

    # Find fallback cover image if none configured or invalid
    if not merged.get("cover_image"):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            found = list(adventure_dir.rglob(f"*{ext}"))
            if found:
                merged["cover_image"] = found[0].relative_to(adventure_dir).as_posix()
                break

    # Save to metadata.json in folder if updated/missing
    if not meta_path.exists() or merged != existing_meta:
        meta_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def collect_adventure_files(adventure_dir: Path) -> List[Path]:
    """Collect all files to upload for a single adventure folder."""
    files: List[Path] = []
    for item in adventure_dir.rglob("*"):
        if item.is_file():
            # Skip temp or cache files
            if item.name.startswith((".", "~")) or "__pycache__" in item.parts:
                continue
            files.append(item)
    return files


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


def is_file_changed(file_path: Path, remote_blob: Any) -> bool:
    """Determine if a local file differs from a remote GCS blob.

    Compares size, MD5 hash, and CRC32c checksum to avoid unnecessary transfers.
    Returns True if the file is new or modified, False if unchanged.
    """
    if remote_blob is None:
        return True

    local_size = file_path.stat().st_size
    remote_size = getattr(remote_blob, "size", None)

    # Size difference indicates an immediate modification
    if isinstance(remote_size, int) and local_size != remote_size:
        return True

    # Compare MD5 hash if available on remote blob
    remote_md5 = getattr(remote_blob, "md5_hash", None)
    if isinstance(remote_md5, str) and remote_md5:
        local_md5 = compute_file_md5(file_path)
        return local_md5 != remote_md5

    # Compare CRC32c checksum if available on remote blob
    remote_crc32c = getattr(remote_blob, "crc32c", None)
    if isinstance(remote_crc32c, str) and remote_crc32c:
        local_crc32c = compute_file_crc32c(file_path)
        if local_crc32c is not None:
            return local_crc32c != remote_crc32c

    # If sizes match and no hashes are available to compare, consider unchanged
    if isinstance(remote_size, int) and local_size == remote_size:
        return False

    return True


def clear_exact_matching_adventure(
    bucket: Any,
    gcs_prefix: str,
    adventure_slug: str,
    adventure_title: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """Safely clear existing files for an adventure in GCS ONLY if there is an exact name/slug match.

    Safety Guards:
    1. Validates that adventure_slug and gcs_prefix are non-empty, safe strings.
    2. Strictly targets only the exact path: '{gcs_prefix}/{adventure_slug}/'.
    3. If metadata.json exists at that exact prefix in GCS, verifies the existing 'id' or 'title'
       matches this adventure exactly before deleting.
    4. Validates that every blob targeted for deletion strictly starts with the exact prefix.
    """
    clean_prefix = gcs_prefix.strip("/")
    clean_slug = adventure_slug.strip("/")

    # Strict safety assertions
    if not clean_prefix or clean_prefix in ("/", ".", "*"):
        print(f"  ⚠️ Deletion aborted: invalid GCS prefix '{gcs_prefix}'.", file=sys.stderr)
        return 0

    if not clean_slug or clean_slug in ("/", ".", "*") or len(clean_slug) < 2:
        print(f"  ⚠️ Deletion aborted: invalid or unsafe adventure slug '{adventure_slug}'.", file=sys.stderr)
        return 0

    exact_target_prefix = f"{clean_prefix}/{clean_slug}/"

    try:
        existing_blobs = list(bucket.list_blobs(prefix=exact_target_prefix))
    except Exception as e:
        print(f"  ⚠️ Could not check existing blobs under '{exact_target_prefix}': {e}", file=sys.stderr)
        return 0

    if not existing_blobs:
        # Nothing exists at this exact prefix, nothing to clear
        return 0

    # Verify existing metadata.json if present to ensure exact match confirmation
    meta_blob = next((b for b in existing_blobs if b.name == f"{exact_target_prefix}metadata.json"), None)
    if meta_blob:
        try:
            raw_meta = meta_blob.download_as_bytes()
            existing_meta = json.loads(raw_meta.decode("utf-8"))
            existing_id = (existing_meta.get("id") or "").strip()
            existing_title = (existing_meta.get("title") or "").strip()

            # Exact match check
            matches_id = existing_id and existing_id == clean_slug
            matches_title = (
                bool(existing_title)
                and bool(adventure_title)
                and (existing_title.strip().lower() == adventure_title.strip().lower())
            )

            if not (matches_id or matches_title):
                print(
                    f"  ⚠️ Deletion skipped: Existing metadata at '{exact_target_prefix}' "
                    f"(id='{existing_id}', title='{existing_title}') does not match "
                    f"(id='{clean_slug}', title='{adventure_title}').",
                    file=sys.stderr,
                )
                return 0
        except Exception as e:
            print(f"  ⚠️ Warning: Could not parse existing metadata.json: {e}.", file=sys.stderr)

    # Validate that every single blob to be deleted strictly starts with the exact target prefix
    for blob in existing_blobs:
        if not blob.name.startswith(exact_target_prefix):
            print(
                f"  ❌ Deletion aborted: Blob '{blob.name}' does not strictly match exact prefix '{exact_target_prefix}'.",
                file=sys.stderr,
            )
            return 0

    total_deleted = 0
    if dry_run:
        print(f"  [DRY-RUN] Exact match confirmed: Found {len(existing_blobs)} existing blob(s) in gs://{getattr(bucket, 'name', 'bucket')}/{exact_target_prefix} - would clear before upload.")
        total_deleted = len(existing_blobs)
    else:
        print(f"  🗑️  Exact match confirmed: Clearing {len(existing_blobs)} existing blob(s) from gs://{getattr(bucket, 'name', 'bucket')}/{exact_target_prefix}...")
        try:
            if hasattr(bucket, "delete_blobs"):
                bucket.delete_blobs(existing_blobs)
                total_deleted = len(existing_blobs)
            else:
                for b in existing_blobs:
                    b.delete()
                    total_deleted += 1
        except Exception:
            for b in existing_blobs:
                try:
                    b.delete()
                    total_deleted += 1
                except Exception as e:
                    print(f"    ⚠️ Failed to delete {getattr(b, 'name', b)}: {e}", file=sys.stderr)
        print(f"  ✓ Cleared {total_deleted} existing file(s) from gs://{getattr(bucket, 'name', 'bucket')}/{exact_target_prefix}")

    return total_deleted


def upload_adventure_to_gcs(
    adventure_dir: Path,
    bucket: Any,
    gcs_prefix: str,
    clear_existing: bool = False,
    diff: bool = True,
    prune: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Upload an adventure directory to GCS with incremental diff and change detection."""
    metadata = create_or_load_metadata(adventure_dir)
    adventure_slug = metadata.get("id") or slugify(adventure_dir.name)
    adventure_title = metadata.get("title", adventure_dir.name)
    files = collect_adventure_files(adventure_dir)

    print(f"\n📂 Processing Adventure: '{adventure_title}' ({adventure_dir.name}) -> {gcs_prefix}/{adventure_slug}/")

    cleared_count = 0
    if clear_existing and bucket is not None:
        cleared_count = clear_exact_matching_adventure(
            bucket=bucket,
            gcs_prefix=gcs_prefix,
            adventure_slug=adventure_slug,
            adventure_title=adventure_title,
            dry_run=dry_run,
        )

    # Collect existing remote blobs for diffing if not cleared
    clean_prefix = gcs_prefix.strip("/")
    exact_target_prefix = f"{clean_prefix}/{adventure_slug}/"
    remote_blobs: Dict[str, Any] = {}
    if not clear_existing and bucket is not None:
        try:
            for b in bucket.list_blobs(prefix=exact_target_prefix):
                if b.name.startswith(exact_target_prefix):
                    rel = b.name[len(exact_target_prefix):]
                    if rel:
                        remote_blobs[rel] = b
        except Exception as e:
            print(f"  ⚠️ Warning: Could not list remote blobs for diffing: {e}", file=sys.stderr)

    total_bytes = 0
    skipped_bytes = 0
    uploaded_files: List[str] = []
    skipped_files: List[str] = []
    pruned_files: List[str] = []

    for file_path in files:
        rel_path = file_path.relative_to(adventure_dir).as_posix()
        blob_name = f"{gcs_prefix}/{adventure_slug}/{rel_path}".replace("//", "/")
        size = file_path.stat().st_size

        remote_blob = remote_blobs.get(rel_path)
        if diff and not clear_existing and remote_blob is not None:
            if not is_file_changed(file_path, remote_blob):
                skipped_files.append(rel_path)
                skipped_bytes += size
                continue

        change_type = "modified" if remote_blob is not None else "new"
        total_bytes += size

        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = "application/octet-stream"

        if dry_run:
            print(f"  [DRY-RUN] Would upload ({change_type}): {rel_path} ({size} bytes, {content_type}) -> gs://{getattr(bucket, 'name', 'bucket')}/{blob_name}")
        else:
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(file_path), content_type=content_type)
            print(f"  ✓ Uploaded ({change_type}): {rel_path} ({size} bytes, {content_type})")

        uploaded_files.append(rel_path)

    # Prune orphaned remote files if prune flag is enabled
    if prune and not clear_existing and bucket is not None:
        local_rel_paths = {f.relative_to(adventure_dir).as_posix() for f in files}
        orphans = [b for rel, b in remote_blobs.items() if rel not in local_rel_paths]
        for orphan_blob in orphans:
            rel = orphan_blob.name[len(exact_target_prefix):]
            if dry_run:
                print(f"  [DRY-RUN] Would prune orphaned file: {rel} from gs://{getattr(bucket, 'name', 'bucket')}/{orphan_blob.name}")
            else:
                try:
                    orphan_blob.delete()
                    print(f"  🗑️ Pruned orphaned file: {rel}")
                except Exception as e:
                    print(f"  ⚠️ Failed to prune {rel}: {e}", file=sys.stderr)
            pruned_files.append(rel)

    if skipped_files:
        skipped_mb = skipped_bytes / (1024 * 1024)
        print(f"  ⚡ Skipped {len(skipped_files)} unchanged file(s) ({skipped_mb:.2f} MB)")

    return {
        "id": adventure_slug,
        "title": adventure_title,
        "created_at": metadata.get("created_at"),
        "files_count": len(uploaded_files),
        "uploaded_files": uploaded_files,
        "skipped_count": len(skipped_files),
        "skipped_files": skipped_files,
        "skipped_bytes": skipped_bytes,
        "pruned_count": len(pruned_files),
        "cleared_count": cleared_count,
        "total_bytes": total_bytes,
    }


def main():
    load_dotenv()
    default_src = DEFAULT_SOURCE_DIR
    parser = argparse.ArgumentParser(description="Upload premade adventures to GCS")
    parser.add_argument(
        "--source-dir",
        default=os.getenv("NARRATRON_ASSETS_DIR", default_src),
        help=f"Source folder containing premade adventures (default: {default_src})",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_ADVENTURES_BUCKET", DEFAULT_BUCKET),
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--adventure",
        default=None,
        help="Specific adventure folder name or slug to upload (e.g. 'Lesovik Station' or 'lesovik-station')",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("GCS_ADVENTURES_PREFIX", DEFAULT_PREFIX),
        help=f"GCS destination prefix/folder (default: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--diff",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Diff local files against GCS and only upload changed/new files (default: True)",
    )
    parser.add_argument(
        "--prune",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Delete remote files that no longer exist locally (default: False)",
    )
    parser.add_argument(
        "--clear-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Clear all existing adventure files from GCS before uploading new version (default: False)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate upload and clearing without transferring or deleting files",
    )
    args = parser.parse_args()

    source_path = Path(args.source_dir)
    if not source_path.exists():
        print(f"❌ Error: Source directory does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)

    # Gather adventure folders from source_path
    adventure_folders: List[Path] = []
    if (source_path / "theater.yaml").exists() or (source_path / "metadata.json").exists():
        adventure_folders = [source_path]
    else:
        adventure_folders = [
            p for p in source_path.iterdir()
            if p.is_dir() and not p.name.startswith(".")
            and ((p / "theater.yaml").exists() or (p / "metadata.json").exists())
        ]

    if args.adventure:
        target = args.adventure.lower().strip()
        adventure_folders = [
            p for p in adventure_folders
            if p.name.lower() == target or slugify(p.name) == target
        ]

    if not adventure_folders:
        print(f"⚠️ No matching adventure folders found in {source_path}")
        sys.exit(0)

    diff_mode = args.diff and not args.clear_existing

    print(f"🚀 Found {len(adventure_folders)} adventure folder(s) to process")
    print(f"☁️ Target GCS Bucket: gs://{args.bucket}/{args.prefix}")
    if args.clear_existing:
        print("🧹 Clear existing matching adventures: Enabled")
    elif diff_mode:
        prune_str = " (pruning enabled)" if args.prune else ""
        print(f"⚡ Incremental diff mode: Enabled{prune_str}")

    client = None
    bucket = None
    if not args.dry_run:
        try:
            from google.cloud import storage
            project = os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")
            client = storage.Client(project=project)
            bucket = client.bucket(args.bucket)
            if not bucket.exists():
                print(f"❌ Bucket '{args.bucket}' does not exist or cannot be accessed.", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"❌ GCS Client Initialization Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Mock bucket or real bucket inspection for dry-run
        try:
            from google.cloud import storage
            project = os.getenv("GOOGLE_CLOUD_PROJECT", "narratron")
            client = storage.Client(project=project)
            bucket = client.bucket(args.bucket)
        except Exception:
            class MockBucket:
                name = args.bucket
                def list_blobs(self, prefix=""):
                    return []
                def blob(self, name):
                    return None
            bucket = MockBucket()

    results = []
    for adv_dir in adventure_folders:
        res = upload_adventure_to_gcs(
            adventure_dir=adv_dir,
            bucket=bucket,
            gcs_prefix=args.prefix.strip("/"),
            clear_existing=args.clear_existing,
            diff=diff_mode,
            prune=args.prune,
            dry_run=args.dry_run,
        )
        results.append(res)

    print("\n========================================================")
    print("✨ Adventure Upload Summary:")
    print("========================================================")
    for r in results:
        mb = r["total_bytes"] / (1024 * 1024)
        skipped_str = f", {r.get('skipped_count', 0)} unchanged" if r.get("skipped_count") else ""
        pruned_str = f", {r.get('pruned_count', 0)} pruned" if r.get("pruned_count") else ""
        cleared_str = f" [cleared {r['cleared_count']} old file(s)]" if r.get("cleared_count") else ""
        print(f" • {r['title']} [{r['id']}] - {r['files_count']} uploaded ({mb:.2f} MB){skipped_str}{pruned_str}{cleared_str} - Date: {r['created_at']}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
