from __future__ import annotations

from typing import TypeGuard

__all__ = ["as_object_dict", "as_object_list", "is_object_dict"]


def is_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    if not isinstance(value, dict):
        return False
    return all(isinstance(key, str) for key in value)


def as_object_dict(value: object) -> dict[str, object]:
    if not is_object_dict(value):
        msg = f"expected dict[str, object], got {type(value).__name__}"
        raise TypeError(msg)
    return value


def as_object_list(value: object) -> list[object]:
    if not isinstance(value, list):
        msg = f"expected list, got {type(value).__name__}"
        raise TypeError(msg)
    return value
