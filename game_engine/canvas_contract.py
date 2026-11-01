"""Versioned, host-independent canvas observations and CEL output validation.

Catalog lookup, authorization and durable application belong to the canvas host.
These models validate the wire contract before anything reaches those boundaries.
"""
from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Annotated, Literal

from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

MAX_CANVAS_BYTES = 131_072
MAX_EFFECT_BYTES = 16_384
MAX_EXACT_INTEGER = 9_007_199_254_740_991

Integer = Annotated[int, Field(ge=-MAX_EXACT_INTEGER, le=MAX_EXACT_INTEGER)]
Coordinate = Annotated[Integer, Field(ge=0, le=10_000)]
Size = Annotated[Integer, Field(ge=0)]
PositiveSize = Annotated[Integer, Field(gt=0)]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Rotation = Annotated[Integer, Field(ge=0, lt=360_000)]
Amount = Annotated[Integer, Field(ge=0, le=10_000)]
Operation = Literal["show_scene", "show_animation", "put_stamp", "remove_stamp", "set_stamp_tray", "set_overlay"]
Actor = Literal["player", "contributor", "narratron"]
EventKind = Literal[
    "stamp_placed", "stamp_moved", "stamp_removed", "drawing_completed",
    "annotation_changed", "annotation_removed", "region_activated", "key_pressed",
]


class CanvasModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CanvasAPI(CanvasModel):
    version: Annotated[int, Field(ge=1, le=1)]
    effects: list[Operation] = Field(max_length=6)

    @model_validator(mode="after")
    def unique_effects(self) -> Self:
        if len(set(self.effects)) != len(self.effects):
            raise ValueError("Canvas capabilities must be unique.")
        return self


class Point(CanvasModel):
    x: Coordinate
    y: Coordinate


class Bounds(CanvasModel):
    x: Integer
    y: Integer
    width: Size
    height: Size


class Rectangle(Bounds):
    width: PositiveSize
    height: PositiveSize


class Animation(CanvasModel):
    id: Identifier
    format: Literal["triframe", "layered", "video"]
    playback: Literal["once", "loop"]


class Page(CanvasModel):
    id: Identifier
    scene_id: Identifier | None
    asset_id: Identifier
    media_kind: Literal["image", "animation", "video"]
    animation: Animation | None
    width: PositiveSize
    height: PositiveSize

    @model_validator(mode="after")
    def consistent_media(self) -> Self:
        if self.media_kind == "image":
            if self.animation is not None:
                raise ValueError("Still pages cannot declare animation playback.")
        elif self.animation is None:
            raise ValueError("Animated pages require animation playback.")
        elif (self.media_kind == "video") != (self.animation.format == "video"):
            raise ValueError("Page media kind must match its animation format.")
        return self


class Placement(CanvasModel):
    id: Identifier
    asset_id: Identifier
    managed_by: Literal["user", "game"]
    game_key: Identifier | None
    draggable: bool
    center: Point
    bounds: Bounds
    rotation: Rotation

    @model_validator(mode="after")
    def consistent_owner(self) -> Self:
        if (self.managed_by == "game") != (self.game_key is not None):
            raise ValueError("Only game-managed stamps must have a game key.")
        return self


class Drawing(CanvasModel):
    id: Identifier
    points: list[Point] = Field(max_length=200)
    bounds: Bounds


class Annotation(CanvasModel):
    id: Identifier
    text: str = Field(max_length=2000)
    bounds: Bounds


class Region(CanvasModel):
    id: Identifier
    bounds: Rectangle

    @model_validator(mode="after")
    def within_image(self) -> Self:
        bounds = self.bounds
        if bounds.x < 0 or bounds.y < 0 or bounds.x + bounds.width > 10_000 or bounds.y + bounds.height > 10_000:
            raise ValueError("Interaction regions must stay inside the image.")
        return self


class OverlayItem(CanvasModel):
    id: Identifier
    bounds: Rectangle
    opacity: Amount


class SymbolItem(OverlayItem):
    kind: Literal["symbol"]
    symbol: Literal["circle", "ring", "cross", "arrow", "diamond", "star", "check"]
    color: Color
    rotation: Rotation


class TextItem(OverlayItem):
    kind: Literal["text"]
    text: str = Field(max_length=2000)
    color: Color
    font_size: PositiveSize
    align: Literal["left", "center", "right"]


class RegionItem(OverlayItem):
    kind: Literal["region"]
    region_id: Identifier
    color: Color


class StampItem(OverlayItem):
    kind: Literal["stamp"]
    asset_id: Identifier
    rotation: Rotation


Item = Annotated[SymbolItem | TextItem | RegionItem | StampItem, Field(discriminator="kind")]


class VisualFilter(CanvasModel):
    kind: Literal["dim", "grayscale", "sepia", "blur", "vignette"]
    amount: Amount


class TintFilter(CanvasModel):
    kind: Literal["tint"]
    amount: Amount
    color: Color


Filter = Annotated[VisualFilter | TintFilter, Field(discriminator="kind")]


class Overlay(CanvasModel):
    items: list[Item] = Field(max_length=128)
    filters: list[Filter] = Field(max_length=8)

    @model_validator(mode="after")
    def unique_entries(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Overlay item IDs must be unique.")
        if len({entry.kind for entry in self.filters}) != len(self.filters):
            raise ValueError("Overlay filter kinds must be unique.")
        return self

    def validate_regions(self, regions: dict[str, Region]) -> None:
        for item in self.items:
            if item.kind == "region":
                if item.region_id not in regions or item.bounds != regions[item.region_id].bounds:
                    raise ValueError("Overlay highlights must match an existing region's bounds.")


def canonical_key(key: str) -> str:
    if key in {"ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", " "}:
        return key
    if len(key) == 1 and key.isascii() and key.isalnum():
        return key.lower()
    raise ValueError("Unsupported game shortcut key.")


class CanvasEvent(CanvasModel):
    id: Identifier
    kind: EventKind
    actor: Actor
    page_id: Identifier | None
    canvas_revision: Size
    object_ids: list[Identifier] = Field(max_length=256)
    point: Point | None
    region_ids: list[Identifier] = Field(max_length=128)
    key: str | None
    binding_id: Identifier | None

    @model_validator(mode="after")
    def consistent_input(self) -> Self:
        if self.kind == "key_pressed":
            if self.key is None or self.binding_id is None or canonical_key(self.key) != self.key:
                raise ValueError("Keyboard events require a canonical key and binding ID.")
            if self.point is not None or self.object_ids or self.region_ids:
                raise ValueError("Keyboard events cannot contain pointer evidence.")
        else:
            if self.key is not None or self.binding_id is not None or self.page_id is None:
                raise ValueError("Canvas edits require a page and cannot contain keyboard evidence.")
        if len(set(self.object_ids)) != len(self.object_ids) or self.region_ids != sorted(set(self.region_ids)):
            raise ValueError("Event object IDs must be unique and region IDs must be sorted and unique.")
        return self


class CanvasSnapshot(CanvasModel):
    version: Annotated[int, Field(ge=1, le=1)]
    revision: Size
    page: Page | None
    stamps: dict[Identifier, Placement] = Field(max_length=256)
    drawings: dict[Identifier, Drawing] = Field(max_length=256)
    annotations: dict[Identifier, Annotation] = Field(max_length=128)
    regions: dict[Identifier, Region] = Field(max_length=128)
    tray: list[Identifier] = Field(max_length=256)
    overlay: Overlay
    event: CanvasEvent | None

    @classmethod
    def empty(cls, revision: int = 0) -> Self:
        return cls(
            version=1, revision=revision, page=None, stamps={}, drawings={},
            annotations={}, regions={}, tray=[], overlay=Overlay(items=[], filters=[]), event=None,
        )

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        for collection in (self.stamps, self.drawings, self.annotations, self.regions):
            if any(key != entry.id for key, entry in collection.items()):
                raise ValueError("Canvas collection keys must match object IDs.")
        keys = [stamp.game_key for stamp in self.stamps.values() if stamp.game_key is not None]
        if len(set(keys)) != len(keys):
            raise ValueError("Game stamp keys must be unique on a page.")
        if len(set(self.tray)) != len(self.tray):
            raise ValueError("Effective tray handles must be unique.")
        if self.page is None and (
            self.stamps or self.drawings or self.annotations or self.regions or self.overlay.items or self.overlay.filters
        ):
            raise ValueError("A canvas without a page cannot have page-scoped content.")
        self.overlay.validate_regions(self.regions)
        if self.event is not None:
            page_id = self.page.id if self.page is not None else None
            if self.event.page_id != page_id or self.event.canvas_revision != self.revision:
                raise ValueError("Canvas evidence must belong to the observed page and revision.")
            if any(region_id not in self.regions for region_id in self.event.region_ids):
                raise ValueError("Canvas evidence references an unknown region.")
        if len(self.model_dump_json().encode("utf-8")) > MAX_CANVAS_BYTES:
            raise ValueError("Canvas observation exceeds its 128 KiB size limit.")
        return self


class ExistingPage(CanvasModel):
    page_id: Identifier


class NewPage(CanvasModel):
    page_ref: Identifier


Target = ExistingPage | NewPage


class ShowScene(CanvasModel):
    op: Literal["show_scene"]
    scene_id: Identifier
    page_ref: Identifier


class ShowAnimation(CanvasModel):
    op: Literal["show_animation"]
    animation_id: Identifier
    playback: Literal["once", "loop"]
    page_ref: Identifier


class PutStamp(CanvasModel):
    op: Literal["put_stamp"]
    target: Target
    key: Identifier
    asset_id: Identifier
    center: Point
    width: PositiveSize
    rotation: Rotation
    draggable: bool


class RemoveStamp(CanvasModel):
    op: Literal["remove_stamp"]
    target: Target
    key: Identifier


class SetStampTray(CanvasModel):
    op: Literal["set_stamp_tray"]
    asset_ids: list[Identifier] = Field(max_length=256)


class SetOverlay(Overlay):
    op: Literal["set_overlay"]
    target: Target


Command = Annotated[
    ShowScene | ShowAnimation | PutStamp | RemoveStamp | SetStampTray | SetOverlay,
    Field(discriminator="op"),
]
COMMANDS = TypeAdapter(Annotated[list[Command], Field(max_length=64)])


def validate_effects(value: JsonValue, api: CanvasAPI, snapshot: CanvasSnapshot) -> list[Command]:
    """Validate a whole command batch without changing the observed canvas.

The host must additionally resolve assets, validate new-page regions and staged
observation limits, and check pins/ownership inside the shared commit protocol.
"""
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_EFFECT_BYTES:
        raise ValueError("Canvas output exceeds its 16 KiB size limit.")
    commands = COMMANDS.validate_python(value, strict=True)
    refs: set[str] = set()
    stamp_writes: set[tuple[str, str, str]] = set()
    overlays: set[tuple[str, str]] = set()
    tray_written = False
    for command in commands:
        if command.op not in api.effects:
            raise ValueError(f"Canvas operation {command.op} is not declared by this package.")
        if command.op in {"show_scene", "show_animation"}:
            if refs:
                raise ValueError("A transition can push at most one canvas page.")
            refs.add(command.page_ref)
        elif command.op == "set_stamp_tray":
            if tray_written:
                raise ValueError("A transition can set the stamp tray at most once.")
            tray_written = True
        else:
            target = target_identity(command.target, snapshot, refs)
            if command.op == "set_overlay":
                if target in overlays:
                    raise ValueError("A transition can set an overlay at most once per target page.")
                overlays.add(target)
                if target[0] == "page_id":
                    command.validate_regions(snapshot.regions)
            else:
                stamp_key = (*target, command.key)
                if stamp_key in stamp_writes:
                    raise ValueError("A transition can write a stamp key at most once per target page.")
                stamp_writes.add(stamp_key)
    return commands


def target_identity(target: Target, snapshot: CanvasSnapshot, refs: set[str]) -> tuple[str, str]:
    """Use the target model's declared fields, without probing arbitrary objects."""
    fields = target.model_dump()
    if "page_id" in fields:
        page_id = fields["page_id"]
        if snapshot.page is None or snapshot.page.id != page_id:
            raise ValueError("Canvas writes can target only the observed active history page.")
        return "page_id", page_id
    page_ref = fields["page_ref"]
    if page_ref not in refs:
        raise ValueError("Canvas page references must name an earlier page push in the same batch.")
    return "page_ref", page_ref


def commands_json(commands: Sequence[Command]) -> list[JsonValue]:
    return [command.model_dump(mode="json") for command in commands]
