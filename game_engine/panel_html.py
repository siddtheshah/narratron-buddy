"""Bounded, passive HTML assets for game panels (never authored scripts)."""
from __future__ import annotations

from base64 import b64encode
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
import xml.etree.ElementTree as ET

if TYPE_CHECKING:
    from game_engine.game_runtime import RenderedNode

MAX_PANEL_BYTES = 131_072
MAX_EXPANDED_PANEL_BYTES = 1_048_576
TAGS = frozenset("html head body title style div span p section article header footer main aside nav h1 h2 h3 h4 h5 h6 strong em b i small code pre br hr ul ol li dl dt dd table thead tbody tfoot tr th td caption button label details summary progress meter img".split())
ATTRIBUTES = frozenset("id class style title role aria-label aria-live aria-hidden colspan rowspan open min max value data-panel-text data-panel-action data-panel-hidden data-panel-disabled".split())


class PanelParser(HTMLParser):
    def __init__(self, svg_assets: dict[str, RenderedNode] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.in_style = False
        self.nodes = 0
        self.svg_assets = svg_assets
        self.asset_bytes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if tag not in TAGS or self.nodes > 2048:
            raise ValueError(f"Unsupported panel HTML element or element budget: {tag}")
        attributes: list[str] = []
        image_source = False
        for name, value in attrs:
            if tag == "img" and name == "src":
                from game_engine.game_runtime import normalize_game_svg_path

                if image_source:
                    raise ValueError("Panel images require exactly one SVG source.")
                image_source = True
                source = normalize_game_svg_path(value or "")
                if self.svg_assets is not None:
                    if source not in self.svg_assets:
                        raise ValueError(f"Unknown panel SVG asset: {source}")
                    svg = ET.tostring(_svg_element(self.svg_assets[source]), encoding="utf-8")
                    source = "data:image/svg+xml;base64," + b64encode(svg).decode("ascii")
                    self.asset_bytes += len(source)
                    if self.asset_bytes > MAX_EXPANDED_PANEL_BYTES:
                        raise ValueError("Expanded panel SVG images exceed their size budget.")
                attributes.append(f' src="{escape(source, quote=True)}"')
                continue
            if tag == "img" and name in {"alt", "width", "height"}:
                attributes.append(f' {name}="{escape(value or "", quote=True)}"')
                continue
            if name not in ATTRIBUTES:
                raise ValueError(f"Unsupported panel HTML attribute: {name}")
            attributes.append(f' {name}="{escape(value or "", quote=True)}"')
        if tag == "img" and not image_source:
            raise ValueError("Panel images require an SVG source.")
        self.parts.append(f"<{tag}{''.join(attributes)}>")
        self.in_style = tag == "style"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag != "img":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in TAGS:
            raise ValueError(f"Unsupported panel HTML element: {tag}")
        self.parts.append(f"</{tag}>")
        self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            if "<" in data:
                raise ValueError("Panel CSS cannot contain HTML markup.")
            self.parts.append(data)
        else:
            self.parts.append(escape(data))

    def handle_decl(self, decl: str) -> None:
        if decl.lower() != "doctype html":
            raise ValueError("Only the HTML doctype is supported in panels.")


def _svg_element(node: RenderedNode) -> ET.Element:
    element = ET.Element("text" if node.kind == "svg_text" else node.kind)
    if node.kind == "svg":
        element.set("xmlns", "http://www.w3.org/2000/svg")
    for name, value in node.props.items():
        if name == "text":
            element.text = str(value)
        else:
            element.set(name, str(value))
    for child in node.children:
        element.append(_svg_element(child))
    return element


def validate_panel_html(content: bytes, svg_assets: dict[str, RenderedNode] | None = None) -> str:
    if len(content) > MAX_PANEL_BYTES:
        raise ValueError("Panel HTML exceeds its size budget.")
    parser = PanelParser(svg_assets)
    parser.feed(content.decode("utf-8"))
    parser.close()
    result = "".join(parser.parts)
    if len(result.encode("utf-8")) > MAX_EXPANDED_PANEL_BYTES:
        raise ValueError("Expanded panel HTML exceeds its size budget.")
    return result


def load_panel_html(game_dir: Path, svg_assets: dict[str, RenderedNode] | None = None) -> str:
    from game_engine.game_runtime import load_game_svg_assets

    path = game_dir / "panel.html"
    if not path.resolve().is_relative_to(game_dir.resolve()):
        raise ValueError("Panel HTML must stay inside the game directory.")
    with path.open("rb") as stream:
        return validate_panel_html(
            stream.read(MAX_PANEL_BYTES + 1),
            load_game_svg_assets(game_dir) if svg_assets is None else svg_assets,
        )
