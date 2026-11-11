"""Kill switch for universal-skill updates.

Universal-skill updates reach the network to refresh the shipped skill
bundle, so they honour an explicit user opt-out. Two INDEPENDENT
controls answer the same question:

- the ``RALPH_DISABLE_SKILL_UPDATES`` environment variable, and
- the ``[skills] universal_updates_enabled = false`` key in the
  user-global config file.

Either control disables the updates on its own; the module never
requires both. The config file is read directly with ``tomllib``
because this repository has no config-schema extension point for a
``[skills]`` table. Every unreadable, malformed, or absent config
fails OPEN (updates stay enabled) so a broken config can never silence
the user's skill tree.

The environment mapping is a parameter rather than hidden global state,
so a caller (and a test) can inject the whole decision surface.
"""

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
    """Return True when the env var carries a truthy opt-out value."""
    raw = environ.get(UNIVERSAL_SKILL_UPDATE_DISABLE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_VALUES


def _global_config_path(environ: Mapping[str, str]) -> Path:
    """Return the user-global config path derived from ``environ``.

    ``XDG_CONFIG_HOME`` wins when the injected mapping sets it; the
    ``~/.config`` fallback is used otherwise.
    """
    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / _CONFIG_FILE_NAME
    return Path.home() / ".config" / _CONFIG_FILE_NAME


def _config_disables_skill_updates(environ: Mapping[str, str]) -> bool:
    """Return True when the user-global config disables skill updates.

    Only the boolean ``false`` disables. A missing file, an unreadable
    file, malformed TOML, a missing ``[skills]`` table, a missing key,
    and a non-boolean value all mean "not disabled".
    """
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
    """Return True when universal-skill updates are disabled.

    The env var and the config key are independent opt-outs: either one
    disables the updates. The check never raises; an unreadable or
    malformed config fails open.

    Args:
        environ: Environment mapping to read. Defaults to the process
            environment.

    Returns:
        True when the updates are disabled, False otherwise.
    """
    resolved = os.environ if environ is None else environ
    return _env_disables_skill_updates(resolved) or _config_disables_skill_updates(resolved)


def universal_skill_updates_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return the inverse of :func:`is_universal_skill_update_disabled`.

    Args:
        environ: Environment mapping to read. Defaults to the process
            environment.

    Returns:
        True when the updates may run, False when they are disabled.
    """
    return not is_universal_skill_update_disabled(environ)


__all__ = [
    "UNIVERSAL_SKILL_UPDATE_DISABLE_ENV",
    "is_universal_skill_update_disabled",
    "universal_skill_updates_enabled",
]
