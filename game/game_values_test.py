import pytest
from celpy import celtypes

from game.game_values import checked_json


def test_json_conversion_preserves_boolean_and_native_string_results() -> None:
    value = celtypes.MapType({celtypes.StringType("enabled"): celtypes.BoolType(True), celtypes.StringType("label"): "native string result"})
    assert checked_json(value) == {"enabled": True, "label": "native string result"}


def test_repeated_large_values_are_rejected_before_full_json_expansion() -> None:
    shared = celtypes.StringType("x" * 8192)
    value = celtypes.ListType([shared] * 1000)
    with pytest.raises(ValueError, match="size budget"):
        checked_json(value)


def test_nested_values_have_a_depth_limit() -> None:
    value = celtypes.ListType([])
    for _ in range(66):
        value = celtypes.ListType([value])
    with pytest.raises(ValueError, match="nesting budget"):
        checked_json(value)


def test_non_json_numbers_and_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        checked_json(celtypes.DoubleType(float("inf")))
    with pytest.raises(ValueError):
        checked_json(celtypes.MapType({celtypes.IntType(1): celtypes.IntType(2)}))
