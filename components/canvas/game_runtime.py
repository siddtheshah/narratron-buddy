"""Generic data, CEL and declarative-view contracts for theater games."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from time import monotonic
from typing import Literal
import xml.etree.ElementTree as ET

from celpy import Environment, Runner, celtypes
from celpy.adapter import json_to_cel
from celpy.evaluation import Activation, Evaluator, Result
from jsonschema import Draft202012Validator
from lark import Token, Tree
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from components.canvas.game_values import MAX_VALUE_BYTES, checked_json

MAX_GAME_BYTES = 131_072
MAX_STEPS = 500_000
MAX_MODEL_STATE_BYTES = 16_384
MAX_SVG_NODES = 512
MAX_SVG_DEPTH = 12
BOOL_ADAPTER = TypeAdapter(bool)
STRING_ADAPTER = TypeAdapter(str)
MAP_ADAPTER = TypeAdapter(dict[str, JsonValue])
LIST_ADAPTER = TypeAdapter(list[JsonValue])

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ALLOWED_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "defs", "linearGradient", "radialGradient",
    "stop", "clipPath", "mask", "symbol",
}
IGNORED_SVG_TAGS = {"title", "desc", "metadata"}
ALLOWED_SVG_ATTRIBUTES = {
    "id", "viewBox", "preserveAspectRatio", "x", "y", "width", "height",
    "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2", "d", "points",
    "dx", "dy", "rotate", "pathLength", "transform", "transform-origin",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
    "stroke-dasharray", "stroke-dashoffset", "opacity", "color", "stop-color",
    "stop-opacity", "offset", "gradientUnits", "gradientTransform",
    "spreadMethod", "fx", "fy", "fr", "clipPathUnits", "maskUnits",
    "maskContentUnits", "clip-rule", "text-anchor", "dominant-baseline",
    "font-size", "fontSize", "font-weight", "fontWeight", "font-family",
    "font-style", "letter-spacing", "vector-effect", "paint-order",
    "shape-rendering", "display", "visibility",
}
UNSAFE_SVG_VALUE_RE = re.compile(r"url\s*\(|expression\s*\(|javascript\s*:|data\s*:|[<>{}\\]", re.IGNORECASE)


class GameModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Binding(GameModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    expr: str = Field(min_length=1, max_length=4000)


class FunctionDefinition(GameModel):
    parameters: list[str] = Field(max_length=12)
    expr: str = Field(min_length=1, max_length=4000)


class TransitionDefinition(GameModel):
    actor: Literal["player", "contributor", "narratron"] = "player"
    permission: Literal["orator", "contributor"] = "orator"
    description: str = Field(default="", max_length=500)
    available: str = Field(default="true", max_length=4000)
    parameters: dict[str, JsonValue]
    guard: str = Field(default="true", max_length=4000)
    bindings: list[Binding] = Field(default_factory=list, max_length=32)
    result: str = Field(min_length=1, max_length=4000)
    triggering: bool = Field(default=True)

    @property
    def is_player_actor(self) -> bool:
        return self.actor in ("player", "contributor")

    @property
    def allows_contributor(self) -> bool:
        return self.actor == "contributor" or self.permission == "contributor"


class ViewAction(GameModel):
    name: str = Field(max_length=4000)
    arguments: str = Field(default="{}", max_length=4000)
    confirmation: str = Field(default="", max_length=200)
    permission: Literal["orator", "contributor"] | None = None


class ViewNode(GameModel):
    kind: Literal[
        "column", "row", "grid", "text", "button",
        "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "svg_text", "image",
    ]
    props: dict[str, str] = Field(default_factory=dict, max_length=24)
    repeat: str | None = Field(default=None, max_length=4000)
    binding: str = Field(default="item", pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    children: list[ViewNode] = Field(default_factory=list, max_length=64)
    action: ViewAction | None = None


class GameDefinition(GameModel):
    version: Literal[2]
    instructions: str = Field(default="", max_length=4000)
    model_state: str | None = Field(default=None, min_length=1, max_length=4000)
    initial: dict[str, JsonValue]
    state_schema: dict[str, JsonValue]
    constants: dict[str, JsonValue] = Field(default_factory=dict)
    functions: dict[str, FunctionDefinition] = Field(default_factory=dict, max_length=24)
    derived: list[Binding] = Field(default_factory=list, max_length=24)
    invariants: list[str] = Field(default_factory=list, max_length=16)
    transitions: dict[str, TransitionDefinition] = Field(min_length=1, max_length=32)
    view: ViewNode


class GameAction(GameModel):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = Field(ge=0)


class RenderedAction(GameModel):
    name: str
    arguments: dict[str, JsonValue]
    confirmation: str = ""
    permission: Literal["orator", "contributor"] = "orator"


class RenderedNode(GameModel):
    kind: str
    props: dict[str, JsonValue]
    children: list[RenderedNode] = Field(default_factory=list)
    action: RenderedAction | None = None


def normalize_game_svg_path(raw_path: str) -> str:
    """Normalize a relative SVG path under game/ and reject traversal or invalid suffixes."""
    cleaned = raw_path.strip()
    if not cleaned or "\\" in cleaned or cleaned.startswith("/"):
        raise ValueError(f"Invalid game SVG path: {raw_path!r}")
    parts = [part for part in cleaned.split("/") if part]
    if parts and parts[0] == "game":
        parts = parts[1:]
    if not parts or any(part in {".", ".."} or re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in parts):
        raise ValueError(f"Invalid game SVG path: {raw_path!r}")
    normalized = "/".join(parts)
    if PurePosixPath(normalized).suffix.lower() != ".svg":
        raise ValueError(f"Game graphic must be an .svg file: {raw_path!r}")
    return normalized


def _validate_svg_attr(name: str, value: str) -> None:
    if name not in ALLOWED_SVG_ATTRIBUTES:
        raise ValueError(f"Unsupported SVG attribute: {name}")
    max_len = 10_000 if name in {"d", "points"} else 200
    if len(value) > max_len or UNSAFE_SVG_VALUE_RE.search(value):
        raise ValueError(f"Invalid SVG attribute value for {name}.")


def _strip_xml_namespace(tag_or_attr: str) -> tuple[str, str]:
    if tag_or_attr.startswith("{"):
        uri, _, local = tag_or_attr[1:].partition("}")
        return uri, local
    return "", tag_or_attr


def _parse_svg_element(element: ET.Element, counter: list[int], depth: int) -> RenderedNode | None:
    if depth > MAX_SVG_DEPTH:
        raise ValueError("SVG element nesting exceeds its budget.")
    uri, tag = _strip_xml_namespace(element.tag)
    if uri not in ("", SVG_NAMESPACE):
        raise ValueError("Unsupported XML namespace in SVG.")
    if tag in IGNORED_SVG_TAGS:
        return None
    if tag not in ALLOWED_SVG_TAGS:
        raise ValueError(f"Unsupported SVG tag: {tag}")
    counter[0] += 1
    if counter[0] > MAX_SVG_NODES:
        raise ValueError("SVG asset exceeds its element budget.")
    props: dict[str, JsonValue] = {}
    for raw_attr, raw_val in element.attrib.items():
        attr_uri, attr_name = _strip_xml_namespace(raw_attr)
        if attr_uri or attr_name == "xmlns" or attr_name.startswith(("xmlns:", "xml:")):
            continue
        if attr_name.lower().startswith("on"):
            raise ValueError(f"Event handler attributes are not permitted in SVG: {attr_name}")
        if attr_name == "style":
            for decl in raw_val.split(";"):
                decl_clean = decl.strip()
                if not decl_clean:
                    continue
                prop_name, sep, prop_val = decl_clean.partition(":")
                if not sep:
                    raise ValueError("Malformed style attribute in SVG.")
                key = prop_name.strip()
                val = prop_val.strip()
                _validate_svg_attr(key, val)
                if key not in props:
                    props[key] = val
            continue
        _validate_svg_attr(attr_name, raw_val)
        props[attr_name] = raw_val
    if tag in {"text", "tspan"} and element.text and element.text.strip():
        text_content = element.text.strip()
        if len(text_content) > 500:
            raise ValueError("SVG text content exceeds size budget.")
        props["text"] = text_content
    children: list[RenderedNode] = []
    for child_elem in element:
        parsed_child = _parse_svg_element(child_elem, counter, depth + 1)
        if parsed_child is not None:
            children.append(parsed_child)
    node_kind = "svg_text" if tag == "text" else tag
    return RenderedNode(kind=node_kind, props=props, children=children)


def validate_game_svg(content: bytes) -> RenderedNode:
    """Parse and validate a static SVG asset from the adventure's game/ directory."""
    if len(content) > MAX_GAME_BYTES:
        raise ValueError("Game SVG exceeds size limit.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Game SVG must be UTF-8 encoded.") from exc
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("SVG DTDs and entities are not permitted.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed SVG XML: {str(exc)[:180]}") from exc
    uri, root_tag = _strip_xml_namespace(root.tag)
    if uri not in ("", SVG_NAMESPACE) or root_tag != "svg":
        raise ValueError("Root element of game graphic must be <svg>.")
    parsed = _parse_svg_element(root, [0], 0)
    if parsed is None:
        raise ValueError("Empty SVG document.")
    if "viewBox" not in parsed.props:
        width = parsed.props.get("width")
        height = parsed.props.get("height")
        if type(width) is str and type(height) is str:
            w_match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:px)?\s*", width)
            h_match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:px)?\s*", height)
            if w_match and h_match:
                parsed.props["viewBox"] = f"0 0 {w_match.group(1)} {h_match.group(1)}"
    return parsed


def load_game_svg_assets(game_dir: Path | None) -> dict[str, RenderedNode]:
    """Load and validate all .svg files located under game_dir."""
    assets: dict[str, RenderedNode] = {}
    if game_dir is None or not game_dir.is_dir():
        return assets
    resolved_dir = game_dir.resolve()
    for path in sorted(game_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".svg":
            continue
        if not path.resolve().is_relative_to(resolved_dir) or path.stat().st_size > MAX_GAME_BYTES:
            raise ValueError(f"Game SVG path or size is invalid: {path.name}")
        rel = normalize_game_svg_path(path.relative_to(game_dir).as_posix())
        assets[rel] = validate_game_svg(path.read_bytes())
    return assets


@dataclass
class Budget:
    remaining: int = MAX_STEPS
    deadline: float = 0
    nodes: int = 0

    def __post_init__(self) -> None:
        self.deadline = monotonic() + 5.0

    def charge(self) -> None:
        self.remaining -= 1
        if self.remaining < 0 or (self.remaining % 256 == 0 and monotonic() > self.deadline):
            raise ValueError("Game evaluation exceeded its work budget.")


class BoundedEvaluator(Evaluator):
    def __init__(self, ast: Tree[Token], activation: Activation, budget: Budget) -> None:
        super().__init__(ast, activation)
        self.budget = budget

    def _visit_tree(self, tree: Tree[Token]) -> Result:
        self.budget.charge()
        return super()._visit_tree(tree)

    def sub_evaluator(self, ast: Tree[Token]) -> BoundedEvaluator:
        return BoundedEvaluator(ast, self.activation, self.budget)


def validate_schema(schema: dict[str, JsonValue]) -> Draft202012Validator:
    # External references could cause network access; local recursive references
    # could defeat the bounded data model. Neither is supported in this version.
    Draft202012Validator.check_schema(schema)
    check_schema_features(schema)
    return Draft202012Validator(schema)


def check_schema_features(schema: JsonValue) -> None:
    """Inspect schema keywords, not application property names or enum values."""
    try:
        node = MAP_ADAPTER.validate_python(schema, strict=True)
    except ValidationError:
        BOOL_ADAPTER.validate_python(schema, strict=True)
        return
    if any(key in node for key in ("$ref", "$dynamicRef")):
        raise ValueError("Game schemas cannot contain references.")
    if any(key in node for key in ("pattern", "patternProperties")):
        raise ValueError("Game schemas cannot contain regular expressions.")
    for keyword in ("properties", "$defs", "definitions", "dependentSchemas"):
        for child in MAP_ADAPTER.validate_python(node.get(keyword, {}), strict=True).values():
            check_schema_features(child)
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in LIST_ADAPTER.validate_python(node.get(keyword, []), strict=True):
            check_schema_features(child)
    for keyword in ("items", "contains", "not", "if", "then", "else", "additionalProperties", "unevaluatedProperties", "unevaluatedItems", "propertyNames"):
        if keyword in node:
            check_schema_features(node[keyword])


class GameProgram:
    """Compile a package once. No game names, field names or move types live here."""

    def __init__(self, definition: GameDefinition, svg_assets: dict[str, RenderedNode] | None = None) -> None:
        self.definition = definition
        self.svg_assets = svg_assets
        self.environment = Environment()
        self.programs: dict[str, Runner] = {}
        self.dependencies: dict[str, list[str]] = {}
        self.initial_view: list[JsonValue] = []
        self.initial_model_state: JsonValue = definition.initial
        self.initial_narratron_actions: dict[str, JsonValue] = {}
        self.state_validator = validate_schema(definition.state_schema)
        self.action_validators = {name: validate_schema(transition.parameters) for name, transition in definition.transitions.items()}
        self.state_validator.validate(definition.initial)
        allowed_functions = {"size", "string", "int", "bool"}
        for name, function in definition.functions.items():
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", name) or name in allowed_functions:
                raise ValueError("Invalid or duplicate function name.")
            if len(set(function.parameters)) != len(function.parameters) or any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", param) for param in function.parameters):
                raise ValueError("Invalid function parameters.")
            self.compile(function.expr, allowed_functions)
            allowed_functions.add(name)
        for binding in definition.derived:
            self.compile(binding.expr, allowed_functions)
        for expression in definition.invariants:
            self.compile(expression, allowed_functions)
        if definition.model_state is not None:
            self.compile(definition.model_state, allowed_functions)
        for transition in definition.transitions.values():
            if transition.actor == "narratron" and transition.permission != "orator":
                raise ValueError("Narratron transitions cannot declare contributor permission.")
            self.compile(transition.available, allowed_functions)
            if "action" in self.dependencies[transition.available]:
                raise ValueError("Action availability cannot depend on action arguments.")
            self.compile(transition.guard, allowed_functions)
            self.compile(transition.result, allowed_functions)
            for binding in transition.bindings:
                self.compile(binding.expr, allowed_functions)
        self.compile_view(definition.view, allowed_functions, 0)

    def compile(self, source: str, functions: set[str]) -> None:
        if len(source) > 4000:
            raise ValueError("CEL source exceeds its size budget.")
        tree = self.environment.compile(source)
        nodes = list(tree.iter_subtrees())
        if len(nodes) > 1000:
            raise ValueError("CEL expression exceeds its AST budget.")
        for node in nodes:
            if node.data in {"member_object", "dot_ident", "dot_ident_arg"}:
                raise ValueError("CEL object construction and root lookup are not supported.")
            if node.data == "ident_arg" and str(node.children[0]) not in functions:
                raise ValueError(f"Unsupported CEL function: {node.children[0]}")
            if node.data == "member_dot_arg" and str(node.children[1]) not in {"map", "filter", "exists", "all", "size"}:
                raise ValueError(f"Unsupported CEL method: {node.children[1]}")
        self.programs[source] = self.environment.program(tree)
        self.dependencies[source] = sorted({str(node.children[0]) for node in nodes if node.data == "ident"})

    def compile_view(self, node: ViewNode, functions: set[str], depth: int) -> None:
        if depth > 12:
            raise ValueError("View nesting exceeds its budget.")
        for source in node.props.values():
            self.compile(source, functions)
        if node.repeat is not None:
            self.compile(node.repeat, functions)
        if node.action is not None:
            self.compile(node.action.name, functions)
            self.compile(node.action.arguments, functions)
        for child in node.children:
            self.compile_view(child, functions, depth + 1)

    def execution(self) -> Execution:
        return Execution(self)


class Execution:
    def __init__(self, program: GameProgram) -> None:
        self.program = program
        self.budget = Budget()
        self.svg_expanded_bytes: int = 0
        self._values: dict[int, tuple[JsonValue, celtypes.Value]] = {}
        self._results: dict[tuple[str, tuple[tuple[str, int], ...]], JsonValue] = {}
        self.functions: dict[str, Callable[..., celtypes.Value]] = {}
        for name, definition in program.definition.functions.items():
            self.functions[name] = self.function(definition)

    def function(self, definition: FunctionDefinition) -> Callable[..., celtypes.Value]:
        def invoke(*arguments: celtypes.Value) -> celtypes.Value:
            if len(arguments) != len(definition.parameters):
                raise ValueError("Incorrect function argument count.")
            context = {name: checked_json(value) for name, value in zip(definition.parameters, arguments)}
            context["constants"] = self.program.definition.constants
            result = self.evaluate(definition.expr, context)
            return json_to_cel(result)
        return invoke

    def evaluate(self, source: str, context: dict[str, JsonValue]) -> JsonValue:
        dependencies = self.program.dependencies[source]
        key = (source, tuple((name, id(context[name])) for name in dependencies if name in context))
        if key in self._results:
            return self._results[key]
        runner = self.program.programs[source]
        activation = Activation(vars={key: self.convert(value) for key, value in context.items()}, functions=self.functions)
        try:
            result = BoundedEvaluator(runner.ast, activation, self.budget).evaluate()
            # A CEL boolean branch may suppress a nested evaluation error. Work
            # exhaustion must still reject the whole evaluation.
            self.budget.charge()
            value = checked_json(result)
            self._results[key] = value
            return value
        except Exception as exc:
            raise ValueError(f"CEL evaluation failed: {str(exc)[:180]}") from exc

    def convert(self, value: JsonValue) -> celtypes.Value:
        key = id(value)
        if key not in self._values:
            self._values[key] = (value, json_to_cel(value))
        return self._values[key][1]

    def bindings(self, bindings: list[Binding], context: dict[str, JsonValue]) -> None:
        for binding in bindings:
            if binding.name in context:
                raise ValueError(f"Binding shadows an existing name: {binding.name}")
            context[binding.name] = self.evaluate(binding.expr, context)

    def context(self, state: dict[str, JsonValue]) -> dict[str, JsonValue]:
        context: dict[str, JsonValue] = {"state": state, "constants": self.program.definition.constants, "initial": self.program.definition.initial}
        self.bindings(self.program.definition.derived, context)
        return context

    def validate(self, state: dict[str, JsonValue], context: dict[str, JsonValue]) -> None:
        self.program.state_validator.validate(state)
        for expression in self.program.definition.invariants:
            if not BOOL_ADAPTER.validate_python(self.evaluate(expression, context), strict=True):
                raise ValueError("Game state invariant failed.")

    def model_context(self, context: dict[str, JsonValue]) -> tuple[JsonValue, dict[str, JsonValue]]:
        """Render package-selected model facts and currently available Narratron actions."""
        definition = self.program.definition
        model_state = (
            self.evaluate(definition.model_state, context)
            if definition.model_state is not None else context["state"]
        )
        if definition.model_state is not None and len(json.dumps(model_state, ensure_ascii=False).encode("utf-8")) > MAX_MODEL_STATE_BYTES:
            raise ValueError("Model-facing game state exceeds its size budget.")
        actions: dict[str, JsonValue] = {}
        for name, transition in definition.transitions.items():
            if transition.actor != "narratron":
                continue
            if BOOL_ADAPTER.validate_python(self.evaluate(transition.available, context), strict=True):
                actions[name] = {"description": transition.description, "parameters": transition.parameters}
        if len(json.dumps(actions, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Model-facing game actions exceed their size budget.")
        return model_state, actions

    def render(self, node: ViewNode, context: dict[str, JsonValue]) -> list[RenderedNode]:
        contexts = [context]
        if node.repeat is not None:
            values = LIST_ADAPTER.validate_python(self.evaluate(node.repeat, context), strict=True)
            if len(values) > 256 or node.binding in context:
                raise ValueError("Invalid view repetition.")
            contexts = [{**context, node.binding: value} for value in values]
        rendered: list[RenderedNode] = []
        for scope in contexts:
            self.budget.nodes += 1
            if self.budget.nodes > 1024:
                raise ValueError("Rendered view exceeds its node budget.")
            props = {name: self.evaluate(expr, scope) for name, expr in node.props.items()}
            action = None
            if node.action is not None:
                name = STRING_ADAPTER.validate_python(self.evaluate(node.action.name, scope), strict=True)
                if name not in self.program.definition.transitions:
                    raise ValueError("View references an unknown transition.")
                transition = self.program.definition.transitions[name]
                if not transition.is_player_actor:
                    raise ValueError("View cannot expose a Narratron-only transition.")
                effective_permission: Literal["orator", "contributor"] = (
                    node.action.permission
                    if node.action.permission is not None
                    else ("contributor" if transition.allows_contributor else "orator")
                )
                action = RenderedAction(
                    name=name,
                    arguments=MAP_ADAPTER.validate_python(self.evaluate(node.action.arguments, scope), strict=True),
                    confirmation=node.action.confirmation,
                    permission=effective_permission,
                )
            children = [child for template in node.children for child in self.render(template, scope)]
            node_kind = "svg" if node.kind == "image" else node.kind
            if node.kind in ("svg", "image"):
                src_key = next((key for key in ("src", "href", "asset") if key in props), None)
                if src_key is not None:
                    raw_src = props.pop(src_key)
                    if raw_src is not None and raw_src != "":
                        src_str = STRING_ADAPTER.validate_python(raw_src, strict=True)
                        normalized_src = normalize_game_svg_path(src_str)
                        props["src"] = normalized_src
                        if self.program.svg_assets is not None:
                            if normalized_src not in self.program.svg_assets:
                                raise ValueError(f"Unknown game SVG asset: {normalized_src}")
                            asset = self.program.svg_assets[normalized_src]
                            props = {**asset.props, **props}
                            children = [*asset.children, *children]
                            self.svg_expanded_bytes += len(
                                json.dumps([child.model_dump(mode="json") for child in asset.children]).encode("utf-8")
                            )
            rendered.append(RenderedNode(kind=node_kind, props=props, children=children, action=action))
        return rendered


def validate_game_package(
    content: bytes,
    *,
    game_dir: Path | None = None,
    svg_assets: dict[str, RenderedNode] | None = None,
) -> GameProgram:
    if len(content) > MAX_GAME_BYTES:
        raise ValueError("Game definition exceeds size limit.")
    try:
        resolved_svgs = svg_assets if svg_assets is not None else (load_game_svg_assets(game_dir) if game_dir is not None else None)
        definition = GameDefinition.model_validate_json(content)
        program = GameProgram(definition, svg_assets=resolved_svgs)
        execution = program.execution()
        context = execution.context(definition.initial)
        execution.validate(definition.initial, context)
        program.initial_view = [node.model_dump(mode="json") for node in execution.render(definition.view, context)]
        program.initial_model_state, program.initial_narratron_actions = execution.model_context(context)
        if len(json.dumps(program.initial_view).encode("utf-8")) - execution.svg_expanded_bytes > MAX_VALUE_BYTES:
            raise ValueError("Rendered view exceeds its size budget.")
        return program
    except Exception as exc:
        raise ValueError(f"Invalid game package: {str(exc)[:250]}") from exc
