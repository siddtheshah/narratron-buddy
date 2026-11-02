"""Bounded, passive HTML assets for game panels (never authored scripts)."""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from pathlib import Path

MAX_PANEL_BYTES = 131_072
TAGS = frozenset("html head body title style div span p section article header footer main aside nav h1 h2 h3 h4 h5 h6 strong em b i small code pre br hr ul ol li dl dt dd table thead tbody tfoot tr th td caption button label details summary progress meter".split())
ATTRIBUTES = frozenset("id class style title role aria-label aria-live aria-hidden colspan rowspan open min max value data-panel-text data-panel-action data-panel-hidden data-panel-disabled".split())


class PanelParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.in_style = False
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if tag not in TAGS or self.nodes > 2048:
            raise ValueError(f"Unsupported panel HTML element or element budget: {tag}")
        attributes: list[str] = []
        for name, value in attrs:
            if name not in ATTRIBUTES:
                raise ValueError(f"Unsupported panel HTML attribute: {name}")
            attributes.append(f' {name}="{escape(value or "", quote=True)}"')
        self.parts.append(f"<{tag}{''.join(attributes)}>")
        self.in_style = tag == "style"

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


def validate_panel_html(content: bytes) -> str:
    if len(content) > MAX_PANEL_BYTES:
        raise ValueError("Panel HTML exceeds its size budget.")
    parser = PanelParser()
    parser.feed(content.decode("utf-8"))
    parser.close()
    return "".join(parser.parts)


def load_panel_html(game_dir: Path) -> str:
    path = game_dir / "panel.html"
    if not path.resolve().is_relative_to(game_dir.resolve()):
        raise ValueError("Panel HTML must stay inside the game directory.")
    with path.open("rb") as stream:
        return validate_panel_html(stream.read(MAX_PANEL_BYTES + 1))
