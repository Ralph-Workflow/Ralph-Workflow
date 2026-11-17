from __future__ import annotations

import os as os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

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
    source = os.environ if env is None else env
    return source.get(name)


def environ() -> MutableMapping[str, str]:
    return os.environ


def copy_environ() -> dict[str, str]:
    return dict(os.environ)


def resolve_environ(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def set_environ(name: str, value: str) -> None:
    os.environ[name] = value


def setdefault_environ(name: str, value: str) -> str:
    return os.environ.setdefault(name, value)


def pop_environ(name: str, default: str | None = None) -> str | None:
    return os.environ.pop(name, default)


def del_environ(name: str) -> None:
    del os.environ[name]


def update_environ(values: Mapping[str, str]) -> None:
    os.environ.update(values)


def resolve_config_home(env: Mapping[str, str] | None = None) -> Path:
    xdg = getenv("XDG_CONFIG_HOME", env) or ""
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"
