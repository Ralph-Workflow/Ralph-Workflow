"""Tests for ``tests._support.typed_accessors`` — the test-side runtime validators."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests._support.typed_accessors import (
    must_bool,
    must_dict_list,
    must_int,
    must_mapping,
    must_object_list,
    must_str,
    must_str_dict,
    must_str_list,
    must_text,
)

# ---------------------------------------------------------------------------
# Single-value accessors
# ---------------------------------------------------------------------------


class TestMustStr:
    def test_returns_string(self) -> None:
        assert must_str("hi") == "hi"

    def test_accepts_empty_string(self) -> None:
        assert must_str("") == ""

    def test_rejects_non_string(self) -> None:
        with pytest.raises(TypeError, match="expected str"):
            must_str(123)

    def test_field_message_includes_field(self) -> None:
        with pytest.raises(TypeError, match="my_field"):
            must_str(123, field="my_field")


class TestMustInt:
    def test_returns_int(self) -> None:
        assert must_int(7) == 7

    def test_rejects_bool(self) -> None:
        # bool is a subclass of int but tests must distinguish
        with pytest.raises(TypeError, match="expected int"):
            must_int(True)

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            must_int("7")


class TestMustBool:
    def test_true(self) -> None:
        assert must_bool(True) is True

    def test_false(self) -> None:
        assert must_bool(False) is False

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            must_bool(1)


# ---------------------------------------------------------------------------
# Container accessors
# ---------------------------------------------------------------------------


class TestMustMapping:
    def test_returns_mapping(self) -> None:
        data = {"a": 1, "b": "two"}
        assert must_mapping(data)["a"] == 1

    def test_rejects_list(self) -> None:
        with pytest.raises(TypeError):
            must_mapping([1, 2])

    def test_rejects_non_str_keys(self) -> None:
        with pytest.raises(TypeError, match="mapping keys must be str"):
            must_mapping({1: "x"})


class TestMustStrDict:
    def test_returns_str_dict(self) -> None:
        assert must_str_dict({"a": "x"}) == {"a": "x"}

    def test_rejects_non_dict(self) -> None:
        with pytest.raises(TypeError):
            must_str_dict([("a", "x")])

    def test_rejects_dict_with_non_str_value(self) -> None:
        with pytest.raises(TypeError, match="dict values must be str"):
            must_str_dict({"a": 1})


class TestMustObjectList:
    def test_returns_list(self) -> None:
        data = [1, "a", None]
        assert must_object_list(data) == [1, "a", None]

    def test_rejects_tuple(self) -> None:
        with pytest.raises(TypeError):
            must_object_list((1, 2))


class TestMustDictList:
    def test_returns_list_of_mappings(self) -> None:
        data = [{"a": 1}, {"b": 2}]
        result = must_dict_list(data)
        assert len(result) == 2

    def test_rejects_non_list(self) -> None:
        with pytest.raises(TypeError):
            must_dict_list(({"a": 1},))

    def test_rejects_list_with_non_mapping(self) -> None:
        with pytest.raises(TypeError, match="index 1"):
            must_dict_list([{"a": 1}, "not a mapping"])


class TestMustStrList:
    def test_returns_list_of_strings(self) -> None:
        assert must_str_list(["a", "b"]) == ["a", "b"]

    def test_rejects_mixed_list(self) -> None:
        with pytest.raises(TypeError, match="list items must be str"):
            must_str_list(["a", 1])


# ---------------------------------------------------------------------------
# MCP / external-payload helper
# ---------------------------------------------------------------------------


class TestMustText:
    def test_returns_text_attribute(self) -> None:
        block = SimpleNamespace(text="hello world")
        assert must_text(block) == "hello world"

    def test_rejects_object_without_text(self) -> None:
        with pytest.raises(TypeError, match="expected MCP TextContent"):
            must_text(SimpleNamespace(blob=b"x"))

    def test_rejects_non_string_text(self) -> None:
        block = SimpleNamespace(text=123)
        with pytest.raises(TypeError):
            must_text(block)

    def test_field_message_includes_field(self) -> None:
        with pytest.raises(TypeError, match="payload"):
            must_text(SimpleNamespace(blob=b"x"), field="payload")


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_helpers_compose_for_realistic_payload() -> None:
    """A realistic deserialized fixture can be walked without any cast."""
    fixture: object = {
        "name": "agent-1",
        "max_retries": 3,
        "tags": ["alpha", "beta"],
        "metadata": {"source": "json"},
    }
    payload = must_mapping(fixture, field="root")
    assert must_str(payload["name"], field="name") == "agent-1"
    assert must_int(payload["max_retries"], field="max_retries") == 3
    assert must_str_list(payload["tags"], field="tags") == ["alpha", "beta"]
    assert must_str_dict(payload["metadata"], field="metadata") == {"source": "json"}
