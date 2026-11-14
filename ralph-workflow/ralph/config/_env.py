from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def getenv(name: str, env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    return source.get(name)


def resolve_config_home(env: Mapping[str, str] | None = None) -> Path:
    xdg = getenv("XDG_CONFIG_HOME", env) or ""
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"
