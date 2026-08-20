#!/usr/bin/env python3
"""Upload premade adventures from local directory to Google Cloud Storage.

This script scans a directory of premade adventure folders (e.g. C:\\Narratron Assets),
generates or updates adventure metadata.json if missing, and uploads all assets
(theater.yaml, metadata.json, lore, references, playlists) to a shared GCS prefix.
"""

import argparse
from datetime import datetime, timezone
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
DEFAULT_SOURCE_DIR = r"C:\Narratron Assets"

DEFAULT_ADVENTURE_METADATA: Dict[str, Dict[str, Any]] = {
    "Lesovik Station": {
        "id": "lesovik-station",
        "title": "Lesovik Station: The Arctic Mystery",
        "description": "An isolated arctic research station plunged into a deadly blizzard after the mysterious death of Dr. Kathlyn Stark. Unravel motives, investigate researchers, and survive the cold.",
        "author": "Narratron Team",
        "genre": "Sci-Fi Mystery",
        "tags": ["Sci-Fi", "Mystery", "Survival", "Arctic", "Thriller"],
        "created_at": "2026-08-18T18:00:00Z",
        "cover_image": "references/lesovik_station_cover.jpg",
        "difficulty": "Intermediate",
        "recommended_players": "1-4",
    },
    "The Witches": {
        "id": "the-witches",
        "title": "The Witches of Muthren",
        "description": "Journey into the secluded province of Muthren to seek dark favors from a capricious coven of three witches—each demanding twisted bargains before granting your wish.",
        "author": "Narratron Team",
        "genre": "Dark Fantasy",
        "tags": ["Dark Fantasy", "Magic", "Folklore", "Coven", "Roleplay"],
        "created_at": "2026-08-17T14:30:00Z",
        "cover_image": "references/the_witches_cover.jpg",
        "difficulty": "Hard",
        "recommended_players": "1-5",
    },
    "Umbral Dungeon": {
        "id": "umbral-dungeon",
        "title": "Umbral Dungeon: Depths of the Fallen",
        "description": "Step into the long-sealed Umbral Dungeon as an outside inspector uncovering decades of corruption, eerie relics, and shadowy dangers in a low-magic dark fantasy realm.",
        "author": "Narratron Team",
        "genre": "Dungeon Crawler",
        "tags": ["Dark Fantasy", "Dungeon Crawl", "Horror", "Exploration"],
        "created_at": "2026-08-16T12:00:00Z",
        "cover_image": "references/umbral_dungeon_cover.jpg",
        "difficulty": "Hard",
        "recommended_players": "1-4",
    },
    "Varlkasseg": {
        "id": "varlkasseg",
        "title": "Varlkasseg: The Viking Chronicle",
        "description": "Join Bjorn the Bard and a hardy band of Norse heroes traversing harsh craters and tribal lands in this gritty, heroic fantasy adventure.",
        "author": "Narratron Team",
        "genre": "Norse Fantasy",
        "tags": ["Norse Fantasy", "Viking", "Heroic", "Party", "Combat"],
        "created_at": "2026-08-15T09:00:00Z",
        "cover_image": "references/locations/daawes crater.png",
        "difficulty": "Medium",
        "recommended_players": "1-6",
    },
    "Demo": {
        "id": "demo",
        "title": "Narratron Adventure Showcase",
        "description": "A versatile adventure sampler featuring diverse environments from the Sunken Library and Ink Monastery to Wild West showdowns, complete with character portraits and rich soundtracks.",
        "author": "Narratron Team",
        "genre": "Multi-Genre Showcase",
        "tags": ["Showcase", "Multi-Genre", "Cinematic", "Introductory"],
        "created_at": "2026-08-14T10:00:00Z",
        "cover_image": "reference_library/the fate written.png",
        "difficulty": "Beginner",
        "recommended_players": "1-4",
    },
    "Pantheon of Hearts": {
        "id": "pantheon-of-hearts",
        "title": "Pantheon of Hearts: Dating the Divine",
        "description": "Summoned to Mount Olympus after accidentally swiping right on Cupid's divine matchmaker app, you must survive hilarious speed-dates with neurotic deities, brooding psychopomps, and chaotic tricksters. Balance affection scores, avoid divine smiting, and find true love with the immortals!",
        "author": "Narratron Team",
        "genre": "Romantic Comedy / Dating Sim",
        "tags": ["Dating Sim", "Comedy", "Mythology", "Romance", "Interactive Fiction", "Roleplay"],
        "created_at": "2026-08-19T12:00:00Z",
        "cover_image": "references/pantheon_cover.jpg",
        "difficulty": "Casual / Intermediate",
        "recommended_players": "1-2",
    },
}


def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def create_or_load_metadata(adventure_dir: Path) -> Dict[str, Any]:
    """Load existing metadata.json or generate from defaults."""
    meta_path = adventure_dir / "metadata.json"
    existing_meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            existing_meta = {}

    folder_name = adventure_dir.name
    defaults = DEFAULT_ADVENTURE_METADATA.get(
        folder_name,
        {
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
        },
    )

    merged = {**defaults, **existing_meta}
    if not merged.get("id"):
        merged["id"] = slugify(folder_name)
    if not merged.get("created_at"):
        merged["created_at"] = defaults.get("created_at") or datetime.now(timezone.utc).isoformat()

    # Find fallback cover image if none configured or invalid
    if not merged.get("cover_image"):
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            found = list(adventure_dir.rglob(f"*{ext}"))
            if found:
                merged["cover_image"] = found[0].relative_to(adventure_dir).as_posix()
                break

    # Save to metadata.json in folder if updated/missing
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
    clear_existing: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Upload an adventure directory to GCS, clearing existing exact matching version first."""
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

    total_bytes = 0
    uploaded_files: List[str] = []

    for file_path in files:
        rel_path = file_path.relative_to(adventure_dir).as_posix()
        blob_name = f"{gcs_prefix}/{adventure_slug}/{rel_path}".replace("//", "/")
        size = file_path.stat().st_size
        total_bytes += size

        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = "application/octet-stream"

        if dry_run:
            print(f"  [DRY-RUN] Would upload: {rel_path} ({size} bytes, {content_type}) -> gs://{getattr(bucket, 'name', 'bucket')}/{blob_name}")
        else:
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(file_path), content_type=content_type)
            print(f"  ✓ Uploaded: {rel_path} ({size} bytes, {content_type})")

        uploaded_files.append(rel_path)

    return {
        "id": adventure_slug,
        "title": adventure_title,
        "created_at": metadata.get("created_at"),
        "files_count": len(uploaded_files),
        "cleared_count": cleared_count,
        "total_bytes": total_bytes,
    }


def main():
    load_dotenv()
    default_src = (
        DEFAULT_SOURCE_DIR
        if Path(DEFAULT_SOURCE_DIR).exists()
        else ("adventures" if Path("adventures").exists() else DEFAULT_SOURCE_DIR)
    )
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
        "--clear-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear existing adventure files from GCS before uploading new version (default: True)",
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

    # Check if source_path is itself a single adventure folder
    if (source_path / "theater.yaml").exists() or (source_path / "metadata.json").exists():
        adventure_folders = [source_path]
    else:
        adventure_folders = [p for p in source_path.iterdir() if p.is_dir() and not p.name.startswith(".")]

    if args.adventure:
        target = args.adventure.lower().strip()
        adventure_folders = [
            p for p in adventure_folders
            if p.name.lower() == target or slugify(p.name) == target
        ]

    if not adventure_folders:
        print(f"⚠️ No matching adventure folders found in {source_path}")
        sys.exit(0)

    print(f"🚀 Found {len(adventure_folders)} adventure folder(s) to process")
    print(f"☁️ Target GCS Bucket: gs://{args.bucket}/{args.prefix}")
    if args.clear_existing:
        print("🧹 Clear existing matching adventures: Enabled")

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
            dry_run=args.dry_run,
        )
        results.append(res)

    print("\n========================================================")
    print("✨ Adventure Upload Summary:")
    print("========================================================")
    for r in results:
        mb = r["total_bytes"] / (1024 * 1024)
        cleared_str = f" [cleared {r['cleared_count']} old file(s)]" if r.get("cleared_count") else ""
        print(f" • {r['title']} [{r['id']}] - {r['files_count']} files uploaded ({mb:.2f} MB){cleared_str} - Date: {r['created_at']}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
