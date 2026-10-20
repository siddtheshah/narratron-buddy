"""Read-only catalog and search support for generated image outputs."""

from __future__ import annotations

import os
import re
from pathlib import Path

from components.theater_manager import Theater
from utils.image_utils import extract_image_metadata_description, extract_image_metadata_title

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class ImageLibrary:
    """Discover generated outputs without cataloging reference assets."""

    output_dir: str = ""

    def __init__(self, theater: Theater) -> None:
        self.theater = theater
        self.output_dir = str(theater.image_artifacts_dir())
        config = theater.config()
        self._cover_stem = Path(config.get("starting_image", "")).stem
        self._exclude_starter = config.get("image_generation", {}).get("exclude_starter", True)

    def _is_image(self, path: str) -> bool:
        """Identify image outputs, excluding configured cover art."""
        image = Path(path)
        return image.suffix.lower() in IMAGE_EXTENSIONS and (
            not self._exclude_starter or image.stem != self._cover_stem
        )

    @staticmethod
    def _get_creation_time(path: str) -> float:
        """Return creation or modification timestamp for an image path."""
        try:
            mtime = os.path.getmtime(path)
            if mtime > 0:
                return float(mtime)
        except OSError:
            pass

        match = re.search(r"_(\d{10,12})\.[a-zA-Z0-9]+$", Path(path).name)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

        try:
            ctime = os.path.getctime(path)
            if ctime > 0:
                return float(ctime)
        except OSError:
            pass

        return 0.0

    @staticmethod
    def _is_preferred_format(new_path: str, existing_path: str) -> bool:
        """Prefer the standard WebP asset when multiple formats share a stem."""
        new_ext = Path(new_path).suffix.lower()
        existing_ext = Path(existing_path).suffix.lower()
        return new_ext == ".webp" and existing_ext != ".webp"

    @staticmethod
    def _entry(path: str) -> dict[str, str]:
        path_obj = Path(path)
        filename = path_obj.name
        stem = path_obj.stem
        parent_name = path_obj.parent.name
        is_subfolder = parent_name.lower() not in ("references", "images", "artifacts", "output", ".")
        name = f"{parent_name}_{stem}" if is_subfolder else stem
        return {
            "name": name,
            "alias": re.sub(r"[^a-zA-Z0-9_-]", "_", name),
            "path": path,
            "title": extract_image_metadata_title(path) or "",
            "description": extract_image_metadata_description(path) or f"Image {filename}",
        }

    def browse_images(self) -> list[str]:
        """Browse generated outputs, excluding adventure cover art."""
        return [
            str(path) for path in sorted(Path(self.output_dir).rglob("*"))
            if path.is_file() and self._is_image(str(path))
        ]

    def search_images(self, query: str) -> list[str]:
        """Return image paths whose filename or metadata matches ``query``."""
        query = str(query or "").strip().lower()
        if not query:
            return []
        matches: list[str] = []
        for path in self.browse_images():
            entry = self._entry(path)
            searchable = (entry["name"], entry["alias"], entry["title"], entry["description"])
            if any(query in value.lower() for value in searchable):
                matches.append(path)
        return matches

    def find_image_names(self, query: str = "") -> list[dict[str, str]]:
        """Find generated output names and metadata."""
        paths = self.search_images(query) if str(query).strip() else self.browse_images()
        return [self._entry(path) for path in paths]

    def get_recent_images(
        self, limit: int = 5
    ) -> list[dict[str, str | float]]:
        """Return the most recently created images sorted descending by creation time.

        Args:
            limit: Maximum number of recent images to return (must be >= 1).

        Returns:
            A list of image entry dicts (with name, alias, path, title, description,
            and created_at timestamp), newest first.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        deduped: dict[tuple[str, str], str] = {}
        for path in self.browse_images():
            image = Path(path)
            key = (str(image.parent.resolve()), image.stem)
            if key not in deduped or self._is_preferred_format(path, deduped[key]):
                deduped[key] = path

        sorted_paths = sorted(
            deduped.values(),
            key=lambda p: self._get_creation_time(p),
            reverse=True,
        )

        results: list[dict[str, str | float]] = []
        for path in sorted_paths[:limit]:
            entry = dict(self._entry(path))
            entry["created_at"] = self._get_creation_time(path)
            results.append(entry)

        return results
