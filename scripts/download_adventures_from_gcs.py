#!/usr/bin/env python3
"""Download existing adventures from Google Cloud Storage to local directory.

This script scans a GCS bucket prefix (default: 'adventures') for premade
adventure packages and downloads them to a local target directory (default: 'adventures/'),
allowing users to inspect, modify, and re-upload them using upload_adventures_to_gcs.py.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_BUCKET = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "adventures"
DEFAULT_TARGET_DIR = "adventures"


def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def group_blobs_by_adventure(blobs: List[Any], gcs_prefix: str) -> Dict[str, List[Tuple[Any, str]]]:
    """Group GCS blobs by adventure slug.

    Args:
        blobs: List of GCS blob objects (or mock objects with a .name attribute).
        gcs_prefix: The root prefix in GCS (e.g. 'adventures').

    Returns:
        Dict mapping adventure_slug -> List[(blob, relative_file_path_inside_adventure)].
    """
    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""

    grouped: Dict[str, List[Tuple[Any, str]]] = {}
    for blob in blobs:
        blob_name = blob.name
        if not blob_name.startswith(prefix_str):
            continue

        rel_path = blob_name[len(prefix_str) :] if prefix_str else blob_name
        parts = [p for p in rel_path.split("/") if p]
        if len(parts) < 2:
            # Skip top-level directory markers or single files directly under prefix
            continue

        adv_slug = parts[0]
        file_rel_path = "/".join(parts[1:])
        if adv_slug not in grouped:
            grouped[adv_slug] = []
        grouped[adv_slug].append((blob, file_rel_path))

    return grouped


def download_adventure_from_gcs(
    adventure_slug: str,
    items: List[Tuple[Any, str]],
    target_dir: Path,
    overwrite: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Download files for a single adventure into target_dir/adventure_slug.

    Args:
        adventure_slug: The identifier/folder name of the adventure.
        items: List of (blob, relative_file_path_inside_adventure).
        target_dir: Base directory where adventure folder will be saved.
        overwrite: If True, clear existing local adventure directory before downloading.
        dry_run: If True, simulate operations without writing files.

    Returns:
        Dictionary summarizing downloaded files count, bytes, title, etc.
    """
    adv_target_path = target_dir / adventure_slug
    title = adventure_slug.replace("-", " ").title()

    # Peek at metadata.json if present in items to extract actual title
    meta_item = next((b for b, rel in items if rel == "metadata.json"), None)
    if meta_item:
        try:
            raw_meta = meta_item.download_as_bytes()
            meta_json = json.loads(raw_meta.decode("utf-8"))
            if meta_json.get("title"):
                title = meta_json["title"]
        except Exception:
            pass

    print(f"\n📂 Downloading Adventure: '{title}' [{adventure_slug}] -> {adv_target_path}")

    if adv_target_path.exists() and overwrite:
        if dry_run:
            print(f"  [DRY-RUN] Would clear existing local folder: {adv_target_path}")
        else:
            print(f"  🧹 Clearing existing local folder: {adv_target_path}")
            shutil.rmtree(adv_target_path)

    total_bytes = 0
    downloaded_files: List[str] = []

    for blob, rel_file_path in items:
        dest_file_path = adv_target_path / rel_file_path
        blob_size = getattr(blob, "size", 0) or 0

        if dry_run:
            print(f"  [DRY-RUN] Would download: {rel_file_path} -> {dest_file_path}")
            total_bytes += blob_size
        else:
            dest_file_path.parent.mkdir(parents=True, exist_ok=True)
            if hasattr(blob, "download_to_filename"):
                try:
                    blob.download_to_filename(str(dest_file_path))
                except Exception:
                    pass

            if not dest_file_path.exists() and hasattr(blob, "download_as_bytes"):
                content = blob.download_as_bytes()
                dest_file_path.write_bytes(content)

            file_actual_size = dest_file_path.stat().st_size if dest_file_path.exists() else blob_size
            total_bytes += file_actual_size
            print(f"  ✓ Downloaded: {rel_file_path} ({file_actual_size} bytes)")

        downloaded_files.append(rel_file_path)

    return {
        "id": adventure_slug,
        "title": title,
        "files_count": len(downloaded_files),
        "total_bytes": total_bytes,
        "target_path": str(adv_target_path),
    }


def download_all_adventures(
    bucket: Any,
    gcs_prefix: str = DEFAULT_PREFIX,
    target_dir: Path = Path(DEFAULT_TARGET_DIR),
    adventure_filter: Optional[str] = None,
    overwrite: bool = True,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch all blobs under gcs_prefix from bucket and download matching adventures."""
    clean_prefix = gcs_prefix.strip("/")
    prefix_str = f"{clean_prefix}/" if clean_prefix else ""

    print(f"🔍 Listing adventures in GCS: gs://{getattr(bucket, 'name', 'bucket')}/{prefix_str}")

    blobs = list(bucket.list_blobs(prefix=prefix_str))
    if not blobs:
        print(f"⚠️ No files found under prefix gs://{getattr(bucket, 'name', 'bucket')}/{prefix_str}")
        return []

    grouped = group_blobs_by_adventure(blobs, gcs_prefix)
    if not grouped:
        print(f"⚠️ No valid adventure subfolders found under prefix '{prefix_str}'")
        return []

    if adventure_filter:
        target = adventure_filter.lower().strip()
        slugified_target = slugify(target)
        filtered_grouped = {
            slug: items for slug, items in grouped.items()
            if slug.lower() == target or slug.lower() == slugified_target
        }
        if not filtered_grouped:
            print(f"⚠️ Adventure filter '{adventure_filter}' did not match any available adventures: {list(grouped.keys())}")
            return []
        grouped = filtered_grouped

    print(f"🚀 Found {len(grouped)} adventure package(s) to download: {', '.join(grouped.keys())}")

    results = []
    for slug, items in grouped.items():
        res = download_adventure_from_gcs(
            adventure_slug=slug,
            items=items,
            target_dir=target_dir,
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
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Overwrite existing local adventure directory before downloading (default: True)",
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
    if args.overwrite:
        print("🧹 Local Overwrite: Enabled")

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
            bucket = MockBucket()

    results = download_all_adventures(
        bucket=bucket,
        gcs_prefix=args.prefix,
        target_dir=target_path,
        adventure_filter=args.adventure,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    if results:
        print("\n========================================================")
        print("✨ Adventure Download Summary:")
        print("========================================================")
        for r in results:
            mb = r["total_bytes"] / (1024 * 1024)
            print(f" • {r['title']} [{r['id']}] - {r['files_count']} file(s) ({mb:.2f} MB) -> {r['target_path']}")
        print("========================================================\n")


if __name__ == "__main__":
    main()
