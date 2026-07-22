"""Layered TOML configuration loader.

Merge order (lowest to highest priority):
  1. Embedded defaults (Pydantic field defaults)
  2. ~/.config/ralph-workflow.toml (or $XDG_CONFIG_HOME/ralph-workflow.toml)
  3. .agent/ralph-workflow.toml (project-local)
  4. CLI flag overrides

This module handles the four-layer configuration merge:
- Embedded defaults provide the baseline for every field.
- Global config supplies user-wide preferences.
- Project-local config supplies repo-specific overrides.
- CLI overrides apply last via dict patch before Pydantic validation.
"""

from __future__ import annotations

import tomllib
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger
from pydantic import ValidationError

from ralph.config.agent_config import AgentConfig
from ralph.config.config_error_messages import format_config_validation_error
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    from ralph.workspace.scope import WorkspaceScope

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow.toml"
LOCAL_CONFIG_PATH = Path(".agent") / "ralph-workflow.toml"


class ConfigTomlError(ValueError):
    """A malformed main configuration file that needs user correction."""


def deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """Recursively merge override into base; override wins on conflict.

    Args:
        base: The base dictionary to merge into.
        override: The override dictionary to merge.

    Returns:
        A new dictionary with the merged result.
    """
    result: dict[str, object] = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(cast("dict[str, object]", result[key]), value)
        else:
            result[key] = value
    return result


def load_toml(path: Path) -> dict[str, object]:
    """Read a TOML file; return empty dict if missing.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed TOML content as a dictionary, or empty dict if file doesn't exist.
    """
    if not path.exists():
        logger.debug("Config file not found, skipping: {}", path)
        return {}
    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
    except cast("type[ValueError]", tomllib.TOMLDecodeError) as exc:
        raise ConfigTomlError(
            f"What failed: Ralph could not read {path}: {exc}.\n"
            "Why it matters: settings in a malformed file are not safe to use.\n"
            f"Fix: correct the TOML syntax in {path}, then run `ralph --check-config`."
        ) from exc
    logger.debug("Loaded config from {}", path)
    return data


def warn_unknown_fields(data: dict[str, object], path: Path) -> None:
    """Warn about misspelled fields in the closed main-config schema."""
    _warn_unknown_mapping_fields(data, UnifiedConfig.model_fields, path, "")
    general = data.get("general")
    if isinstance(general, dict):
        _warn_unknown_mapping_fields(general, GeneralConfig.model_fields, path, "general.")
    agents = data.get("agents")
    if isinstance(agents, dict):
        for name, agent in agents.items():
            if isinstance(name, str) and isinstance(agent, dict):
                _warn_unknown_mapping_fields(
                    agent, AgentConfig.model_fields, path, f"agents.{name}."
                )


def _warn_unknown_mapping_fields(
    data: dict[str, object], known_fields: object, path: Path, prefix: str
) -> None:
    field_names = set(cast("dict[str, object]", known_fields))
    for field in data:
        if field not in field_names:
            logger.warning("Unknown configuration field `{}` in {}.", f"{prefix}{field}", path)


def _convert_legacy_config(data: dict[str, object]) -> dict[str, object]:
    """Convert legacy UnifiedConfig format to current format.

    This handles the migration from the old flat structure to the new
    nested GeneralConfig with behavior/workflow/execution flags.

    Args:
        data: Raw config dictionary from TOML.

    Returns:
        Converted config dictionary.
    """
    if "general" in data:
        return data

    general: dict[str, object] = {}
    _migrate_verbosity(data, general)
    _migrate_workflow_flags(data, general)
    _migrate_simple_fields(data, general)

    if general:
        data["general"] = general

    return data


def _global_config_path() -> Path:
    """Resolve the global config path, honoring XDG_CONFIG_HOME when set."""
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "ralph-workflow.toml"
    return GLOBAL_CONFIG_PATH


def _migrate_verbosity(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate verbosity field."""
    if "verbosity" in data:
        general["verbosity"] = data.pop("verbosity")


def _migrate_workflow_flags(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate workflow flags."""
    workflow: dict[str, object] = {}
    if "checkpoint_enabled" in data:
        workflow["checkpoint_enabled"] = data.pop("checkpoint_enabled")
    if workflow:
        general["workflow"] = workflow


def _migrate_simple_fields(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate simple configuration fields."""
    simple_fields = (
        "developer_iters",
        "developer_context",
        "prompt_path",
        "templates_dir",
        "git_user_name",
        "git_user_email",
        "provider_fallback",
        "max_same_agent_retries",
        "max_commit_residual_retries",
        "max_retries",
        "retry_delay_ms",
        "backoff_multiplier",
        "max_backoff_ms",
        "max_cycles",
        "execution_history_limit",
        "auto_integrate_enabled",
        "auto_integrate_target",
        "auto_integrate_fetch_enabled",
        "auto_integrate_fetch_timeout_seconds",
        "auto_integrate_resolve_timeout_seconds",
    )
    for field in simple_fields:
        if field in data:
            general[field] = data.pop(field)


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    workspace_scope: WorkspaceScope | None = None,
) -> UnifiedConfig:
    """Build merged UnifiedConfig from all layers.

    Merge order (lowest to highest priority):
      1. Embedded defaults (Pydantic field defaults)
      2. ~/.config/ralph-workflow.toml
      3. .agent/ralph-workflow.toml (project-local)
      4. CLI flag overrides

    Args:
        config_path: Optional path to local config file. Defaults to .agent/ralph-workflow.toml.
        cli_overrides: Optional dictionary of CLI flag overrides.

    Returns:
        Validated UnifiedConfig instance.

    Raises:
        SystemExit: If configuration validation fails.
    """
    global_data = load_toml(_global_config_path())
    propagated_data: dict[str, object] = {}
    local_path = config_path or LOCAL_CONFIG_PATH
    if config_path is None:
        if workspace_scope is None:
            msg = "workspace_scope is required when config_path is not provided"
            raise ValueError(msg)
        if LOCAL_CONFIG_PATH.is_absolute():
            local_path = LOCAL_CONFIG_PATH
        else:
            local_path = workspace_scope.local_config_path
            for propagated_path in workspace_scope.propagated_config_paths:
                propagated_data = deep_merge(propagated_data, load_toml(propagated_path))
    local_data = load_toml(local_path)

    # Convert legacy config format if needed
    global_data = _convert_legacy_config(global_data)
    propagated_data = _convert_legacy_config(propagated_data)
    local_data = _convert_legacy_config(local_data)
    warn_unknown_fields(global_data, _global_config_path())
    warn_unknown_fields(local_data, local_path)

    # Merge: global -> propagated -> local
    merged = deep_merge(global_data, propagated_data)
    merged = deep_merge(merged, local_data)

    # Apply CLI overrides last
    if cli_overrides:
        merged = deep_merge(merged, cli_overrides)

    try:
        config = UnifiedConfig.model_validate(merged)
        logger.debug("Configuration validated successfully")
        return config
    except ValidationError as exc:
        logger.error(format_config_validation_error(exc, local_path))
        raise SystemExit(1) from exc


def load_local_only(config_path: Path) -> UnifiedConfig:
    """Load configuration from a specific path without merging global config.

    Args:
        config_path: Path to the configuration file.

    Returns:
        Validated UnifiedConfig instance.
    """
    data = load_toml(config_path)
    data = _convert_legacy_config(data)
    warn_unknown_fields(data, config_path)
    try:
        return UnifiedConfig.model_validate(data)
    except ValidationError as exc:
        logger.error(format_config_validation_error(exc, config_path))
        raise SystemExit(1) from exc
