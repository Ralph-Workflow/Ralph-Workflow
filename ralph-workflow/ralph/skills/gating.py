from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
UNIVERSAL_SKILL_UPDATE_DISABLE_ENV = "RALPH_DISABLE_SKILL_UPDATES"
_CONFIG_FILE_NAME = "ralph-workflow.toml"
_CONFIG_SECTION = "skills"
_CONFIG_KEY = "universal_updates_enabled"


def _env_disables_skill_updates(environ: Mapping[str, str]) -> bool:
    raw = environ.get(UNIVERSAL_SKILL_UPDATE_DISABLE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_VALUES


def _global_config_path(environ: Mapping[str, str]) -> Path:
    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / _CONFIG_FILE_NAME
    return Path.home() / ".config" / _CONFIG_FILE_NAME


def _config_disables_skill_updates(environ: Mapping[str, str]) -> bool:
    try:
        config_path = _global_config_path(environ)
        if not config_path.exists():
            return False
        with config_path.open("rb") as handle:
            data = cast("dict[str, object]", tomllib.load(handle))
    except (OSError, RuntimeError, ValueError):
        return False
    raw_section = data.get(_CONFIG_SECTION)
    if not isinstance(raw_section, dict):
        return False
    section = cast("dict[str, object]", raw_section)
    return section.get(_CONFIG_KEY) is False


def is_universal_skill_update_disabled(environ: Mapping[str, str] | None = None) -> bool:
    resolved = os.environ if environ is None else environ
    return _env_disables_skill_updates(resolved) or _config_disables_skill_updates(resolved)


def universal_skill_updates_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return not is_universal_skill_update_disabled(environ)


__all__ = [
    "UNIVERSAL_SKILL_UPDATE_DISABLE_ENV",
    "is_universal_skill_update_disabled",
    "universal_skill_updates_enabled",
]
