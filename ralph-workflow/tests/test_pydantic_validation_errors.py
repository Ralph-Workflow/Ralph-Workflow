from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ralph.pydantic_validation_errors import (
    format_validation_error_detail,
    format_validation_error_messages,
    format_validation_location,
    format_validation_message,
    suggest_canonical_field,
)


class SampleClosedEnum(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "migration",
        "security",
        "performance",
        "cleanup",
        "research",
        "unknown",
        "file_change",
        "prompt",
        "other",
    ]


class SampleStringLength(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., max_length=10, min_length=3)


class SampleListLength(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str] = Field(..., min_length=2, max_length=4)


class SampleInt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(..., ge=1, le=10)


def _details(exc: ValidationError) -> list[dict[str, object]]:
    return [dict(detail) for detail in exc.errors()]


def test_string_too_long_includes_max_and_actual_length() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleStringLength.model_validate({"intent": "x" * 15})
    msgs = format_validation_error_messages(exc_info.value)
    assert len(msgs) == 1
    msg = msgs[0]
    assert "intent" in msg
    assert "10" in msg
    assert "15" in msg


def test_string_type_error_passes_through_raw_message() -> None:

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        intent: str = Field(..., max_length=5)

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"intent": 12345})
    msgs = format_validation_error_messages(exc_info.value)
    assert len(msgs) == 1
    assert "intent" in msgs[0]
    assert "valid string" in msgs[0]


def test_string_too_short_reports_minimum_length() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleStringLength.model_validate({"intent": "a"})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("minimum length is 3" in m for m in msgs)


def test_missing_required_field_uses_required_phrase() -> None:

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        x: str

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("required and must be provided" in m for m in msgs)


def test_literal_error_lists_valid_values_and_rejected_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleClosedEnum.model_validate({"category": "ship_it"})
    msgs = format_validation_error_messages(exc_info.value)
    assert len(msgs) == 1
    msg = msgs[0]
    assert "category" in msg
    assert "bugfix" in msg
    assert "ship_it" in msg


def test_too_short_list_includes_actual_and_minimum() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleListLength.model_validate({"items": ["only"]})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("at least 2" in m for m in msgs)
    assert any("got 1" in m for m in msgs)


def test_too_long_list_includes_actual_and_maximum() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleListLength.model_validate({"items": ["a", "b", "c", "d", "e"]})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("at most 4" in m for m in msgs)
    assert any("got 5" in m for m in msgs)


def test_extra_forbidden_names_the_unknown_key() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleClosedEnum.model_validate({"category": "bugfix", "extra_field": True})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("extra_field" in m for m in msgs)


def test_int_parsing_error_includes_repr_of_value() -> None:

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        x: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"x": "abc"})
    msgs = format_validation_error_messages(exc_info.value)
    assert any("'abc'" in m for m in msgs)


def test_int_from_float_error_includes_repr_of_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        SampleInt.model_validate({"count": 3.14})
    msgs = format_validation_error_messages(exc_info.value)
    assert msgs, msgs
    assert any("3.14" in m for m in msgs)


def test_format_validation_location_handles_tuples() -> None:
    assert format_validation_location(("summary", "intent")) == "summary.intent"
    assert format_validation_location(("steps", 0, "step_type")) == "steps.0.step_type"


def test_format_validation_location_handles_lists_and_strings() -> None:
    assert format_validation_location(["summary", "intent"]) == "summary.intent"
    assert format_validation_location("top") == "top"


def test_format_validation_location_sentinel_for_empty_inputs() -> None:
    assert format_validation_location(None) == "<root>"
    assert format_validation_location([]) == "<root>"
    assert format_validation_location(()) == "<root>"


def test_format_validation_message_substitutes_placeholder_for_none() -> None:
    assert format_validation_message(None) == "<missing message>"
    assert format_validation_message("ok") == "ok"
    assert format_validation_message(42) == "42"


def test_format_validation_error_detail_uses_indent_and_colon() -> None:
    detail = {"loc": ("a", "b"), "msg": "boom", "type": "some_unknown_type"}
    assert format_validation_error_detail(detail) == "  a.b: boom"


def test_format_validation_error_detail_enhances_known_error_types() -> None:
    detail = {"loc": ("a", "b"), "msg": "Field required", "type": "missing"}
    rendered = format_validation_error_detail(detail)
    assert rendered == "  a.b: Field required; field is required and must be provided"


def test_suggest_canonical_field_returns_closest_match() -> None:
    suggestion = suggest_canonical_field("design_constraints", ["constraints", "non_goals"])
    assert suggestion == "constraints"


def test_suggest_canonical_field_returns_none_when_no_candidate() -> None:
    suggestion = suggest_canonical_field("totally_unrelated", ["alpha", "beta"])
    assert suggestion is None


def test_suggest_canonical_field_returns_none_for_empty_candidate_set() -> None:
    assert suggest_canonical_field("anything", []) is None


def test_format_validation_error_messages_preserves_multiple_errors() -> None:

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        a: int
        b: int

    with pytest.raises(ValidationError) as exc_info:
        M.model_validate({"a": "no", "b": "no"})
    msgs = format_validation_error_messages(exc_info.value)
    assert len(msgs) == 2


def test_format_validation_error_detail_ignores_unknown_error_type() -> None:
    detail: dict[str, object] = {
        "type": "some_new_error_kind",
        "loc": ("x",),
        "msg": "weird",
    }
    rendered = format_validation_error_detail(detail)
    assert "weird" in rendered
