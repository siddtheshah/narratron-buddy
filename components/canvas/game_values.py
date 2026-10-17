"""Bounded CEL-to-JSON conversion without expanding oversized intermediate data."""
from dataclasses import dataclass
from functools import singledispatch
import json
import math

from celpy import celtypes
from pydantic import JsonValue, TypeAdapter

MAX_VALUE_BYTES = 131_072
STRING = TypeAdapter(str)


@dataclass
class ValueBudget:
    remaining: int = MAX_VALUE_BYTES

    def charge(self, amount: int) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise ValueError("Game value exceeds its size budget.")


def convert(value: celtypes.Value, budget: ValueBudget, depth: int = 0) -> JsonValue:
    if depth > 64:
        raise ValueError("Game value exceeds its nesting budget.")
    budget.charge(1)
    return decode(value, budget, depth)


@singledispatch
def decode(value: celtypes.Value, budget: ValueBudget, depth: int) -> JsonValue:
    raise ValueError("Game expressions must return JSON-compatible values.")


@decode.register(celtypes.BoolType)
@decode.register(bool)
def decode_bool(value: celtypes.BoolType | bool, budget: ValueBudget, depth: int) -> bool:
    budget.charge(5)
    return bool(value)


@decode.register(celtypes.IntType)
@decode.register(celtypes.UintType)
@decode.register(int)
def decode_integer(value: int, budget: ValueBudget, depth: int) -> int:
    budget.charge(len(str(value)))
    return int(value)


@decode.register(celtypes.DoubleType)
@decode.register(float)
def decode_float(value: float, budget: ValueBudget, depth: int) -> float:
    if not math.isfinite(value):
        raise ValueError("Game numbers must be finite.")
    budget.charge(len(str(value)))
    return float(value)


@decode.register(celtypes.StringType)
@decode.register(str)
def decode_string(value: str, budget: ValueBudget, depth: int) -> str:
    # Check length before encoding; large strings must not be copied first.
    budget.charge(len(value) + 2)
    encoded_length = len(json.dumps(str(value)).encode("utf-8"))
    budget.charge(encoded_length - len(value) - 2)
    return str(value)


@decode.register(type(None))
def decode_null(value: None, budget: ValueBudget, depth: int) -> None:
    budget.charge(4)
    return None


@decode.register(celtypes.ListType)
def decode_list(value: celtypes.ListType, budget: ValueBudget, depth: int) -> list[JsonValue]:
    budget.charge(len(value) + 2)
    return [convert(item, budget, depth + 1) for item in value]


@decode.register(celtypes.MapType)
def decode_map(value: celtypes.MapType, budget: ValueBudget, depth: int) -> dict[str, JsonValue]:
    budget.charge(len(value) * 2 + 2)
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        name = STRING.validate_python(convert(key, budget, depth + 1), strict=True)
        result[name] = convert(item, budget, depth + 1)
    return result


def checked_json(value: celtypes.Value) -> JsonValue:
    return convert(value, ValueBudget())
