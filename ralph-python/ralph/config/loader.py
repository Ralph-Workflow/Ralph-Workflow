"""Layered TOML configuration loader.

Merge order (lowest to highest priority):
  1. Embedded defaults (Pydantic field defaults)
  2. ~/.config/ralph-workflow.toml
  3. .agent/ralph-workflow.toml (project-local)
  4. CLI flag overrides

This module handles the three-layer configuration merging from the Rust implementation:
- Global config: ~/.config/ralph-workflow.toml
- Local config: .agent/ralph-workflow.toml
- CLI overrides: Applied last via dict patch before Pydantic validation
"""

from __future__ import annotations

import tomllib
from os import getenv
from pathlib import Path
from typing import cast

from loguru import logger
from pydantic import ValidationError

from ralph.config.models import UnifiedConfig

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow.toml"
LOCAL_CONFIG_PATH = Path(".agent") / "ralph-workflow.toml"


def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
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
            result[key] = _deep_merge(cast("dict[str, object]", result[key]), value)
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
        logger.debug("Loaded config from {}", path)
        return data
    except Exception as exc:
        logger.warning("Failed to parse config at {}: {}", path, exc)
        return {}


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
    _migrate_behavior_flags(data, general)
    _migrate_workflow_flags(data, general)
    _migrate_execution_flags(data, general)
    _migrate_simple_fields(data, general)
    _migrate_agent_policy_tables(data)

    if general:
        data["general"] = general

    return data


def _migrate_agent_policy_tables(data: dict[str, object]) -> None:
    """Flatten policy-style chain/drain tables into UnifiedConfig shapes."""
    chains = data.get("agent_chains")
    if isinstance(chains, dict):
        normalized_chains: dict[str, object] = {}
        for name, value in chains.items():
            if not isinstance(name, str):
                continue
            normalized_chains[name] = (
                value.get("agents", value) if isinstance(value, dict) else value
            )
        data["agent_chains"] = normalized_chains

    drains = data.get("agent_drains")
    if isinstance(drains, dict):
        normalized_drains: dict[str, object] = {}
        for name, value in drains.items():
            if not isinstance(name, str):
                continue
            normalized_drains[name] = (
                value.get("chain", value) if isinstance(value, dict) else value
            )
        data["agent_drains"] = normalized_drains


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


def _migrate_behavior_flags(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate behavior flags."""
    behavior: dict[str, object] = {}
    for field in ("interactive", "auto_detect_stack", "strict_validation"):
        if field in data:
            behavior[field] = data.pop(field)
    if behavior:
        general["behavior"] = behavior


def _migrate_workflow_flags(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate workflow flags."""
    workflow: dict[str, object] = {}
    if "checkpoint_enabled" in data:
        workflow["checkpoint_enabled"] = data.pop("checkpoint_enabled")
    if workflow:
        general["workflow"] = workflow


def _migrate_execution_flags(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate execution flags."""
    execution: dict[str, object] = {}
    for field in ("force_universal_prompt", "isolation_mode"):
        if field in data:
            execution[field] = data.pop(field)
    if execution:
        general["execution"] = execution


def _migrate_simple_fields(data: dict[str, object], general: dict[str, object]) -> None:
    """Migrate simple configuration fields."""
    simple_fields = (
        "developer_iters",
        "reviewer_reviews",
        "developer_context",
        "reviewer_context",
        "review_depth",
        "prompt_path",
        "templates_dir",
        "git_user_name",
        "git_user_email",
        "provider_fallback",
        "max_dev_continuations",
        "max_same_agent_retries",
        "max_commit_residual_retries",
        "max_retries",
        "retry_delay_ms",
        "backoff_multiplier",
        "max_backoff_ms",
        "max_cycles",
        "execution_history_limit",
    )
    for field in simple_fields:
        if field in data:
            general[field] = data.pop(field)


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
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
    local_path = config_path or LOCAL_CONFIG_PATH
    local_data = load_toml(local_path)

    # Convert legacy config format if needed
    global_data = _convert_legacy_config(global_data)
    local_data = _convert_legacy_config(local_data)

    # Merge: global -> local
    merged = _deep_merge(global_data, local_data)
    _migrate_agent_policy_tables(merged)

    # Apply CLI overrides last
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)
        _migrate_agent_policy_tables(merged)

    try:
        config = UnifiedConfig.model_validate(merged)
        logger.debug("Configuration validated successfully")
        return config
    except ValidationError as exc:
        logger.error("Configuration validation failed:\n{}", exc)
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
    try:
        return UnifiedConfig.model_validate(data)
    except ValidationError as exc:
        logger.error("Configuration validation failed:\n{}", exc)
        raise SystemExit(1) from exc
