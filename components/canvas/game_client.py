"""Export a bounded expression tree for optional browser-side game previews."""
from __future__ import annotations

import json

from lark import Token, Tree
from pydantic import JsonValue

from components.canvas.game_runtime import GameProgram, ViewNode
from components.canvas.game_values import checked_json


def _browser_integer(source: str) -> int:
    value = int(source)
    if abs(value) > 9_007_199_254_740_991:
        raise ValueError("Integer exceeds browser precision.")
    return value


def _unsupported_float(source: str) -> float:
    raise ValueError("Floating-point values use the server runtime.")


def browser_compatible(value: JsonValue) -> bool:
    """Keep values outside the browser's integer subset on the server path."""
    try:
        json.loads(json.dumps(value, allow_nan=False), parse_int=_browser_integer, parse_float=_unsupported_float)
        return True
    except ValueError:
        return False


def _expression(program: GameProgram, node: Tree[Token] | Token) -> JsonValue:
    if type(node) is Token:
        return str(node)
    kind = str(node.data)
    children = node.children
    if kind == "ident_arg" and str(children[0]) == "random_int":
        raise ValueError("Random calls use the server runtime.")
    if kind == "literal":
        # Preserve CEL's integer division; unsupported numeric kinds use server rendering.
        if children[0].type not in {"INT_LIT", "STRING_LIT", "BOOL_LIT", "NULL_LIT"}:
            raise ValueError("This literal is not supported by browser previews.")
        value = checked_json(program.environment.program(node).evaluate({}))
        if not browser_compatible(value):
            raise ValueError("Literal exceeds browser precision.")
        return ["literal", value]
    if kind in {"expr", "conditionalor", "conditionaland", "relation", "addition", "multiplication", "unary", "member", "primary", "paren_expr"} and len(children) == 1:
        return _expression(program, children[0])
    supported = {
        "expr", "conditionalor", "conditionaland", "relation", "addition", "multiplication", "unary",
        "relation_eq", "relation_ne", "relation_lt", "relation_le", "relation_gt", "relation_ge", "relation_in",
        "addition_add", "addition_sub", "multiplication_mul", "multiplication_div", "multiplication_mod",
        "unary_not", "unary_neg", "ident", "member_dot", "member_index", "member_dot_arg",
        "ident_arg", "exprlist", "list_lit", "map_lit", "mapinits",
    }
    if kind not in supported:
        raise ValueError(f"Unsupported browser expression: {kind}")
    return [kind, *[_expression(program, child) for child in children]]


def browser_program(program: GameProgram) -> dict[str, JsonValue] | None:
    """Export player rules only; server execution remains authoritative."""
    if not program.definition.local_preview:
        return None
    if not browser_compatible([program.definition.initial, program.definition.constants]):
        return None
    sources: set[str] = set()

    def collect_view(node: ViewNode) -> None:
        sources.update(node.props.values())
        if node.repeat is not None:
            sources.add(node.repeat)
        if node.action is not None:
            sources.update((node.action.name, node.action.arguments))
        for child in node.children:
            collect_view(child)

    definition = program.definition
    collect_view(definition.view)
    sources.update(function.expr for function in definition.functions.values())
    sources.update(binding.expr for binding in definition.derived)
    sources.update(definition.invariants)
    transitions: dict[str, JsonValue] = {}
    for name, transition in definition.transitions.items():
        if not transition.is_player_actor:
            continue
        sources.update((transition.guard, transition.result))
        sources.update(binding.expr for binding in transition.bindings)
        client_transition = transition.model_dump(mode="json")
        client_transition.pop("on_error")
        transitions[name] = client_transition
    try:
        expressions: dict[str, JsonValue] = {source: _expression(program, program.programs[source].ast) for source in sorted(sources)}
    except ValueError:
        return None
    return {
        "expressions": expressions,
        "initial": definition.initial,
        "constants": definition.constants,
        "functions": {name: function.model_dump(mode="json") for name, function in definition.functions.items()},
        "derived": [binding.model_dump(mode="json") for binding in definition.derived],
        "invariants": definition.invariants,
        "transitions": transitions,
        "view": definition.view.model_dump(mode="json"),
        "assets": {name: node.model_dump(mode="json") for name, node in (program.svg_assets or {}).items()},
    }
