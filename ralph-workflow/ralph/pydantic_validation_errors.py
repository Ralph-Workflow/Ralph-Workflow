from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pydantic import ValidationError

ValidationErrorDetail = Mapping[str, object]
ValidationErrorDetails = Sequence[ValidationErrorDetail]

__all__ = [
    "ValidationErrorDetail",
    "ValidationErrorDetails",
    "format_validation_error_detail",
    "format_validation_error_messages",
    "format_validation_location",
    "format_validation_message",
    "suggest_canonical_field",
]


def format_validation_error_messages(exc: ValidationError) -> list[str]:
    details = cast("ValidationErrorDetails", exc.errors())
    return [format_validation_error_detail(detail) for detail in details]


def format_validation_error_detail(detail: ValidationErrorDetail) -> str:
    loc = detail.get("loc")
    msg = _format_message_with_value(detail)
    return f"  {format_validation_location(loc)}: {msg}"


def format_validation_location(raw_loc: object | None) -> str:
    if raw_loc is None:
        return "<root>"
    if isinstance(raw_loc, (list, tuple)):
        if not raw_loc:
            return "<root>"
        return ".".join(str(component) for component in raw_loc)
    return str(raw_loc)


def format_validation_message(raw_msg: object | None) -> str:
    if isinstance(raw_msg, str):
        return raw_msg
    if raw_msg is None:
        return "<missing message>"
    return str(raw_msg)


def suggest_canonical_field(
    unknown_key: str,
    candidate_fields: Sequence[str],
    *,
    cutoff: float = 0.6,
) -> str | None:
    if not candidate_fields:
        return None
    matches = difflib.get_close_matches(unknown_key, list(candidate_fields), n=1, cutoff=cutoff)
    if not matches:
        return None
    return matches[0]


_INT_ERROR_TYPES: frozenset[str] = frozenset({"int_parsing", "int_type", "int_from_float"})


def _format_int_entry(base: str, detail: ValidationErrorDetail, err_type: str) -> str:
    return _format_int(base, detail.get("input"))


_MESSAGE_FORMATTERS: dict[str, Callable[[str, ValidationErrorDetail, str], str]] = {
    "string_too_long": lambda base, detail, err_type: _format_string_too_long(
        base, _as_mapping(detail.get("ctx")), detail.get("input")
    ),
    "string_too_short": lambda base, detail, err_type: _format_string_too_short(
        base, _as_mapping(detail.get("ctx")), err_type
    ),
    "missing": lambda base, detail, err_type: _format_string_too_short(
        base, _as_mapping(detail.get("ctx")), err_type
    ),
    "literal_error": lambda base, detail, err_type: _format_literal_error(
        base, _as_mapping(detail.get("ctx")), detail.get("input")
    ),
    "enum": lambda base, detail, err_type: _format_enum(
        base, _as_mapping(detail.get("ctx")), detail.get("input")
    ),
    "enum_member": lambda base, detail, err_type: _format_enum(
        base, _as_mapping(detail.get("ctx")), detail.get("input")
    ),
    "too_short": lambda base, detail, err_type: _format_too_short_or_long(
        base, _as_mapping(detail.get("ctx")), detail.get("input"), err_type
    ),
    "too_long": lambda base, detail, err_type: _format_too_short_or_long(
        base, _as_mapping(detail.get("ctx")), detail.get("input"), err_type
    ),
    "extra_forbidden": lambda base, detail, err_type: _format_extra_forbidden(
        base, detail.get("input"), _as_mapping(detail.get("ctx")), detail.get("loc")
    ),
    "missing_keyword_only_argument": lambda base, detail, err_type: _format_missing_field(
        base, detail.get("input"), _as_mapping(detail.get("ctx"))
    ),
    **dict.fromkeys(_INT_ERROR_TYPES, _format_int_entry),
}


def _format_message_with_value(detail: ValidationErrorDetail) -> str:
    err_type_obj = detail.get("type")
    base = format_validation_message(detail.get("msg"))
    if not isinstance(err_type_obj, str):
        return base
    formatter = _MESSAGE_FORMATTERS.get(err_type_obj)
    if formatter is None:
        return base
    return formatter(base, detail, err_type_obj)


def _format_string_too_long(base: str, ctx: Mapping[str, object], input_value: object) -> str:
    max_length_obj = ctx.get("max_length")
    max_length = max_length_obj if isinstance(max_length_obj, int) else None
    actual_length = _string_length(input_value)
    parts: list[str] = [base]
    if max_length is not None and actual_length is not None:
        parts.append(f"rejected value has {actual_length} characters, max is {max_length}")
    elif max_length is not None:
        parts.append(f"max is {max_length} characters")
    return "; ".join(parts)


def _format_string_too_short(base: str, ctx: Mapping[str, object], err_type: str) -> str:
    if err_type == "missing":
        return f"{base}; field is required and must be provided"
    min_length_obj = ctx.get("min_length")
    min_length = min_length_obj if isinstance(min_length_obj, int) else None
    if min_length is not None:
        return f"{base}; minimum length is {min_length}"
    return base


def _format_literal_error(base: str, ctx: Mapping[str, object], input_value: object) -> str:
    expected_obj = ctx.get("expected")
    expected = expected_obj if isinstance(expected_obj, str) else None
    parts: list[str] = [base]
    if expected is not None:
        parts.append(f"valid values: {expected}")
    if input_value is not None:
        parts.append(f"got: {input_value!r}")
    return "; ".join(parts)


def _format_enum(base: str, ctx: Mapping[str, object], input_value: object) -> str:
    expected_obj = ctx.get("expected")
    expected = expected_obj if isinstance(expected_obj, str) else None
    parts: list[str] = [base]
    if expected is not None:
        parts.append(f"valid values: {expected}")
    if input_value is not None:
        parts.append(f"got: {input_value!r}")
    return "; ".join(parts)


def _format_too_short_or_long(
    base: str, ctx: Mapping[str, object], input_value: object, err_type: str
) -> str:
    min_length_obj = ctx.get("min_length")
    min_length = min_length_obj if isinstance(min_length_obj, int) else None
    max_length_obj = ctx.get("max_length")
    max_length = max_length_obj if isinstance(max_length_obj, int) else None
    actual_obj = ctx.get("actual_length")
    actual = actual_obj if isinstance(actual_obj, int) else None
    field_type_obj = ctx.get("field_type")
    field_type = field_type_obj if isinstance(field_type_obj, str) else None
    parts: list[str] = [base]
    if err_type == "too_short" and min_length is not None:
        if field_type is not None:
            parts.append(f"{field_type} must have at least {min_length} item(s)")
        else:
            parts.append(f"minimum length is {min_length}")
        if actual is not None:
            parts.append(f"got {actual}")
    elif err_type == "too_long" and max_length is not None:
        if field_type is not None:
            parts.append(f"{field_type} must have at most {max_length} item(s)")
        else:
            parts.append(f"maximum length is {max_length}")
        if actual is not None:
            parts.append(f"got {actual}")
    if input_value is not None and not parts[1:]:
        parts.append(f"got: {input_value!r}")
    return "; ".join(parts)


def _format_extra_forbidden(
    base: str, input_value: object, ctx: Mapping[str, object], loc: object | None
) -> str:
    parts: list[str] = [base]
    key = _extra_forbidden_key(input_value, loc)
    if key is not None:
        parts.append(f"unknown field {key!r}")
    elif input_value is not None:
        parts.append(f"unknown field {input_value!r}")
    field_obj = ctx.get("field")
    if field_obj is not None:
        parts.append(f"on {field_obj!r}")
    return "; ".join(parts)


def _extra_forbidden_key(input_value: object, loc: object | None) -> str | None:
    if isinstance(loc, (list, tuple)) and loc:
        last = loc[-1]
        if isinstance(last, str):
            return last
    return None


def _format_int(base: str, input_value: object) -> str:
    if input_value is None:
        return base
    return f"{base}; got: {input_value!r}"


def _format_missing_field(base: str, input_value: object, ctx: Mapping[str, object]) -> str:
    field_obj = ctx.get("field")
    if field_obj is not None:
        return f"{base}; field {field_obj!r} is required"
    if input_value is not None:
        return f"{base}; got: {input_value!r}"
    return base


def _string_length(value: object) -> int | None:
    if isinstance(value, str):
        return len(value)
    return None


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    return {}
