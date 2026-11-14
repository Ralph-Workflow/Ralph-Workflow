from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config._env import resolve_config_home

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    return resolve_config_home(env)
