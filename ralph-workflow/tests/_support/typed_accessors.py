"""Test-side typed accessors — runtime validators for test fixtures.

These helpers exist so that the test suite can shape deserialized-style
fixtures WITHOUT using ``typing.cast``. They mirror the production
``ralph.checked_accessors`` helpers in spirit: each one takes a value of
type ``object``, validates its shape at runtime, and returns the
narrowed type. Tests are policy-required to use these helpers rather
than ``cast`` when a fixture needs a typed value.

Unlike ``ralph.checked_accessors``, these helpers raise on every
mismatch — there is no lenient variant for tests because test
fixtures must be deterministic: if a fixture is malformed the test
should fail loudly, not silently skip.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "must_bool",
    "must_dict_list",
    "must_int",
    "must_mapping",
    "must_object_list",
    "must_str",
    "must_str_dict",
    "must_str_list",
    "must_text",
]


# ---------------------------------------------------------------------------
# Single-value accessors
# ---------------------------------------------------------------------------


def must_str(value: object, *, field: str | None = None) -> str:
    """Return ``value`` if it is a non-empty :class:`str`, else raise."""
    if not isinstance(value, str):
        msg = _field_message(field, f"expected str, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_int(value: object, *, field: str | None = None) -> int:
    """Return ``value`` if it is a non-bool :class:`int`, else raise."""
    if isinstance(value, bool) or not isinstance(value, int):
        msg = _field_message(field, f"expected int, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_bool(value: object, *, field: str | None = None) -> bool:
    """Return ``value`` if it is a :class:`bool`, else raise."""
    if not isinstance(value, bool):
        msg = _field_message(field, f"expected bool, got {type(value).__name__}")
        raise TypeError(msg)
    return value


# ---------------------------------------------------------------------------
# Container accessors
# ---------------------------------------------------------------------------


def must_mapping(value: object, *, field: str | None = None) -> Mapping[str, object]:
    """Return ``value`` if it is a :class:`Mapping` of ``str`` to ``object``."""
    if not isinstance(value, Mapping):
        msg = _field_message(field, f"expected Mapping[str, object], got {type(value).__name__}")
        raise TypeError(msg)
    for k in value:
        if not isinstance(k, str):
            msg = _field_message(field, f"mapping keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
    return value


def must_str_dict(value: object, *, field: str | None = None) -> dict[str, str]:
    """Return ``value`` if it is a :class:`dict[str, str]``."""
    if not isinstance(value, dict):
        msg = _field_message(field, f"expected dict[str, str], got {type(value).__name__}")
        raise TypeError(msg)
    for v in value.values():
        if not isinstance(v, str):
            msg = _field_message(field, f"dict values must be str, got {type(v).__name__}")
            raise TypeError(msg)
    return value


def must_object_list(value: object, *, field: str | None = None) -> list[object]:
    """Return ``value`` if it is a :class:`list`."""
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[object], got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_dict_list(value: object, *, field: str | None = None) -> list[Mapping[str, object]]:
    """Return ``value`` if it is a :class:`list` of :class:`Mapping`."""
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[Mapping], got {type(value).__name__}")
        raise TypeError(msg)
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            msg = _field_message(
                field,
                f"item at index {i} must be Mapping, got {type(item).__name__}",
            )
            raise TypeError(msg)
    return value


def must_str_list(value: object, *, field: str | None = None) -> list[str]:
    """Return ``value`` if it is a :class:`list[str]`."""
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[str], got {type(value).__name__}")
        raise TypeError(msg)
    for item in value:
        if not isinstance(item, str):
            msg = _field_message(field, f"list items must be str, got {type(item).__name__}")
            raise TypeError(msg)
    return value


# ---------------------------------------------------------------------------
# MCP / external-payload helpers
# ---------------------------------------------------------------------------


def must_text(value: object, *, field: str | None = None) -> str:
    """Return ``value.text`` when ``value`` is an MCP ``TextContent`` block.

    Many MCP payloads are objects with a ``.text`` attribute (the text
    content block). Tests use this helper to narrow that block to a
    plain string without resorting to ``cast``.

    Args:
        value: An object expected to expose ``.text`` returning a string.
        field: Optional field name used for clearer error messages.

    Returns:
        The text payload.

    Raises:
        TypeError: When ``value`` does not expose ``.text``, or its
            ``.text`` is not a string.
    """
    text_attr: object = getattr(value, "text", _MISSING)
    if text_attr is _MISSING or not isinstance(text_attr, str):
        msg = _field_message(field, "expected MCP TextContent with a str .text attribute")
        raise TypeError(msg)
    return text_attr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MissingSentinel:
    """Sentinel distinct from ``None`` — distinguishes ``absent`` from ``present-and-None``."""


_MISSING: object = _MissingSentinel()

def _field_message(field: str | None, message: str) -> str:
    if field is None:
        return message
    return f"{field}: {message}"
