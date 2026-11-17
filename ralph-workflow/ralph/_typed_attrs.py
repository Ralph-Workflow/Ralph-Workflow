from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast, overload

__all__ = [
    "attr_or_none",
    "has_attr",
    "is_callable_obj",
    "mapping_attr",
    "optional_bool_attr",
    "optional_int_attr",
    "optional_mapping_attr",
    "optional_str_attr",
    "read_attr",
    "read_bool_attr",
    "read_callable_attr",
    "read_float_attr",
    "read_instance_attr",
    "read_int_attr",
    "read_optional_attr",
    "read_optional_instance_attr",
    "read_optional_int_attr",
    "read_optional_str_attr",
    "read_str_attr",
    "require_attr",
]

T = TypeVar("T")


def attr_or_none(obj: object, name: str) -> object | None:
    return cast("object | None", getattr(obj, name, None))


@overload
def read_attr(obj: object, name: str, default: None) -> object | None: ...


@overload
def read_attr[T](obj: object, name: str, default: T) -> T: ...


def read_attr(obj: object, name: str, default: object = None) -> object:
    return cast("object", getattr(obj, name, default))


def read_optional_attr(obj: object, name: str) -> object | None:
    return attr_or_none(obj, name)


def require_attr(obj: object, name: str) -> object:
    if not hasattr(obj, name):
        msg = f"missing attribute {name!r} on {type(obj).__name__}"
        raise AttributeError(msg)
    return cast("object", getattr(obj, name))


def has_attr(obj: object, name: str) -> bool:
    return hasattr(obj, name)


def is_callable_obj(value: object) -> bool:
    return callable(value)


def optional_str_attr(obj: object, name: str) -> str | None:
    value = attr_or_none(obj, name)
    if not isinstance(value, str):
        return None
    return value


def read_optional_str_attr(obj: object, name: str) -> str | None:
    return optional_str_attr(obj, name)


def read_str_attr(obj: object, name: str, default: str = "") -> str:
    value = attr_or_none(obj, name)
    if not isinstance(value, str):
        return default
    return value


def optional_int_attr(obj: object, name: str) -> int | None:
    value = attr_or_none(obj, name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def read_optional_int_attr(obj: object, name: str) -> int | None:
    return optional_int_attr(obj, name)


def read_int_attr(obj: object, name: str, default: int = 0) -> int:
    value = optional_int_attr(obj, name)
    if value is None:
        return default
    return value


def optional_bool_attr(obj: object, name: str) -> bool | None:
    value = attr_or_none(obj, name)
    if not isinstance(value, bool):
        return None
    return value


def read_bool_attr(obj: object, name: str, default: bool = False) -> bool:
    value = optional_bool_attr(obj, name)
    if value is None:
        return default
    return value


def read_float_attr(obj: object, name: str, default: float = 0.0) -> float:
    value = attr_or_none(obj, name)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def optional_mapping_attr(obj: object, name: str) -> Mapping[str, object] | None:
    value = attr_or_none(obj, name)
    if not isinstance(value, Mapping):
        return None
    for key in value:
        if not isinstance(key, str):
            return None
    return cast("Mapping[str, object]", value)


def mapping_attr(obj: object, name: str, default: Mapping[str, object]) -> Mapping[str, object]:
    value = optional_mapping_attr(obj, name)
    if value is None:
        return default
    return value


def read_instance_attr[T](obj: object, name: str, typ: type[T], default: T) -> T:
    value = attr_or_none(obj, name)
    if not isinstance(value, typ):
        return default
    return value


def read_optional_instance_attr[T](obj: object, name: str, typ: type[T]) -> T | None:
    value = attr_or_none(obj, name)
    if value is None:
        return None
    if not isinstance(value, typ):
        return None
    return value


@overload
def read_callable_attr(obj: object, name: str, default: None) -> object | None: ...


@overload
def read_callable_attr[T](obj: object, name: str, default: T) -> T: ...


def read_callable_attr(obj: object, name: str, default: object = None) -> object:
    value = attr_or_none(obj, name)
    if not callable(value):
        return default
    return value
