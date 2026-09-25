"""Read-only catalog and search support for a theater's image assets."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

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
