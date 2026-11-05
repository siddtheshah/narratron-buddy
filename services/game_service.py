"""Service for managing pre-defined games stored on disk or mounted storage.

Lists game packages through local or mounted storage and downloads cloud assets
directly through the GCS Python client when attaching a game to a theater.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import mimetypes
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from google.cloud import storage
from google.cloud.storage import transfer_manager
from pydantic import JsonValue, TypeAdapter

from absl import flags
from components.theater_manager import asset_destination
from components.theater_package import IMAGE_EXTENSIONS, safe_asset_path
from game_engine.game_source import GAME_FILES, GAME_FILENAMES

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, games, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS

# Ensure mime types are registered
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/yaml", ".yaml")
mimetypes.add_type("text/yaml", ".yml")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")

CACHE_CHECK_INTERVAL_SECONDS = 60.0
DEFAULT_GAMES_BUCKET = "narratron-buddy-app-storage"
DEFAULT_GAMES_PREFIX = "games"
DOWNLOAD_WORKERS = 8

COVER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")

logger = logging.getLogger(__name__)


def get_games_root() -> Path:
    """Return the game-data root for the selected runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return Path(__file__).parent.parent / "games"
    return Path("/mnt/storage/games")


def ensure_games_root() -> Path:
    """Return and create the selected game-data root."""
    games_root = get_games_root().resolve()
    games_root.mkdir(parents=True, exist_ok=True)
    return games_root


class GameService:
    """Provides access to pre-defined game packages on local or mounted storage."""

    def __init__(self, base_dir: Path | str) -> None:
        self._base_dir: Path | None = Path(base_dir).resolve()
        self._games_cache: list[dict[str, JsonValue]] | None = None
        self._cache_key: tuple[tuple[str, int, int], ...] | None = None
        self._refresh_lock = Lock()

    @property
    def base_dir(self) -> Path:
        """Return the effective games root directory."""
        if self._base_dir is not None:
            return self._base_dir
        return get_games_root().resolve()

    @base_dir.setter
    def base_dir(self, value: Path | str | None) -> None:
        self._base_dir = Path(value).resolve() if value is not None else None

    @property
    def local_dir(self) -> Path:
        return self.base_dir

    def invalidate_cache(self) -> None:
        """Clear cached game listings."""
        with self._refresh_lock:
            self._games_cache = None
            self._cache_key = None

    def _get_cache_key(self) -> tuple[tuple[str, int, int], ...]:
        """Fingerprint package paths and file stats without reading asset contents."""
        root = self.base_dir
        if not root.exists():
            return ()
        entries: list[tuple[str, int, int]] = []
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            for path in [folder, *folder.rglob("*")]:
                stat = path.stat()
                entries.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(entries))

    def refresh_if_changed(self, force_refresh: bool = False) -> bool:
        """Atomically replace the snapshot only when its source fingerprint changes."""
        with self._refresh_lock:
            cache_key = self._get_cache_key()
            if not force_refresh and self._games_cache is not None and cache_key == self._cache_key:
                return False
            games = self._fetch_games()
            games.sort(key=lambda game: str(game.get("created_at") or ""), reverse=True)
            self._games_cache = games
            self._cache_key = cache_key
            return True

    def list_games(self, force_refresh: bool = False) -> list[dict[str, JsonValue]]:
        """List all pre-defined games sorted by newest first (created_at desc)."""
        if force_refresh or self._games_cache is None:
            self.refresh_if_changed(force_refresh=force_refresh)
        return self._games_cache or []

    def get_game(self, game_id: str) -> dict[str, JsonValue] | None:
        """Retrieve metadata for a specific game by ID."""
        for game in self.list_games():
            if game.get("id") == game_id:
                return game
        return None

    @staticmethod
    def _read_metadata(meta_file: Path) -> dict[str, JsonValue]:
        """Parse metadata.json into a JSON dictionary."""
        if not meta_file.is_file():
            return {}
        return TypeAdapter(dict[str, JsonValue]).validate_json(meta_file.read_text(encoding="utf-8"))

    def _fetch_games(self) -> list[dict[str, JsonValue]]:
        """Read game packages from the base directory."""
        target_dir = self.base_dir
        if not target_dir.exists():
            return []

        results: list[dict[str, JsonValue]] = []
        for folder in target_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            meta = self._read_metadata(folder / "metadata.json")
            raw_id = meta.get("id")
            game_id = str(raw_id) if raw_id else folder.name.lower().replace(" ", "-")
            if not meta:
                meta = {
                    "id": game_id,
                    "title": folder.name,
                    "description": f"Pre-defined game package for {folder.name}.",
                    "created_at": datetime.fromtimestamp(folder.stat().st_ctime, timezone.utc).isoformat(),
                }
            if not meta.get("id"):
                meta["id"] = game_id
            if not meta.get("title"):
                meta["title"] = folder.name

            declaration_file = ""
            for rel_decl in GAME_FILES:
                if (folder / rel_decl).is_file():
                    declaration_file = rel_decl
                    break
            if not declaration_file:
                for base_decl in GAME_FILENAMES:
                    if (folder / base_decl).is_file():
                        declaration_file = f"game/{base_decl}"
                        break

            has_panel = (folder / "game" / "panel.html").is_file() or (folder / "panel.html").is_file()
            svg_count = sum(
                1 for p in folder.rglob("*.svg")
                if p.is_file() and "__pycache__" not in p.parts
            )
            stamp_count = sum(
                1 for p in (folder / "stamps").rglob("*")
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
            ) if (folder / "stamps").is_dir() else 0

            meta["declaration_file"] = declaration_file
            meta["has_panel"] = has_panel
            meta["svg_count"] = svg_count
            meta["stamp_count"] = stamp_count

            if not meta.get("cover_image"):
                for ext in COVER_EXTENSIONS:
                    found = [p for p in folder.rglob(f"*{ext}") if p.is_file() and "__pycache__" not in p.parts]
                    if found:
                        meta["cover_image"] = found[0].relative_to(folder).as_posix()
                        break

            results.append(meta)

        return results

    def _find_game_folder(self, game_id: str) -> Path | None:
        target_dir = self.base_dir
        if not target_dir.exists():
            return None
        for folder in target_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            cand_id = folder.name.lower().replace(" ", "-")
            meta = self._read_metadata(folder / "metadata.json")
            if meta.get("id"):
                cand_id = str(meta["id"])
            if cand_id == game_id or folder.name == game_id:
                return folder
        return None

    def get_game_cover(self, game_id: str) -> tuple[bytes, str] | None:
        """Return (image_bytes, content_type) for the game's cover image."""
        game = self.get_game(game_id)
        cover_path = str(game.get("cover_image") or "") if game else ""

        folder = self._find_game_folder(game_id)
        if folder is None:
            return None

        if cover_path:
            img_file = (folder / cover_path).resolve()
            if folder.resolve() in img_file.parents and img_file.is_file():
                ctype = mimetypes.guess_type(str(img_file))[0] or "image/jpeg"
                return img_file.read_bytes(), ctype

        for ext in COVER_EXTENSIONS:
            found = [p for p in folder.rglob(f"*{ext}") if p.is_file() and "__pycache__" not in p.parts]
            if found:
                ctype = mimetypes.guess_type(str(found[0]))[0] or "image/jpeg"
                return found[0].read_bytes(), ctype

        return None

    def load_game_assets(self, game_id: str) -> dict[str, bytes]:
        """Load and package all attachable game files for mounting into a theater.

        Returns a mapping of theater-relative asset paths (such as ``game/game.yaml``,
        ``game/panel.html``, ``game/*.svg``, ``game/*.json``, and ``stamps/*``) to file bytes.
        """
        matched_folder = self._find_game_folder(game_id)
        if matched_folder is None:
            return {}

        use_gcs = (not FLAGS["testing_use_local"].value
                   and self.base_dir == get_games_root().resolve())
        if use_gcs:
            with TemporaryDirectory(prefix="narratron-game-") as temporary:
                local_folder = Path(temporary)
                self._download_game_folder(matched_folder.name, local_folder)
                return self._load_game_folder(local_folder)
        return self._load_game_folder(matched_folder)

    @staticmethod
    def _download_game_folder(folder_name: str, destination: Path) -> None:
        """Download a game package directly from GCS with bounded parallel transfers."""
        client = storage.Client()
        bucket = client.bucket(os.getenv("GCS_BUCKET", DEFAULT_GAMES_BUCKET))
        root_prefix = os.getenv("GCS_GAMES_PREFIX", DEFAULT_GAMES_PREFIX).strip("/")
        prefix = f"{root_prefix}/{folder_name}/" if root_prefix else f"{folder_name}/"
        names = [blob.name.removeprefix(prefix) for blob in bucket.list_blobs(prefix=prefix)
                 if not blob.name.endswith("/")]
        if not names:
            raise FileNotFoundError(f"No game files found under gs://{bucket.name}/{prefix}")
        for name in names:
            asset_destination(destination, name)
        transfer_manager.download_many_to_path(
            bucket, names, destination_directory=str(destination), blob_name_prefix=prefix,
            worker_type=transfer_manager.THREAD, max_workers=DOWNLOAD_WORKERS,
            raise_exception=True,
        )

    @staticmethod
    def _load_game_folder(folder: Path) -> dict[str, bytes]:
        """Extract attachable game and stamp files from a local or downloaded game package."""
        assets: dict[str, bytes] = {}
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(folder).as_posix()
            parts = [p for p in rel.split("/") if p]
            if not parts or any(p.startswith(".") for p in parts) or "__pycache__" in parts:
                continue
            filename = file_path.name
            suffix = file_path.suffix.lower()
            if suffix in {".py", ".pyc", ".pyo"}:
                continue

            target_rel: str | None = None
            if rel in GAME_FILES:
                target_rel = rel
            elif rel in GAME_FILENAMES:
                target_rel = f"game/{rel}"
            elif rel in ("game/panel.html", "panel.html"):
                target_rel = "game/panel.html"
            elif parts[0] == "game" and suffix in {".svg", ".json"}:
                target_rel = rel
            elif len(parts) == 1 and suffix == ".svg":
                target_rel = f"game/{filename}"
            elif parts[0] == "stamps" and suffix in IMAGE_EXTENSIONS:
                target_rel = rel

            if target_rel is not None:
                safe_asset_path(folder, target_rel)
                assets[target_rel] = file_path.read_bytes()

        return assets
