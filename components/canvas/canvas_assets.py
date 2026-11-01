"""Resolve theater canvas handles to content-addressed local media revisions."""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import io
from pathlib import Path

from PIL import Image
from pydantic import Field, JsonValue, TypeAdapter
import yaml

from game_engine.canvas_contract import CanvasModel, Identifier, PositiveSize, Region
from utils.atomic_file import atomic_write_bytes

JSON_MAP = TypeAdapter(dict[str, JsonValue])
REGIONS = TypeAdapter(dict[Identifier, Region])
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class CatalogImage(CanvasModel):
    asset_id: Identifier
    revision: Identifier
    width: PositiveSize
    height: PositiveSize
    path: str
    url: str
    scene_id: Identifier | None = None
    regions: dict[Identifier, Region] = Field(default_factory=dict, max_length=128)


class CanvasAssetCatalog:
    def __init__(self, directory: Path, url_for_path: Callable[[str], str]) -> None:
        self.directory = directory.resolve()
        self.url_for_path = url_for_path
        self.cache = (self.directory / "artifacts" / "canvas_assets").resolve()
        if not self.cache.is_relative_to(self.directory):
            raise ValueError("Pinned canvas media cannot escape the theater directory.")

    def _image(self, path: Path, *, asset_id: str = "", scene_id: str | None = None) -> CatalogImage:
        path = path.resolve()
        if not path.is_relative_to(self.directory) or path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("Canvas media must be an image inside the selected theater.")
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image.verify()
        digest = sha256(data).hexdigest()
        self.cache.mkdir(parents=True, exist_ok=True)
        pinned = self.cache / f"{digest}{path.suffix.lower()}"
        # Every file name identifies its bytes. Replacement of the authored image
        # cannot change the art selected by an already prepared action or page.
        if not pinned.exists():
            atomic_write_bytes(pinned, data)
        elif sha256(pinned.read_bytes()).hexdigest() != digest:
            raise ValueError("Pinned canvas asset content does not match its revision.")
        regions = self._regions(path, digest) if scene_id is not None else {}
        return CatalogImage(
            asset_id=asset_id or f"image:{digest}", revision=f"sha256:{digest}",
            width=width, height=height, path=str(pinned), url=self.url_for_path(str(pinned)),
            scene_id=scene_id, regions=regions,
        )

    def observe_image(self, path: str) -> CatalogImage:
        image = Path(path).resolve()
        scene_id: str | None = None
        for root in (self.directory / "artifacts" / "updated_references" / "scenes", self.directory / "references" / "scenes"):
            if image.is_relative_to(root.resolve()):
                relative = image.relative_to(root.resolve())
                scene_id = relative.parts[0] if len(relative.parts) > 1 else image.stem
                break
        return self._image(image, scene_id=scene_id)

    def scene(self, scene_id: str) -> CatalogImage:
        if not scene_id or "/" in scene_id or "\\" in scene_id or scene_id in {".", ".."}:
            raise ValueError("Scene IDs are authored names, not paths.")
        for root in (self.directory / "artifacts" / "updated_references" / "scenes", self.directory / "references" / "scenes"):
            folder = root / scene_id
            candidates = []
            if folder.is_dir() and folder.resolve().is_relative_to(root.resolve()):
                candidates = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
            if not candidates and root.is_dir():
                candidates = [path for path in root.iterdir() if path.is_file() and path.stem == scene_id and path.suffix.lower() in IMAGE_SUFFIXES]
            if candidates:
                def revision_order(path: Path) -> tuple[int, int, str]:
                    return (int(path.stem) if path.stem.isdigit() else 0, path.stat().st_mtime_ns, path.name)

                return self._image(max(candidates, key=revision_order), scene_id=scene_id)
        raise ValueError(f"Unknown canvas scene: {scene_id}")

    def stamp(self, asset_id: str) -> CatalogImage:
        if not asset_id.startswith("stamp:"):
            raise ValueError("Game stamp writes require a theater stamp handle.")
        name = asset_id.removeprefix("stamp:")
        root = self.directory / "stamps"
        matches = [path for path in root.rglob("*") if path.is_file() and path.stem == name and path.suffix.lower() in IMAGE_SUFFIXES] if root.is_dir() else []
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous canvas stamp: {asset_id}")
        return self._image(matches[0], asset_id=asset_id)

    def _regions(self, image: Path, digest: str) -> dict[str, Region]:
        declaration = image.parent / "scene.yaml"
        if not declaration.is_file():
            return {}
        if not declaration.resolve().is_relative_to(self.directory):
            raise ValueError("Scene region metadata cannot escape the theater directory.")
        data = JSON_MAP.validate_python(yaml.safe_load(declaration.read_text(encoding="utf-8")), strict=True)
        canvas = JSON_MAP.validate_python(data.get("canvas", {}), strict=True)
        artwork = JSON_MAP.validate_python(canvas.get("artwork", {}), strict=True)
        authored = artwork.get(f"sha256:{digest}")
        if authored is None:
            return {}
        regions = REGIONS.validate_python(authored, strict=True)
        if len(regions) > 128 or any(key != region.id for key, region in regions.items()):
            raise ValueError("Scene region keys must match IDs and fit the region limit.")
        return regions

    def resolve_revision(self, asset_id: str) -> CatalogImage:
        """Read a previously pinned page revision, without selecting today's art."""
        if not asset_id.startswith("image:"):
            raise ValueError("Unknown pinned image handle.")
        digest = asset_id.removeprefix("image:")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("Invalid pinned image handle.")
        matches = list(self.cache.glob(f"{digest}.*"))
        if not matches:
            raise ValueError("Pinned canvas image is missing.")
        resolved = self._image(matches[0])
        if resolved.asset_id != asset_id:
            raise ValueError("Pinned canvas image no longer matches the recorded revision.")
        return resolved
