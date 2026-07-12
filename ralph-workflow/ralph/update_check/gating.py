"""Opt-out gating for the update nagger.

A version check is an outbound call, so it honors the existing telemetry opt-out
(``RALPH_DISABLE_TELEMETRY`` / ``telemetry_enabled = false``). A dedicated
``RALPH_DISABLE_UPDATE_CHECK`` env var and ``update_check_enabled = false``
config key let a user silence the nag without also disabling telemetry.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.telemetry._sentry import is_telemetry_disabled, is_telemetry_disabled_by_config

if TYPE_CHECKING:
    from collections.abc import Mapping

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
UPDATE_CHECK_DISABLE_ENV = "RALPH_DISABLE_UPDATE_CHECK"


def _env_disables_update_check(environ: Mapping[str, str]) -> bool:
    raw = environ.get(UPDATE_CHECK_DISABLE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_VALUES


def _global_config_path(environ: Mapping[str, str]) -> Path:
    xdg_config_home = environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "ralph-workflow.toml"
    return Path.home() / ".config" / "ralph-workflow.toml"


def _config_disables_update_check(environ: Mapping[str, str]) -> bool:
    config_path = _global_config_path(environ)
    try:
        if not config_path.exists():
            return False
        with config_path.open("rb") as handle:
            data = cast("dict[str, object]", tomllib.load(handle))
    except (OSError, ValueError):
        return False
    raw_general = data.get("general")
    if not isinstance(raw_general, dict):
        return False
    general = cast("dict[str, object]", raw_general)
    return general.get("update_check_enabled") is False


def is_update_check_disabled(environ: Mapping[str, str]) -> bool:
    """Return True when any opt-out signal (telemetry or update-specific) is set."""
    return (
        is_telemetry_disabled(environ)
        or is_telemetry_disabled_by_config(environ)
        or _env_disables_update_check(environ)
        or _config_disables_update_check(environ)
    )
