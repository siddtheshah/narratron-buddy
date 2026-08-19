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
    "Animation Test": {
        "id": "animation-test",
        "title": "Animation & Scene Dynamic Test",
        "description": "High-octane scene testing suite designed for real-time visual animation triggers, dynamic music cues, and character entrance transitions.",
        "author": "Narratron Team",
        "genre": "Test Lab",
        "tags": ["Animation", "Visuals", "Testing", "Action"],
        "created_at": "2026-08-13T08:00:00Z",
        "cover_image": "reference_library/quancho.png",
        "difficulty": "Beginner",
        "recommended_players": "1",
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


def upload_adventure_to_gcs(
    adventure_dir: Path,
    bucket: Any,
    gcs_prefix: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Upload an adventure directory to GCS."""
    metadata = create_or_load_metadata(adventure_dir)
    adventure_slug = metadata.get("id") or slugify(adventure_dir.name)
    files = collect_adventure_files(adventure_dir)

    total_bytes = 0
    uploaded_files: List[str] = []

    print(f"\n📂 Processing Adventure: '{metadata.get('title')}' ({adventure_dir.name}) -> {gcs_prefix}/{adventure_slug}/")

    for file_path in files:
        rel_path = file_path.relative_to(adventure_dir).as_posix()
        blob_name = f"{gcs_prefix}/{adventure_slug}/{rel_path}".replace("//", "/")
        size = file_path.stat().st_size
        total_bytes += size

        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = "application/octet-stream"

        if dry_run:
            print(f"  [DRY-RUN] Would upload: {rel_path} ({size} bytes, {content_type}) -> gs://{bucket.name}/{blob_name}")
        else:
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(file_path), content_type=content_type)
            print(f"  ✓ Uploaded: {rel_path} ({size} bytes, {content_type})")

        uploaded_files.append(rel_path)

    return {
        "id": adventure_slug,
        "title": metadata.get("title"),
        "created_at": metadata.get("created_at"),
        "files_count": len(uploaded_files),
        "total_bytes": total_bytes,
    }


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upload premade adventures to GCS")
    parser.add_argument(
        "--source-dir",
        default=os.getenv("NARRATRON_ASSETS_DIR", DEFAULT_SOURCE_DIR),
        help=f"Source folder containing premade adventures (default: {DEFAULT_SOURCE_DIR})",
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
        "--dry-run",
        action="store_true",
        help="Simulate upload without transferring files",
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
        # Mock bucket object for dry-run
        class MockBucket:
            name = args.bucket
        bucket = MockBucket()

    results = []
    for adv_dir in adventure_folders:
        res = upload_adventure_to_gcs(
            adventure_dir=adv_dir,
            bucket=bucket,
            gcs_prefix=args.prefix.strip("/"),
            dry_run=args.dry_run,
        )
        results.append(res)

    print("\n========================================================")
    print("✨ Adventure Upload Summary:")
    print("========================================================")
    for r in results:
        mb = r["total_bytes"] / (1024 * 1024)
        print(f" • {r['title']} [{r['id']}] - {r['files_count']} files ({mb:.2f} MB) - Date: {r['created_at']}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
