from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, MutableMapping
from pathlib import Path


class _EnvironView(MutableMapping[str, str]):
    __slots__ = ("_raw",)
    _raw: object

    def __init__(self, raw: object) -> None:
        if not isinstance(raw, MutableMapping):
            raise TypeError(f"expected MutableMapping, got {type(raw).__name__}")
        self._raw = raw

    def _mapping(self) -> MutableMapping[object, object]:
        raw = self._raw
        if not isinstance(raw, MutableMapping):
            raise TypeError(f"expected MutableMapping, got {type(raw).__name__}")
        typed: MutableMapping[object, object] = raw
        return typed

    def __getitem__(self, key: str) -> str:
        value: object = self._mapping()[key]
        if not isinstance(value, str):
            raise TypeError(f"environ[{key!r}] must be str, got {type(value).__name__}")
        return value

    def __setitem__(self, key: str, value: str) -> None:
        self._mapping()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        for key in self._mapping():
            if not isinstance(key, str):
                raise TypeError(f"environ key must be str, got {type(key).__name__}")
            yield key

    def __len__(self) -> int:
        return len(self._mapping())

    def __contains__(self, key: object) -> bool:
        return key in self._mapping()


def _os_environ() -> _EnvironView:
    raw: object = os.environ
    return _EnvironView(raw)

__all__ = [
    "copy_environ",
    "del_environ",
    "environ",
    "getenv",
    "os",
    "pop_environ",
    "resolve_config_home",
    "resolve_environ",
    "set_environ",
    "setdefault_environ",
    "update_environ",
]


def getenv(name: str, env: Mapping[str, str] | None = None) -> str | None:
    source: Mapping[str, str] = _os_environ() if env is None else env
    return source.get(name)


def environ() -> MutableMapping[str, str]:
    return _os_environ()


def copy_environ() -> dict[str, str]:
    return dict(_os_environ())


def resolve_environ(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return _os_environ() if env is None else env


def set_environ(name: str, value: str) -> None:
    _os_environ()[name] = value


def setdefault_environ(name: str, value: str) -> str:
    current = getenv(name)
    if current is not None:
        return current
    set_environ(name, value)
    return value


def pop_environ(name: str, default: str | None = None) -> str | None:
    mapping = _os_environ()
    if name not in mapping:
        return default
    return mapping.pop(name)


def del_environ(name: str) -> None:
    del _os_environ()[name]


def update_environ(values: Mapping[str, str]) -> None:
    mapping = _os_environ()
    for key, value in values.items():
        mapping[key] = value


def resolve_config_home(env: Mapping[str, str] | None = None) -> Path:
    xdg = getenv("XDG_CONFIG_HOME", env) or ""
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"
