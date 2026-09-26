"""Read-only catalog and search support for a theater's image assets."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from components.theater_manager import Theater
from utils.image_utils import extract_image_metadata_description, extract_image_metadata_title


logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


class ImageLibrary:
    """Discover mounted and generated images without modifying the canvas.

    The library deliberately returns stable ``name`` and ``alias`` fields so
    planners can persist an image identifier in a sticky note and the live
    agent can later pass that identifier to ``show_image``.
    """

    def __init__(self, theater: Theater) -> None:
        self.theater = theater
        self.reference_dir = str(theater.references_dir())
        self.output_dir = str(theater.image_artifacts_dir())
        self.references_manifest: dict[str, dict[str, str]] = {}
        self._load_references()

    @staticmethod
    def _is_image(path: str) -> bool:
        return Path(path).suffix.lower() in IMAGE_EXTENSIONS

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
        """Prefer png/jpg/jpeg over webp for catalog representation when stems match."""
        new_ext = Path(new_path).suffix.lower()
        existing_ext = Path(existing_path).suffix.lower()
        if existing_ext == ".webp" and new_ext in (".png", ".jpg", ".jpeg"):
            return True
        return False

    @staticmethod
    def _entry(path: str) -> dict[str, str]:
        filename = Path(path).name
        name = Path(path).stem
        return {
            "name": name,
            "alias": re.sub(r"[^a-zA-Z0-9_-]", "_", name),
            "path": path,
            "title": extract_image_metadata_title(path) or "",
            "description": extract_image_metadata_description(path) or f"Image {filename}",
        }

    def _load_references(self) -> None:
        """Refresh the manifest of mounted reference images."""
        self.references_manifest.clear()
        try:
            for root, _, files in os.walk(self.reference_dir):
                for filename in files:
                    path = os.path.join(root, filename)
                    if self._is_image(path):
                        self.references_manifest[path] = self._entry(path)
        except OSError as exc:
            logger.warning("[ImageLibrary] Failed to load references: %s", exc)

    def list_references(self) -> list[dict[str, str]]:
        """List mounted reference images with names, aliases, and metadata."""
        return list(self.references_manifest.values())

    def browse_images(self) -> list[str]:
        """Browse file paths for every mounted or generated image."""
        images: list[str] = []
        seen: set[str] = set()
        for directory in (self.reference_dir, self.output_dir):
            if not os.path.isdir(directory):
                continue
            for root, _, files in os.walk(directory):
                for filename in files:
                    path = os.path.join(root, filename)
                    if self._is_image(path) and path not in seen:
                        seen.add(path)
                        images.append(path)
        return images

    def search_images(self, query: str) -> list[str]:
        """Return image paths whose filename or metadata matches ``query``."""
        query = str(query or "").strip().lower()
        if not query:
            return []
        matches: list[str] = []
        for path in self.browse_images():
            entry = self.references_manifest.get(path) or self._entry(path)
            searchable = (entry["name"], entry["alias"], entry["title"], entry["description"])
            if any(query in value.lower() for value in searchable):
                matches.append(path)
        return matches

    def find_image_names(self, query: str = "") -> list[dict[str, str]]:
        """Find image names for planner sticky notes.

        Use the returned ``alias`` (or ``name``) in a sticky note; either can
        later be supplied to the live agent's ``show_image`` tool.
        """
        paths = self.search_images(query) if str(query).strip() else self.browse_images()
        return [self.references_manifest.get(path) or self._entry(path) for path in paths]

    def get_recent_images(
        self, limit: int = 5, generated_only: bool = True
    ) -> list[dict[str, Any]]:
        """Return the most recently created images sorted descending by creation time.

        Args:
            limit: Maximum number of recent images to return (must be >= 1).
            generated_only: If True, only search the output directory (default: True).

        Returns:
            A list of image entry dicts (with name, alias, path, title, description,
            and created_at timestamp), newest first.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        search_dirs = [self.output_dir] if generated_only else [self.output_dir, self.reference_dir]
        deduped: dict[tuple[str, str], str] = {}

        for directory in search_dirs:
            if not os.path.isdir(directory):
                continue
            for root, _, files in os.walk(directory):
                for filename in files:
                    path = os.path.join(root, filename)
                    if not self._is_image(path):
                        continue
                    stem = Path(path).stem
                    parent = str(Path(path).parent.resolve())
                    key = (parent, stem)
                    if key not in deduped or self._is_preferred_format(path, deduped[key]):
                        deduped[key] = path

        sorted_paths = sorted(
            deduped.values(),
            key=lambda p: self._get_creation_time(p),
            reverse=True,
        )

        results: list[dict[str, Any]] = []
        for path in sorted_paths[:limit]:
            entry = dict(self.references_manifest.get(path) or self._entry(path))
            entry["created_at"] = self._get_creation_time(path)
            results.append(entry)

        return results

