"""Tests for ``ralph.checked_accessors`` — the production boundary accessors."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.checked_accessors import (
    as_bool,
    as_int,
    as_mapping,
    as_object_list,
    as_path,
    as_sequence,
    as_str,
    as_str_dict,
    as_str_list,
    optional_bool,
    optional_int,
    optional_str,
    optional_str_list,
)

# ---------------------------------------------------------------------------
# Raising variants
# ---------------------------------------------------------------------------


class TestAsStr:
    def test_returns_string_unchanged(self) -> None:
        assert as_str("hello") == "hello"

    def test_field_message_uses_field_name(self) -> None:
        with pytest.raises(TypeError, match="my_field"):
            as_str(123, field="my_field")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError, match="expected str"):
            as_str(123)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="expected non-empty str"):
            as_str("")

    def test_rejects_none(self) -> None:
        with pytest.raises(TypeError):
            as_str(None)


class TestAsInt:
    def test_returns_int(self) -> None:
        assert as_int(7) == 7

    def test_rejects_bool(self) -> None:
        # bool is a subclass of int but must be rejected
        with pytest.raises(TypeError, match="expected int"):
            as_int(True)

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            as_int("7")

    def test_rejects_none(self) -> None:
        with pytest.raises(TypeError):
            as_int(None)


class TestAsBool:
    def test_true(self) -> None:
        assert as_bool(True) is True

    def test_false(self) -> None:
        assert as_bool(False) is False

    def test_rejects_int(self) -> None:
        # 0 and 1 are int, not bool
        with pytest.raises(TypeError):
            as_bool(1)

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            as_bool("true")


class TestAsPath:
    def test_accepts_path(self) -> None:
        p = Path("/tmp/x")
        assert as_path(p) is p

    def test_accepts_str_and_converts(self) -> None:
        assert as_path("/tmp/x") == Path("/tmp/x")

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            as_path(42)


class TestAsStrDict:
    def test_returns_str_dict(self) -> None:
        result = as_str_dict({"a": "x", "b": "y"})
        assert result == {"a": "x", "b": "y"}

    def test_rejects_non_dict(self) -> None:
        with pytest.raises(TypeError):
            as_str_dict("not a dict")

    def test_rejects_dict_with_non_str_value(self) -> None:
        with pytest.raises(TypeError, match="dict values must be str"):
            as_str_dict({"a": 1})

    def test_accepts_empty_dict(self) -> None:
        assert as_str_dict({}) == {}


class TestAsMapping:
    def test_returns_mapping(self) -> None:
        data = {"a": 1, "b": "two"}
        result = as_mapping(data)
        assert result["a"] == 1
        assert result["b"] == "two"

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(TypeError):
            as_mapping([1, 2])

    def test_rejects_non_str_keys(self) -> None:
        with pytest.raises(TypeError, match="mapping keys must be str"):
            as_mapping({1: "x"})


class TestAsObjectList:
    def test_returns_list(self) -> None:
        data = [1, "a", None]
        result = as_object_list(data)
        assert result == [1, "a", None]

    def test_rejects_tuple(self) -> None:
        with pytest.raises(TypeError):
            as_object_list((1, 2))

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            as_object_list("abc")


class TestAsStrList:
    def test_returns_list_of_strings(self) -> None:
        assert as_str_list(["a", "b"]) == ["a", "b"]

    def test_rejects_non_list(self) -> None:
        with pytest.raises(TypeError):
            as_str_list(("a", "b"))

    def test_rejects_list_with_non_string(self) -> None:
        with pytest.raises(TypeError, match="list items must be str"):
            as_str_list(["a", 1])

    def test_accepts_empty_list(self) -> None:
        assert as_str_list([]) == []


class TestAsSequence:
    def test_accepts_list(self) -> None:
        assert list(as_sequence([1, 2, 3])) == [1, 2, 3]

    def test_accepts_tuple(self) -> None:
        assert tuple(as_sequence((1, 2, 3))) == (1, 2, 3)

    def test_rejects_string(self) -> None:
        # strings ARE sequences but must be rejected
        with pytest.raises(TypeError):
            as_sequence("abc")

    def test_rejects_bytes(self) -> None:
        with pytest.raises(TypeError):
            as_sequence(b"abc")


# ---------------------------------------------------------------------------
# Absent-returning variants
# ---------------------------------------------------------------------------


class TestOptionalStr:
    def test_returns_string(self) -> None:
        assert optional_str("hi") == "hi"

    def test_returns_none_for_empty(self) -> None:
        assert optional_str("") is None

    def test_returns_none_for_non_string(self) -> None:
        assert optional_str(7) is None

    def test_returns_none_for_none(self) -> None:
        assert optional_str(None) is None


class TestOptionalInt:
    def test_returns_int(self) -> None:
        assert optional_int(7) == 7

    def test_returns_none_for_bool(self) -> None:
        assert optional_int(True) is None

    def test_returns_none_for_string(self) -> None:
        assert optional_int("7") is None

    def test_returns_none_for_none(self) -> None:
        assert optional_int(None) is None


class TestOptionalBool:
    def test_returns_true(self) -> None:
        assert optional_bool(True) is True

    def test_returns_false(self) -> None:
        assert optional_bool(False) is False

    def test_returns_none_for_int(self) -> None:
        assert optional_bool(1) is None

    def test_returns_none_for_string(self) -> None:
        assert optional_bool("true") is None


class TestOptionalStrList:
    def test_returns_list(self) -> None:
        assert optional_str_list(["a", "b"]) == ["a", "b"]

    def test_returns_none_for_tuple(self) -> None:
        assert optional_str_list(("a", "b")) is None

    def test_returns_none_for_mixed_list(self) -> None:
        assert optional_str_list(["a", 1]) is None

    def test_returns_none_for_none(self) -> None:
        assert optional_str_list(None) is None

    def test_accepts_empty_list(self) -> None:
        assert optional_str_list([]) == []


# ---------------------------------------------------------------------------
# Cross-cutting smoke
# ---------------------------------------------------------------------------


def test_all_helpers_are_pure() -> None:
    """Calling helpers does not mutate their inputs."""
    data = {"k": "v"}
    as_mapping(data)
    assert data == {"k": "v"}

    seq = [1, 2, 3]
    as_object_list(seq)
    assert seq == [1, 2, 3]


def test_helpers_compose_for_realistic_payload() -> None:
    """A realistic deserialized payload can be walked without any cast."""
    payload: object = {
        "name": "agent-1",
        "max_retries": 3,
        "tags": ["alpha", "beta"],
        "metadata": {"source": "json"},
    }
    as_mapping(payload, field="root")
    mapping = as_mapping(payload, field="root")
    assert as_str(mapping["name"], field="name") == "agent-1"
    assert as_int(mapping["max_retries"], field="max_retries") == 3
    assert as_str_list(mapping["tags"], field="tags") == ["alpha", "beta"]
    assert as_str_dict(mapping["metadata"], field="metadata") == {"source": "json"}
