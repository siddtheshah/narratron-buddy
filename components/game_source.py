"""File discovery and safe YAML decoding for authored game packages."""
from __future__ import annotations

import json
from pathlib import Path
import re

from pydantic import JsonValue, TypeAdapter
import yaml
from yaml.events import AliasEvent, MappingStartEvent, SequenceStartEvent, MappingEndEvent, SequenceEndEvent
from yaml.nodes import MappingNode

GAME_FILENAMES = ("game.yaml", "game.yml", "game.json")
GAME_FILES = tuple(f"game/{name}" for name in GAME_FILENAMES)


class GameYamlLoader(yaml.SafeLoader):
    """Reject ambiguous mappings and keep dates and yes/no/on/off as strings."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str:
                raise ValueError("Game YAML mapping keys must be strings.")
            if key in result:
                raise ValueError(f"Duplicate game YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


# Copy the resolver table so other YAML configuration loaders are unaffected.
GameYamlLoader.yaml_implicit_resolvers = {
    character: [
        (tag, pattern) for tag, pattern in resolvers
        if tag not in {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp"}
    ]
    for character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
GameYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"),
)


def find_game_file(directory: Path) -> Path | None:
    paths = [directory / name for name in GAME_FILES if (directory / name).exists()]
    if len(paths) > 1:
        raise ValueError("Keep only one game declaration: game.yaml, game.yml, or game.json.")
    return paths[0] if paths else None


def game_source_json(content: bytes, filename: str) -> bytes:
    """Normalize YAML to JSON before applying the existing strict game contract."""
    if filename not in GAME_FILENAMES:
        raise ValueError("Unsupported game declaration filename.")
    if filename == "game.json":
        return content
    depth = 0
    for event in yaml.parse(content, Loader=GameYamlLoader):
        if type(event) is AliasEvent:
            raise ValueError("Game YAML aliases are unsupported; write each value explicitly.")
        if type(event) in (MappingStartEvent, SequenceStartEvent):
            depth += 1
            if depth > 64:
                raise ValueError("Game YAML exceeds its nesting limit.")
        elif type(event) in (MappingEndEvent, SequenceEndEvent):
            depth -= 1
    data = TypeAdapter(dict[str, JsonValue]).validate_python(
        yaml.load(content, Loader=GameYamlLoader), strict=True,
    )
    return json.dumps(data, allow_nan=False, ensure_ascii=False).encode("utf-8")
