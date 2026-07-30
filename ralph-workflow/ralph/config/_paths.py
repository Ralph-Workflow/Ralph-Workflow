"""User-global configuration path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the user-global config directory.

    Honors XDG_CONFIG_HOME when set; falls back to ~/.config.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    xdg = env_map.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"
