"""Checked accessors for production boundary code.

These helpers exist so that production code can convert an ``object`` read
from deserialized input, a configuration file, subprocess output, a
network response, or any other untyped mapping into a typed Python value
WITHOUT using ``typing.cast``.

The shape of the value is unknown to the type checker: the value is
``object`` precisely because it comes from outside the type system's
control. A ``cast`` would be an unverified claim about data the project
does not control. The helpers here replace that claim with a runtime
check that either returns a typed value or raises an exception (for
raising variants) or returns an explicit absent value (for the
absent-returning variants used at deliberately lenient boundaries such
as third-party agent-output parsers).

Two families:

* Raising variants (``as_str``, ``as_int``, ``as_bool``, ``as_path``,
  ``as_str_dict``, ``as_object_list``, ``as_str_list``, ``as_mapping``,
  ``as_sequence``) — used at trust boundaries that MUST validate the
  payload (e.g. user-supplied configuration, structured artifact
  payloads).
* Absent-returning variants (``optional_str``, ``optional_int``,
  ``optional_bool``, ``optional_str_list``) — used at deliberately
  lenient boundaries where a malformed third-party record MUST be
  skipped, not raised on (e.g. parsers for third-party agent output).

The helpers are pure and side-effect free. They carry no ``cast`` and
no ``type: ignore``; the implementation narrows with ``isinstance``
checks whose truth value the type checker can verify.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = [
    "as_bool",
    "as_int",
    "as_mapping",
    "as_object_list",
    "as_path",
    "as_sequence",
    "as_str",
    "as_str_dict",
    "as_str_list",
    "optional_bool",
    "optional_int",
    "optional_str",
    "optional_str_list",
]


# ---------------------------------------------------------------------------
# Raising variants
# ---------------------------------------------------------------------------


def as_str(value: object, *, field: str | None = None) -> str:
    """Return ``value`` if it is a non-empty :class:`str`, else raise.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        The validated string.

    Raises:
        TypeError: When ``value`` is not a string.
        ValueError: When ``value`` is an empty string.
    """
    if not isinstance(value, str):
        msg = _field_message(field, f"expected str, got {type(value).__name__}")
        raise TypeError(msg)
    if not value:
        msg = _field_message(field, "expected non-empty str, got empty value")
        raise ValueError(msg)
    return value


def as_int(value: object, *, field: str | None = None) -> int:
    """Return ``value`` if it is a non-bool :class:`int`, else raise.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        The validated integer.

    Raises:
        TypeError: When ``value`` is not an integer (booleans are rejected
            because Python's ``bool`` is a subclass of ``int``).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        msg = _field_message(field, f"expected int, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_bool(value: object, *, field: str | None = None) -> bool:
    """Return ``value`` if it is a :class:`bool`, else raise.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        The validated boolean.

    Raises:
        TypeError: When ``value`` is not a boolean.
    """
    if not isinstance(value, bool):
        msg = _field_message(field, f"expected bool, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_path(value: object, *, field: str | None = None) -> Path:
    """Return ``value`` if it is a :class:`Path`, else raise.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        The validated path.

    Raises:
        TypeError: When ``value`` is not a path-like object.
    """
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    msg = _field_message(field, f"expected Path, got {type(value).__name__}")
    raise TypeError(msg)


def as_str_dict(value: object, *, field: str | None = None) -> dict[str, str]:
    """Return ``value`` if it is a :class:`dict` of ``str`` to ``str``.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        A new ``dict[str, str]`` view (a shallow copy when the input is
        already ``dict[str, str]``).

    Raises:
        TypeError: When ``value`` is not a dict, or when one of its
            values is not a string.
    """
    if not isinstance(value, dict):
        msg = _field_message(field, f"expected dict[str, str], got {type(value).__name__}")
        raise TypeError(msg)
    result: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(v, str):
            msg = _field_message(field, f"dict values must be str, got {type(v).__name__}")
            raise TypeError(msg)
        result[k] = v
    return result


def as_mapping(value: object, *, field: str | None = None) -> Mapping[str, object]:
    """Return ``value`` if it is a :class:`Mapping` of ``str`` to ``object``.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        A read-only view of the mapping. Returns the value unchanged
        when it already satisfies the static type.

    Raises:
        TypeError: When ``value`` is not a mapping of strings.
    """
    if not isinstance(value, Mapping):
        msg = _field_message(field, f"expected Mapping[str, object], got {type(value).__name__}")
        raise TypeError(msg)
    for k in value:
        if not isinstance(k, str):
            msg = _field_message(field, f"mapping keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
    return value


def as_object_list(value: object, *, field: str | None = None) -> list[object]:
    """Return ``value`` if it is a :class:`list`.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        A list view. The element type is left as ``object`` so callers
        can narrow further with ``isinstance`` checks or additional
        accessors.

    Raises:
        TypeError: When ``value`` is not a list.
    """
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_str_list(value: object, *, field: str | None = None) -> list[str]:
    """Return ``value`` if it is a :class:`list` of strings.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        A new ``list[str]`` view.

    Raises:
        TypeError: When ``value`` is not a list, or when one of its
            elements is not a string.
    """
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[str], got {type(value).__name__}")
        raise TypeError(msg)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = _field_message(field, f"list items must be str, got {type(item).__name__}")
            raise TypeError(msg)
        result.append(item)
    return result


def as_sequence(value: object, *, field: str | None = None) -> Sequence[object]:
    """Return ``value`` if it is a non-string :class:`Sequence`.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used to produce a clearer error message.

    Returns:
        A read-only view of the sequence.

    Raises:
        TypeError: When ``value`` is not a non-string sequence.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        msg = _field_message(field, f"expected Sequence[object], got {type(value).__name__}")
        raise TypeError(msg)
    return value


# ---------------------------------------------------------------------------
# Absent-returning variants (lenient boundaries)
# ---------------------------------------------------------------------------


def optional_str(value: object, *, field: str | None = None) -> str | None:
    """Return ``value`` if it is a non-empty :class:`str`, else ``None``.

    Designed for deliberately lenient boundaries: the parser must skip
    the malformed record and continue, not raise. An optional field is
    silently treated as ``None``.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used for debugging messages.

    Returns:
        The validated string, or ``None`` when the value is not a
        non-empty string.
    """
    del field  # kept for symmetry with the raising variants.
    if not isinstance(value, str) or not value:
        return None
    return value


def optional_int(value: object, *, field: str | None = None) -> int | None:
    """Return ``value`` if it is a non-bool :class:`int`, else ``None``.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used for debugging messages.

    Returns:
        The validated integer, or ``None`` when the value is not an
        integer.
    """
    del field
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def optional_bool(value: object, *, field: str | None = None) -> bool | None:
    """Return ``value`` if it is a :class:`bool`, else ``None``.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used for debugging messages.

    Returns:
        The validated boolean, or ``None`` when the value is not a
        boolean.
    """
    del field
    if not isinstance(value, bool):
        return None
    return value


def optional_str_list(value: object, *, field: str | None = None) -> list[str] | None:
    """Return ``value`` if it is a :class:`list` of strings, else ``None``.

    Designed for lenient boundaries: a malformed list is treated as a
    missing value rather than an error.

    Args:
        value: The raw value read from an untyped boundary.
        field: Optional field name used for debugging messages.

    Returns:
        The validated string list, or ``None`` when ``value`` is not a
        list of strings.
    """
    del field
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field_message(field: str | None, message: str) -> str:
    """Format a clearer error message that includes the field name when given."""
    if field is None:
        return message
    return f"{field}: {message}"
