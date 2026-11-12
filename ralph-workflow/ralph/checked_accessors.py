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
    "optional_float",
    "optional_int",
    "optional_str",
    "optional_str_list",
]


def as_str(value: object, *, field: str | None = None) -> str:
    if not isinstance(value, str):
        msg = _field_message(field, f"expected str, got {type(value).__name__}")
        raise TypeError(msg)
    if not value:
        msg = _field_message(field, "expected non-empty str, got empty value")
        raise ValueError(msg)
    return value


def as_int(value: object, *, field: str | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = _field_message(field, f"expected int, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_bool(value: object, *, field: str | None = None) -> bool:
    if not isinstance(value, bool):
        msg = _field_message(field, f"expected bool, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_path(value: object, *, field: str | None = None) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    msg = _field_message(field, f"expected Path, got {type(value).__name__}")
    raise TypeError(msg)


def as_str_dict(value: object, *, field: str | None = None) -> dict[str, str]:
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
    if not isinstance(value, Mapping):
        msg = _field_message(field, f"expected Mapping[str, object], got {type(value).__name__}")
        raise TypeError(msg)
    for k in value:
        if not isinstance(k, str):
            msg = _field_message(field, f"mapping keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
    return value


def as_object_list(value: object, *, field: str | None = None) -> list[object]:
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list, got {type(value).__name__}")
        raise TypeError(msg)
    return value


def as_str_list(value: object, *, field: str | None = None) -> list[str]:
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
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        msg = _field_message(field, f"expected Sequence[object], got {type(value).__name__}")
        raise TypeError(msg)
    return value


def optional_str(value: object, *, field: str | None = None) -> str | None:
    del field
    if not isinstance(value, str) or not value:
        return None
    return value


def optional_int(value: object, *, field: str | None = None) -> int | None:
    del field
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def optional_float(value: object, *, field: str | None = None) -> float | None:
    del field
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def optional_bool(value: object, *, field: str | None = None) -> bool | None:
    del field
    if not isinstance(value, bool):
        return None
    return value


def optional_str_list(value: object, *, field: str | None = None) -> list[str] | None:
    del field
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        result.append(item)
    return result


def _field_message(field: str | None, message: str) -> str:
    if field is None:
        return message
    return f"{field}: {message}"
