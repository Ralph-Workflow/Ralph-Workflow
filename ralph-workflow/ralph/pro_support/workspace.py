
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support.env import RALPH_WORKSPACE, _get_env

if TYPE_CHECKING:
    from collections.abc import Mapping


def resolve_pro_workspace(
    env: Mapping[str, str] | None = None,
    fallback: Path | str | None = None,
) -> Path:
    env_map = _get_env(env)
    candidate = env_map.get(RALPH_WORKSPACE, "")
    if candidate:
        return Path(candidate).expanduser().resolve()
    base = Path.cwd() if fallback is None else Path(fallback)
    return base.expanduser().resolve()


def resolve_pro_workspace_from_environ(
    fallback: Path | str | None = None,
) -> Path:
    return resolve_pro_workspace(os.environ, fallback=fallback)


__all__ = ["resolve_pro_workspace", "resolve_pro_workspace_from_environ"]
