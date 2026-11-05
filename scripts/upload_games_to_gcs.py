#!/usr/bin/env python3
"""Upload pre-defined games from local directory to Google Cloud Storage.

This script scans a directory of pre-defined game folders (e.g. games/),
generates or updates game metadata.json if missing, and uploads all assets
(metadata.json, game/game.yaml, game/panel.html, game/crest.svg, stamps/*, etc.)
to a shared GCS prefix.
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
from typing import Dict, Iterable, List, Optional, Protocol, TypedDict, Union

from dotenv import load_dotenv
import yaml

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


class StorageBlobProtocol(Protocol):
    name: str
    size: Optional[int]
    md5_hash: Optional[str]
    crc32c: Optional[str]

    def download_as_bytes(self) -> bytes: ...
    def upload_from_filename(self, filename: str, content_type: Optional[str] = None) -> None: ...
    def delete(self) -> None: ...


class StorageBucketProtocol(Protocol):
    name: str

    def list_blobs(self, prefix: str = "") -> Iterable[StorageBlobProtocol]: ...
    def blob(self, blob_name: str) -> StorageBlobProtocol: ...
    def delete_blobs(self, blobs: List[StorageBlobProtocol]) -> None: ...


class UploadGameResult(TypedDict, total=False):
    id: str
    title: str
    created_at: str
    files_count: int
    uploaded_files: List[str]
    skipped_count: int
    skipped_files: List[str]
    skipped_bytes: int
    pruned_count: int
    cleared_count: int
    total_bytes: int
    excluded: bool


# Ensure mimetypes knows common game asset extensions
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/yaml", ".yml")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")

DEFAULT_BUCKET = "narratron-buddy-app-storage"
DEFAULT_PREFIX = "games"
DEFAULT_SOURCE_DIR = "games"
EXCLUDED_GAMES = {"example_game", "example-game"}


def slugify(text: str) -> str:
    """Convert text into a safe URL slug."""
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in text.lower())
    return "-".join(part for part in clean.split("-") if part)


def is_game_excluded(name_or_slug: str) -> bool:
    """Check if a game name, folder name, or slug should be excluded from GCS sync."""
    target = (name_or_slug or "").strip().lower()
    return target in EXCLUDED_GAMES or slugify(target) in EXCLUDED_GAMES


def has_game_manifest(game_dir: Path) -> bool:
    """Return True if the directory contains a game manifest or metadata.json."""
    candidates = (
        game_dir / "metadata.json",
        game_dir / "game" / "game.yaml",
        game_dir / "game" / "game.json",
        game_dir / "game.yaml",
        game_dir / "game.json",
    )
    return any(p.is_file() for p in candidates)


MetadataValue = Union[str, int, float, bool, List[str], Dict[str, str]]
MetadataDict = Dict[str, MetadataValue]


def create_or_load_metadata(game_dir: Path) -> MetadataDict:
    """Load existing metadata.json or generate from game folder."""
    meta_path = game_dir / "metadata.json"
    existing_meta: MetadataDict = {}
    if meta_path.exists():
        existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))

    folder_name = game_dir.name
    defaults: MetadataDict = {
        "id": slugify(folder_name),
        "title": folder_name,
        "description": f"Pre-defined game package for {folder_name}.",
        "author": "Narratron Creator",
        "genre": "Board Game",
        "tags": ["Game", "Board Game"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cover_image": "",
        "difficulty": "Medium",
        "recommended_players": "1-2",
    }

    merged: MetadataDict = {**defaults, **existing_meta}
    if not merged.get("id"):
        merged["id"] = slugify(folder_name)
    if not merged.get("created_at"):
        merged["created_at"] = datetime.now(timezone.utc).isoformat()

    # Find fallback cover image if none configured
    if not merged.get("cover_image"):
        preferred_crests = (game_dir / "game" / "crest.svg", game_dir / "crest.svg")
        for crest in preferred_crests:
            if crest.is_file():
                merged["cover_image"] = crest.relative_to(game_dir).as_posix()
                break
    if not merged.get("cover_image"):
        for ext in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
            found = list(game_dir.rglob(f"*{ext}"))
            if found:
                merged["cover_image"] = found[0].relative_to(game_dir).as_posix()
                break

    # Save to metadata.json in folder if updated/missing
    if not meta_path.exists() or merged != existing_meta:
        meta_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def collect_game_files(game_dir: Path) -> List[Path]:
    """Collect all files to upload for a single game folder."""
    files: List[Path] = []
    for item in game_dir.rglob("*"):
        if item.is_file():
            # Skip temp or cache files
            if item.name.startswith((".", "~")) or "__pycache__" in item.parts:
                continue
            files.append(item)
    return files


YamlScalar = Union[str, int, float, bool, None]
YamlValue = Union[YamlScalar, List[YamlScalar], Dict[str, YamlScalar]]


class UniqueKeyYamlLoader(yaml.SafeLoader):
    """YAML SafeLoader that detects duplicate mapping keys and resolves merge keys."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> Dict[str, YamlValue]:
        self.flatten_mapping(node)
        mapping: Dict[str, YamlValue] = {}
        for key_node, value_node in node.value:
            key = str(self.construct_object(key_node, deep=deep))
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key '{key}'",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def validate_game_yaml_files(game_dir: Path) -> List[Path]:
    """Validate that all YAML files in a game directory parse correctly.

    Scans for .yaml and .yml files (excluding temp/cache files) and verifies they
    can be parsed without YAML syntax errors or duplicate mapping keys.

    Returns:
        List of validated YAML file paths.

    Raises:
        ValueError: If any YAML file fails to parse or contains duplicate keys.
    """
    yaml_files: List[Path] = []
    for item in sorted(game_dir.rglob("*")):
        if item.is_file() and item.suffix.lower() in (".yaml", ".yml"):
            if item.name.startswith((".", "~")) or "__pycache__" in item.parts:
                continue
            yaml_files.append(item)

    for yml_path in yaml_files:
        try:
            content = yml_path.read_text(encoding="utf-8")
            list(yaml.load_all(content, Loader=UniqueKeyYamlLoader))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            rel = yml_path.relative_to(game_dir).as_posix()
            raise ValueError(
                f"Failed to parse YAML file in game '{game_dir.name}' ({rel}): {e}"
            ) from e

    return yaml_files


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


def is_file_changed(file_path: Path, remote_blob: Optional[StorageBlobProtocol]) -> bool:
    """Determine if a local file differs from a remote GCS blob.

    Compares size, MD5 hash, and CRC32c checksum to avoid unnecessary transfers.
    Returns True if the file is new or modified, False if unchanged.
    """
    if remote_blob is None:
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


def clear_exact_matching_game(
    bucket: StorageBucketProtocol,
    gcs_prefix: str,
    game_slug: str,
    game_title: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """Safely clear existing files for a game in GCS ONLY if there is an exact name/slug match."""
    clean_prefix = gcs_prefix.strip("/")
    clean_slug = game_slug.strip("/")

    if not clean_prefix or clean_prefix in ("/", ".", "*"):
        print(f"  ⚠️ Deletion aborted: invalid GCS prefix '{gcs_prefix}'.", file=sys.stderr)
        return 0

    if not clean_slug or clean_slug in ("/", ".", "*") or len(clean_slug) < 2:
        print(f"  ⚠️ Deletion aborted: invalid or unsafe game slug '{game_slug}'.", file=sys.stderr)
        return 0

    exact_target_prefix = f"{clean_prefix}/{clean_slug}/"
    existing_blobs = list(bucket.list_blobs(prefix=exact_target_prefix))
    if not existing_blobs:
        return 0

    meta_blob = next((b for b in existing_blobs if b.name == f"{exact_target_prefix}metadata.json"), None)
    if meta_blob is not None:
        raw_meta = meta_blob.download_as_bytes()
        existing_meta = json.loads(raw_meta.decode("utf-8"))
        existing_id = str(existing_meta.get("id") or "").strip()
        existing_title = str(existing_meta.get("title") or "").strip()

        matches_id = bool(existing_id) and (existing_id == clean_slug or slugify(existing_id) == clean_slug)
        matches_title = (
            bool(existing_title)
            and bool(game_title)
            and (
                existing_title.strip().lower() == game_title.strip().lower()
                or slugify(existing_title) == clean_slug
            )
        )

        if not (matches_id or matches_title):
            print(
                f"  ⚠️ Deletion skipped: Existing metadata at '{exact_target_prefix}' "
                f"(id='{existing_id}', title='{existing_title}') does not match "
                f"(id='{clean_slug}', title='{game_title}').",
                file=sys.stderr,
            )
            return 0

    for blob in existing_blobs:
        if not blob.name.startswith(exact_target_prefix):
            print(
                f"  ❌ Deletion aborted: Blob '{blob.name}' does not strictly match exact prefix '{exact_target_prefix}'.",
                file=sys.stderr,
            )
            return 0

    total_deleted = 0
    bucket_name = bucket.name
    if dry_run:
        print(
            f"  [DRY-RUN] Exact match confirmed: Found {len(existing_blobs)} existing blob(s) "
            f"in gs://{bucket_name}/{exact_target_prefix} - would clear before upload."
        )
        total_deleted = len(existing_blobs)
    else:
        print(
            f"  🗑️  Exact match confirmed: Clearing {len(existing_blobs)} existing blob(s) "
            f"from gs://{bucket_name}/{exact_target_prefix}..."
        )
        try:
            bucket.delete_blobs(existing_blobs)
            total_deleted = len(existing_blobs)
        except Exception:
            for b in existing_blobs:
                b.delete()
                total_deleted += 1
        print(f"  ✓ Cleared {total_deleted} existing file(s) from gs://{bucket_name}/{exact_target_prefix}")

    return total_deleted


def upload_game_to_gcs(
    game_dir: Path,
    bucket: Optional[StorageBucketProtocol],
    gcs_prefix: str,
    diff: bool = True,
    prune: bool = True,
    clear_existing: bool = False,
    overwrite: Optional[bool] = None,
    dry_run: bool = False,
) -> UploadGameResult:
    """Upload a game directory to GCS with incremental diff and change detection."""
    should_clear = clear_existing or (overwrite is True and not diff)
    metadata = create_or_load_metadata(game_dir)
    raw_slug = metadata.get("id")
    game_slug = str(raw_slug) if raw_slug is not None else slugify(game_dir.name)
    raw_title = metadata.get("title")
    game_title = str(raw_title) if raw_title is not None else game_dir.name

    if is_game_excluded(game_dir.name) or is_game_excluded(game_slug):
        print(f"⚠️ Skipping '{game_title}' ({game_dir.name}): excluded example game.")
        return {
            "id": game_slug,
            "title": game_title,
            "created_at": str(metadata.get("created_at") or ""),
            "files_count": 0,
            "uploaded_files": [],
            "skipped_count": 0,
            "skipped_files": [],
            "skipped_bytes": 0,
            "pruned_count": 0,
            "cleared_count": 0,
            "total_bytes": 0,
            "excluded": True,
        }

    validate_game_yaml_files(game_dir)

    files = collect_game_files(game_dir)

    print(f"\n📂 Processing Game: '{game_title}' ({game_dir.name}) -> {gcs_prefix}/{game_slug}/")

    cleared_count = 0
    if should_clear and bucket is not None:
        cleared_count = clear_exact_matching_game(
            bucket=bucket,
            gcs_prefix=gcs_prefix,
            game_slug=game_slug,
            game_title=game_title,
            dry_run=dry_run,
        )

    clean_prefix = gcs_prefix.strip("/")
    exact_target_prefix = f"{clean_prefix}/{game_slug}/"
    remote_blobs: Dict[str, StorageBlobProtocol] = {}
    if not should_clear and bucket is not None:
        for b in bucket.list_blobs(prefix=exact_target_prefix):
            if b.name.startswith(exact_target_prefix):
                rel = b.name[len(exact_target_prefix) :]
                if rel:
                    remote_blobs[rel] = b

    total_bytes = 0
    skipped_bytes = 0
    uploaded_files: List[str] = []
    skipped_files: List[str] = []
    pruned_files: List[str] = []

    for file_path in files:
        rel_path = file_path.relative_to(game_dir).as_posix()
        blob_name = f"{gcs_prefix}/{game_slug}/{rel_path}".replace("//", "/")
        size = file_path.stat().st_size

        remote_blob = remote_blobs.get(rel_path)
        if diff and not should_clear and remote_blob is not None:
            if not is_file_changed(file_path, remote_blob):
                skipped_files.append(rel_path)
                skipped_bytes += size
                continue

        change_type = "modified" if remote_blob is not None else "new"
        total_bytes += size

        content_type, _ = mimetypes.guess_type(file_path.name)
        if content_type is None:
            content_type = "application/octet-stream"

        bucket_name = bucket.name if bucket is not None else "bucket"
        if dry_run:
            print(
                f"  [DRY-RUN] Would upload ({change_type}): {rel_path} "
                f"({size} bytes, {content_type}) -> gs://{bucket_name}/{blob_name}"
            )
        else:
            if bucket is not None:
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(str(file_path), content_type=content_type)
            print(f"  ✓ Uploaded ({change_type}): {rel_path} ({size} bytes, {content_type})")

        uploaded_files.append(rel_path)

    if prune and not should_clear and bucket is not None:
        local_rel_paths = {f.relative_to(game_dir).as_posix() for f in files}
        orphans = [b for rel, b in remote_blobs.items() if rel not in local_rel_paths]
        bucket_name = bucket.name
        for orphan_blob in orphans:
            rel = orphan_blob.name[len(exact_target_prefix) :]
            if dry_run:
                print(f"  [DRY-RUN] Would prune orphaned file: {rel} from gs://{bucket_name}/{orphan_blob.name}")
            else:
                orphan_blob.delete()
                print(f"  🗑️ Pruned orphaned file: {rel}")
            pruned_files.append(rel)

    if skipped_files:
        skipped_mb = skipped_bytes / (1024 * 1024)
        print(f"  ⚡ Skipped {len(skipped_files)} unchanged file(s) ({skipped_mb:.2f} MB)")

    return {
        "id": game_slug,
        "title": game_title,
        "created_at": str(metadata.get("created_at") or ""),
        "files_count": len(uploaded_files),
        "uploaded_files": uploaded_files,
        "skipped_count": len(skipped_files),
        "skipped_files": skipped_files,
        "skipped_bytes": skipped_bytes,
        "pruned_count": len(pruned_files),
        "cleared_count": cleared_count,
        "total_bytes": total_bytes,
    }


def main() -> None:
    load_dotenv()
    default_src = DEFAULT_SOURCE_DIR
    parser = argparse.ArgumentParser(description="Upload pre-defined games to GCS")
    parser.add_argument(
        "--source-dir",
        default=os.getenv("NARRATRON_GAMES_DIR", default_src),
        help=f"Source folder containing pre-defined games (default: {default_src})",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET", DEFAULT_BUCKET),
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--game",
        default=None,
        help="Specific game folder name or slug to upload (e.g. 'wacky-chess' or 'peaks-of-tianmu')",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("GCS_GAMES_PREFIX", DEFAULT_PREFIX),
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
        default=True,
        help="Delete remote files that no longer exist locally (default: True)",
    )
    parser.add_argument(
        "--clear-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Clear all existing game files from GCS before uploading (default: False)",
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

    game_folders: List[Path] = []
    if has_game_manifest(source_path):
        if not is_game_excluded(source_path.name):
            game_folders = [source_path]
    else:
        game_folders = [
            p
            for p in source_path.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and not is_game_excluded(p.name)
            and has_game_manifest(p)
        ]

    if args.game:
        target = args.game.lower().strip()
        if is_game_excluded(target):
            print(f"⚠️ Game '{args.game}' is an example template and is excluded from GCS upload.")
            sys.exit(0)
        game_folders = [
            p
            for p in game_folders
            if p.name.lower() == target or slugify(p.name) == target
        ]

    if not game_folders:
        print(f"⚠️ No matching game folders found in {source_path}")
        sys.exit(0)

    clear_mode = args.clear_existing
    diff_mode = args.diff and not args.clear_existing

    print(f"🚀 Found {len(game_folders)} game folder(s) to process")
    print(f"☁️ Target GCS Bucket: gs://{args.bucket}/{args.prefix}")
    if clear_mode:
        print("🧹 Clear existing matching games: Enabled")
    elif diff_mode:
        prune_str = " (pruning enabled)" if args.prune else ""
        print(f"⚡ Incremental diff mode: Enabled{prune_str}")

    print(f"🔍 Validating YAML files in {len(game_folders)} game folder(s)...")
    try:
        for g_dir in game_folders:
            validate_game_yaml_files(g_dir)
        print("  ✓ All game YAML files parsed successfully.")
    except ValueError as e:
        print(f"❌ YAML Validation Error: {e}", file=sys.stderr)
        sys.exit(1)

    bucket: Optional[StorageBucketProtocol] = None
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

            def list_blobs(self, prefix: str = "") -> List[StorageBlobProtocol]:
                return []

            def blob(self, blob_name: str) -> StorageBlobProtocol:
                raise NotImplementedError

            def delete_blobs(self, blobs: List[StorageBlobProtocol]) -> None:
                pass

        bucket = MockBucket()

    results: List[UploadGameResult] = []
    for g_dir in game_folders:
        res = upload_game_to_gcs(
            game_dir=g_dir,
            bucket=bucket,
            gcs_prefix=args.prefix.strip("/"),
            diff=diff_mode,
            prune=args.prune,
            clear_existing=clear_mode,
            dry_run=args.dry_run,
        )
        results.append(res)

    print("\n========================================================")
    print("✨ Game Upload Summary:")
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
            f" • {r.get('title', '')} [{r.get('id', '')}] - {r.get('files_count', 0)} uploaded "
            f"({mb:.2f} MB){skipped_str}{pruned_str}{cleared_str} - Date: {r.get('created_at', '')}"
        )
    print("========================================================\n")


if __name__ == "__main__":
    main()
