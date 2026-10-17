"""Generic data, CEL and declarative-view contracts for theater games."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import re
from time import monotonic
from typing import Literal

from celpy import Environment, Runner, celtypes
from celpy.adapter import json_to_cel
from celpy.evaluation import Activation, Evaluator, Result
from jsonschema import Draft202012Validator
from lark import Token, Tree
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from components.canvas.game_values import MAX_VALUE_BYTES, checked_json

MAX_GAME_BYTES = 65_536
MAX_STEPS = 500_000
BOOL_ADAPTER = TypeAdapter(bool)
STRING_ADAPTER = TypeAdapter(str)
MAP_ADAPTER = TypeAdapter(dict[str, JsonValue])
LIST_ADAPTER = TypeAdapter(list[JsonValue])


class GameModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Binding(GameModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    expr: str = Field(min_length=1, max_length=4000)


class FunctionDefinition(GameModel):
    parameters: list[str] = Field(max_length=12)
    expr: str = Field(min_length=1, max_length=4000)


class TransitionDefinition(GameModel):
    parameters: dict[str, JsonValue]
    guard: str = Field(default="true", max_length=4000)
    bindings: list[Binding] = Field(default_factory=list, max_length=32)
    result: str = Field(min_length=1, max_length=4000)


class ViewAction(GameModel):
    name: str = Field(max_length=4000)
    arguments: str = Field(default="{}", max_length=4000)
    confirmation: str = Field(default="", max_length=200)


class ViewNode(GameModel):
    kind: Literal["column", "row", "grid", "text", "button", "svg", "rect", "circle", "svg_text"]
    props: dict[str, str] = Field(default_factory=dict, max_length=24)
    repeat: str | None = Field(default=None, max_length=4000)
    binding: str = Field(default="item", pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    children: list[ViewNode] = Field(default_factory=list, max_length=64)
    action: ViewAction | None = None


class GameDefinition(GameModel):
    version: Literal[2]
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


class RenderedNode(GameModel):
    kind: str
    props: dict[str, JsonValue]
    children: list[RenderedNode] = Field(default_factory=list)
    action: RenderedAction | None = None


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

    def __init__(self, definition: GameDefinition) -> None:
        self.definition = definition
        self.environment = Environment()
        self.programs: dict[str, Runner] = {}
        self.dependencies: dict[str, list[str]] = {}
        self.initial_view: list[JsonValue] = []
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
        for transition in definition.transitions.values():
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
                action = RenderedAction(name=name, arguments=MAP_ADAPTER.validate_python(self.evaluate(node.action.arguments, scope), strict=True), confirmation=node.action.confirmation)
            children = [child for template in node.children for child in self.render(template, scope)]
            rendered.append(RenderedNode(kind=node.kind, props=props, children=children, action=action))
        return rendered


def validate_game_package(content: bytes) -> GameProgram:
    if len(content) > MAX_GAME_BYTES:
        raise ValueError("Game definition exceeds size limit.")
    try:
        definition = GameDefinition.model_validate_json(content)
        program = GameProgram(definition)
        execution = program.execution()
        context = execution.context(definition.initial)
        execution.validate(definition.initial, context)
        program.initial_view = [node.model_dump(mode="json") for node in execution.render(definition.view, context)]
        if len(json.dumps(program.initial_view).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Rendered view exceeds its size budget.")
        return program
    except Exception as exc:
        raise ValueError(f"Invalid game package: {str(exc)[:250]}") from exc
