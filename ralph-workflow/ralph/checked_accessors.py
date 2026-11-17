from __future__ import annotations

import dataclasses
import json
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, cast

__all__ = [
    "as_bool",
    "as_int",
    "as_mapping",
    "as_object_dict",
    "as_object_list",
    "as_path",
    "as_sequence",
    "as_str",
    "as_str_dict",
    "as_str_list",
    "dataclass_field_names",
    "loads_json",
    "loads_toml",
    "module_attr",
    "optional_bool",
    "optional_float",
    "optional_int",
    "optional_str",
    "optional_str_list",
    "type_as_object_type",
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
    for k, v in cast("Mapping[object, object]", value).items():
        if not isinstance(k, str):
            msg = _field_message(field, f"dict keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
        if not isinstance(v, str):
            msg = _field_message(field, f"dict values must be str, got {type(v).__name__}")
            raise TypeError(msg)
        result[k] = v
    return result


def loads_json(raw: str | bytes, *, strict: bool = True) -> object:
    if isinstance(raw, bytes):
        return cast("object", json.loads(raw))
    return cast("object", json.loads(raw, strict=strict))


def loads_toml(raw: IO[bytes]) -> object:
    return cast("object", tomllib.load(raw))


def module_attr(module: object, name: str) -> object:
    return cast("object", getattr(module, name))


def type_as_object_type(typ: object) -> type[object]:
    builtins_mod = __import__("builtins")
    type_obj = module_attr(builtins_mod, "type")
    type_type = cast("type[object]", type_obj)
    if not isinstance(typ, type_type):
        msg = f"expected type, got {type(typ).__name__}"
        raise TypeError(msg)
    return cast("type[object]", typ)


def dataclass_field_names(instance: object) -> frozenset[str]:
    fields_fn = module_attr(dataclasses, "fields")
    if not callable(fields_fn):
        msg = "dataclasses.fields is not callable"
        raise TypeError(msg)
    fields = cast("tuple[object, ...]", fields_fn(instance))
    names: set[str] = set()
    for field in fields:
        name = cast("object | None", getattr(field, "name", None))
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def as_object_dict(value: object, *, field: str | None = None) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = _field_message(field, f"expected dict[str, object], got {type(value).__name__}")
        raise TypeError(msg)
    result: dict[str, object] = {}
    for k, v in cast("Mapping[object, object]", value).items():
        if not isinstance(k, str):
            msg = _field_message(field, f"dict keys must be str, got {type(k).__name__}")
            raise TypeError(msg)
        result[k] = v
    return result


def as_mapping(value: object, *, field: str | None = None) -> Mapping[str, object]:
    return as_object_dict(value, field=field)


def as_object_list(value: object, *, field: str | None = None) -> list[object]:
    if not isinstance(value, list):
        msg = _field_message(field, f"expected list, got {type(value).__name__}")
        raise TypeError(msg)
    return list(cast("list[object]", value))


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
    return cast("Sequence[object]", value)


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
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    return None


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
