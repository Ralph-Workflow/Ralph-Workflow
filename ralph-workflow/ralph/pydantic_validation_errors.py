"""Shared Pydantic ValidationError formatter.

Converts ``pydantic.ValidationError`` exceptions into agent-friendly,
field-level messages. The formatter is consumed by every typed artifact
normalizer (plan, issues, fix_result, development_result, etc.) so the
hints an agent sees are uniform across all artifact types.

The exported helpers are:

- :func:`format_validation_error_messages` — top-level entry point that
  returns a list of ``"location: message"`` strings for every error
  attached to a ``ValidationError``.
- :func:`format_validation_error_detail` — format a single pydantic
  ``ErrorDetails`` mapping into one ``"location: message"`` string.
- :func:`format_validation_location` — convert a pydantic ``loc`` tuple
  to a dotted path string (e.g. ``"summary.intent"``).
- :func:`format_validation_message` — pull the human-readable ``msg``
  out of an error detail, substituting a placeholder if the message is
  missing.
- :func:`suggest_canonical_field` — when a model declares an unknown
  key, suggest the closest existing field name (via
  :func:`difflib.get_close_matches`).

The formatter is plan-agnostic: it only depends on pydantic and the
standard library, so it can be imported from any artifact normalizer
without creating circular imports.

The decision to use this module (rather than re-implementing
formatting in every normalizer) is driven by the recurring failure mode
of cheap models: a raw ``str(exc)`` of a pydantic
``ValidationError`` shows errors like::

    7 validation errors for Summary
    intent
      String should have at most 200 characters [type=string_too_long, ...]

which does not name the actual length, the actual value, or the valid
options for closed-enum fields. The formatter produced here adds those
three pieces of information (location, rejected value, allowed shape)
to every error line so an agent can act without guessing.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
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
    """Format all pydantic ValidationError errors into human-readable strings.

    Args:
        exc: The ``pydantic.ValidationError`` raised by ``model_validate``.

    Returns:
        A list of ``"location: message"`` strings, one per error in the
        exception. The location is the dotted field path; the message
        includes the rejected value and the allowed shape.
    """
    details = cast("ValidationErrorDetails", exc.errors())
    return [format_validation_error_detail(detail) for detail in details]


def format_validation_error_detail(detail: ValidationErrorDetail) -> str:
    """Format a single pydantic validation error detail as ``location: message``.

    Args:
        detail: One entry from ``ValidationError.errors()``.

    Returns:
        A single ``"  location: message"`` string.
    """
    loc = detail.get("loc")
    msg = _format_message_with_value(detail)
    return f"  {format_validation_location(loc)}: {msg}"


def format_validation_location(raw_loc: object | None) -> str:
    """Format a pydantic error location tuple to a dotted path string.

    Args:
        raw_loc: The ``loc`` field from a pydantic error detail. May be
            a tuple, a list, a string, or ``None``.

    Returns:
        A dotted path (e.g. ``"summary.intent"``) or a sentinel
        ``"<root>"`` when the location is empty/missing.
    """
    if raw_loc is None:
        return "<root>"
    if isinstance(raw_loc, (list, tuple)):
        if not raw_loc:
            return "<root>"
        return ".".join(str(component) for component in raw_loc)
    return str(raw_loc)


def format_validation_message(raw_msg: object | None) -> str:
    """Return the validation error message string.

    Substitutes ``"<missing message>"`` if the message is ``None`` so
    callers always get a non-empty string back.

    Args:
        raw_msg: The ``msg`` field from a pydantic error detail.

    Returns:
        The message string, ``"<missing message>"``, or the string form
        of the value.
    """
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
    """Suggest the closest canonical field name for an unknown key.

    Used by callers that detect an ``extra_forbidden`` error to point
    the agent at the field they probably meant. The suggestion is
    computed via :func:`difflib.get_close_matches` with the supplied
    cutoff (default 0.6, the difflib default).

    Args:
        unknown_key: The rejected key (e.g. ``"design_constraints"``).
        candidate_fields: The list of valid field names on the model.
        cutoff: Match cutoff in the range [0, 1]. Default ``0.6``.

    Returns:
        The closest matching field name, or ``None`` if no candidate
        scores above the cutoff.
    """
    if not candidate_fields:
        return None
    matches = difflib.get_close_matches(unknown_key, list(candidate_fields), n=1, cutoff=cutoff)
    if not matches:
        return None
    return matches[0]


_INT_ERROR_TYPES: frozenset[str] = frozenset({"int_parsing", "int_type", "int_from_float"})


def _format_message_with_value(detail: ValidationErrorDetail) -> str:  # noqa: PLR0911
    """Build a context-rich message from a single pydantic error detail.

    For known error types the rejected value and the allowed shape are
    appended to the base message. Unknown error types fall through to
    the raw pydantic ``msg`` so no information is lost.
    """
    err_type_obj = detail.get("type")
    raw_msg = detail.get("msg")
    base = format_validation_message(raw_msg)
    ctx_obj = detail.get("ctx")
    input_value = detail.get("input")
    ctx = _as_mapping(ctx_obj)
    if not isinstance(err_type_obj, str):
        return base
    if err_type_obj == "string_too_long":
        return _format_string_too_long(base, ctx, input_value)
    if err_type_obj in {"string_too_short", "missing"}:
        return _format_string_too_short(base, ctx, err_type_obj)
    if err_type_obj == "literal_error":
        return _format_literal_error(base, ctx, input_value)
    if err_type_obj in {"enum", "enum_member"}:
        return _format_enum(base, ctx, input_value)
    if err_type_obj in {"too_short", "too_long"}:
        return _format_too_short_or_long(base, ctx, input_value, err_type_obj)
    if err_type_obj == "extra_forbidden":
        return _format_extra_forbidden(base, input_value, ctx, detail.get("loc"))
    if err_type_obj in _INT_ERROR_TYPES:
        return _format_int(base, input_value)
    if err_type_obj == "missing_keyword_only_argument":
        return _format_missing_field(base, input_value, ctx)
    return base


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
    """Return the rejected key for an ``extra_forbidden`` error if known.

    Pydantic puts the rejected key in ``loc`` (the last component) and
    the rejected value in ``input``. Callers that want to name the
    key (e.g. to suggest a canonical field name) should prefer this
    helper over inspecting ``input`` directly.
    """
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
    """Return the length of *value* if it is a string, else ``None``."""
    if isinstance(value, str):
        return len(value)
    return None


def _as_mapping(value: object) -> Mapping[str, object]:
    """Coerce a value to a read-only mapping, defaulting to empty."""
    if isinstance(value, Mapping):
        return cast("Mapping[str, object]", value)
    return {}
