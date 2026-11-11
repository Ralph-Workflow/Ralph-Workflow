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


def must_str(value: object, *, field: str | None = None) -> str:
    if not isinstance(value, str):
        msg = _field_message(field, f"expected str, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_int(value: object, *, field: str | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = _field_message(field, f"expected int, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_bool(value: object, *, field: str | None = None) -> bool:
    if not isinstance(value, bool):
        msg = _field_message(field, f"expected bool, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_mapping(value: object, *, field: str | None = None) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        msg = _field_message(field, f"expected Mapping[str, object], got {type(value).__name__}")
        raise TypeError(msg)
    for k in value:
        if not isinstance(k, str):
            msg = _field_message(field, f"mapping keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
    return value


def must_str_dict(value: object, *, field: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        msg = _field_message(field, f"expected dict[str, str], got {type(value).__name__}")
        raise TypeError(msg)
    for v in value.values():
        if not isinstance(v, str):
            msg = _field_message(field, f"dict values must be str, got {type(v).__name__}")
            raise TypeError(msg)
    return value


def must_object_list(value: object, *, field: str | None = None) -> list[object]:
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[object], got {type(value).__name__}")
        raise TypeError(msg)
    return value


def must_dict_list(value: object, *, field: str | None = None) -> list[Mapping[str, object]]:
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
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list[str], got {type(value).__name__}")
        raise TypeError(msg)
    for item in value:
        if not isinstance(item, str):
            msg = _field_message(field, f"list items must be str, got {type(item).__name__}")
            raise TypeError(msg)
    return value


def must_text(value: object, *, field: str | None = None) -> str:
    text_attr: object = getattr(value, "text", _MISSING)
    if text_attr is _MISSING or not isinstance(text_attr, str):
        msg = _field_message(field, "expected MCP TextContent with a str .text attribute")
        raise TypeError(msg)
    return text_attr


class _MissingSentinel:
    pass


_MISSING: object = _MissingSentinel()


def _field_message(field: str | None, message: str) -> str:
    if field is None:
        return message
    return f"{field}: {message}"
