from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def as_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def require_object_dict(value: object, *, error: str) -> dict[str, object]:
    result = as_object_dict(value)
    if result is None:
        raise TypeError(error)
    return result


def mapping_as_object_dict(value: Mapping[object, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if isinstance(key, str)}


__all__ = ["as_object_dict", "mapping_as_object_dict", "require_object_dict"]
