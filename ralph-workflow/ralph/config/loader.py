"""Layered TOML configuration loader.

Merge order (lowest to highest priority):
  1. Embedded defaults (Pydantic field defaults)
  2. ~/.config/ralph-workflow-agents.toml (agent CLI definitions)
  3. ~/.config/ralph-workflow.toml (or $XDG_CONFIG_HOME/ralph-workflow.toml)
  4. .agent/ralph-workflow.toml (project-local)
  5. CLI flag overrides

This module handles the layered configuration merge:

- Embedded defaults provide the baseline for every field.
- The agents file supplies ``[agents.*]`` transport plumbing, kept out of the
  main config so that file opens on the ``[agent_chains]`` operators edit. It
  sits BELOW the main config so an ``[agents.*]`` table left in an existing
  ``ralph-workflow.toml`` keeps winning and no upgrade loses a customization.
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

from ralph.config._general_workflow_flags import GeneralWorkflowFlags
from ralph.config.agent_config import AgentConfig
from ralph.config.config_error_messages import format_config_validation_error
from ralph.config.general_config import GeneralConfig
from ralph.config.models import UnifiedConfig
from ralph.pydantic_validation_errors import suggest_canonical_field

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pydantic_compat import RalphBaseModel
    from ralph.workspace.scope import WorkspaceScope

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow.toml"
GLOBAL_AGENTS_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow-agents.toml"
LOCAL_CONFIG_PATH = Path(".agent") / "ralph-workflow.toml"

# Open user-key maps whose IMMEDIATE child KEYS are user-defined.  We do
# NOT warn on the child key names (they are chain/alias/agent names the
# operator chose) but we DO recurse into the leaf fields underneath them.
# Each entry is (mapping name, leaf-fields model class) where the model
# class is None when the leaves are free-form (e.g. CCS alias table).
_USER_KEYED_TABLES: tuple[tuple[str, type[object] | None], ...] = (
    ("agents", AgentConfig),
    ("ccs_aliases", None),
    ("agent_chains", None),
    ("agent_drains", None),
)

# Closed nested subtables whose field names we DO want to detect typos on.
# ``general.workflow`` carries specific leaf fields, so a misspelled leaf is a
# real bug the operator probably wants to see.
# Reference the classes directly so the Any-free ``RalphBaseModel`` facade
# in ``ralph.pydantic_compat`` does not force a type-ignore suppression here.
_CLOSED_SUBTABLE_LEAVES: tuple[tuple[str, type[RalphBaseModel]], ...] = (
    ("workflow", GeneralWorkflowFlags),
)


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
    except cast(
        "type[ValueError]", tomllib.TOMLDecodeError
    ) as exc:  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        raise ConfigTomlError(
            f"What failed: Ralph could not read {path}: {exc}.\n"
            "Why it matters: settings in a malformed file are not safe to use.\n"
            f"Fix: correct the TOML syntax in {path}, then run `ralph --check-config`."
        ) from exc
    logger.debug("Loaded config from {}", path)
    return data


def warn_unknown_fields(data: dict[str, object], path: Path) -> None:
    """Emit warning logs for every misspelled field discovered under *data*.

    Walks the closed main-config schema (general, ccs),
    the open user-keyed maps (agents, ccs_aliases, agent_chains,
    agent_drains) at one level of depth, and the leaf fields under each
    user-defined agent name. Top-level typos are caught, nested typos
    like ``general.wrokflow`` and ``general.workflow.checkpont_enabled``
    are caught, and user-defined names like ``[agent_chains.planning]``
    or ``[agents.claude]`` are NOT warned about.

    Args:
        data: The parsed TOML data to inspect.
        path: The TOML file path (used in the warning message).
    """
    for line in collect_unknown_config_fields(data, path):
        logger.warning(line)


def _format_unknown_field_message(*, full_path: str, path: Path, suggestion: str | None) -> str:
    """Build a what/why/fix line for a single unknown field.

    Args:
        full_path: The dotted field path, e.g. ``"general.wrokflow"``.
        path: The TOML file the field was found in.
        suggestion: The closest canonical field name, or ``None``.

    Returns:
        A formatted single-line message mirroring the
        ``ConfigTomlError`` envelope.
    """
    base = (
        f"What failed: unknown setting `{full_path}` in {path}. "
        "Why it matters: Ralph ignores keys it does not recognize, so your "
        "change silently does nothing. "
        f"Fix: correct the key in {path}"
    )
    if suggestion is not None:
        base = f"{base}; did you mean `{suggestion}`?"
    base = f"{base}, then run `ralph --check-config`."
    return base


def collect_unknown_config_fields(data: dict[str, object], path: Path) -> list[str]:
    """Return formatted what/why/fix lines for every unknown field under *data*.

    This is the pure collector used by ``ralph --diagnose`` to surface
    configuration typos without requiring a side-effecting log sink. The
    returned lines are newline-free single strings; the caller decides
    how to render them.

    Args:
        data: The parsed TOML data to inspect.
        path: The TOML file path (used in the message body).

    Returns:
        A list of formatted human-readable lines, one per unknown field.
        Returns an empty list when the config is fully clean.
    """
    lines: list[str] = []
    seen: set[tuple[str, Path]] = set()

    def emit(full_path: str, suggestion: str | None) -> None:
        key = (full_path, path)
        if key in seen:
            return
        seen.add(key)
        lines.append(
            _format_unknown_field_message(full_path=full_path, path=path, suggestion=suggestion)
        )

    # 1. Top-level closed keys
    _collect_unknown_leaves(
        data,
        known_fields=set(UnifiedConfig.model_fields),
        path=path,
        prefix="",
        suggester=emit,
    )

    # 2. Closed nested subtables (general, ccs)
    general = data.get("general")
    if isinstance(general, dict):
        _collect_unknown_leaves(
            general,
            known_fields=set(GeneralConfig.model_fields),
            path=path,
            prefix="general.",
            suggester=emit,
        )
        # Recurse into the closed sub-subtables declared in
        # ``_CLOSED_SUBTABLE_LEAVES`` so a typo like
        # ``general.workflow.checkpont_enabled`` is caught.
        for sub_name, sub_model in _CLOSED_SUBTABLE_LEAVES:
            sub = general.get(sub_name)
            if isinstance(sub, dict):
                _collect_unknown_leaves(
                    sub,
                    known_fields=set(sub_model.model_fields),
                    path=path,
                    prefix=f"general.{sub_name}.",
                    suggester=emit,
                )

    ccs = data.get("ccs")
    if isinstance(ccs, dict):
        from ralph.config.ccs_config import CcsConfig  # local import: avoid cycle at import time

        _collect_unknown_leaves(
            ccs,
            known_fields=set(CcsConfig.model_fields),
            path=path,
            prefix="ccs.",
            suggester=emit,
        )

    # 3. Open user-keyed maps: keys are operator-defined; leaves under
    #    each key are still validated against the closed leaf schema.
    agents = data.get("agents")
    if isinstance(agents, dict):
        for name, agent in agents.items():
            if isinstance(name, str) and isinstance(agent, dict):
                _collect_unknown_leaves(
                    agent,
                    known_fields=set(AgentConfig.model_fields),
                    path=path,
                    prefix=f"agents.{name}.",
                    suggester=emit,
                )

    # 4. agent_chains and agent_drains carry rich shapes (lists of
    #    agents, or {chain: str} dicts). Their leaves are NOT a closed
    #    model — accept anything — but we still want to warn if the
    #    operator typo'd a chain/drain entry itself. We treat each
    #    chain/drain name as a user-defined key and stop there.
    #    (No leaf warnings for these tables by design.)

    return lines


def _collect_unknown_leaves(
    data: dict[str, object],
    *,
    known_fields: set[str],
    path: Path,
    prefix: str,
    suggester: Callable[[str, str | None], None],
) -> None:
    """Emit one warning line for every key in *data* not in *known_fields*."""
    for field in data:
        if field in known_fields:
            continue
        full_path = f"{prefix}{field}"
        suggestion = suggest_canonical_field(field, sorted(known_fields))
        suggester(full_path, suggestion)


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


def _global_agents_config_path() -> Path:
    """Resolve the agent-definitions config path, honoring XDG_CONFIG_HOME."""
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "ralph-workflow-agents.toml"
    return GLOBAL_AGENTS_CONFIG_PATH


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
        "developer_iters", "developer_context", "prompt_path", "templates_dir",
        "git_user_name", "git_user_email", "provider_fallback", "max_same_agent_retries",
        "max_commit_residual_retries", "max_retries", "retry_delay_ms", "backoff_multiplier",
        "max_backoff_ms", "max_cycles", "execution_history_limit", "auto_integrate_enabled",
        "auto_integrate_target", "auto_integrate_remote_enabled", "auto_integrate_remote",
    )
    for field in simple_fields:
        if field in data:
            general[field] = data.pop(field)


_RETIRED_AUTO_INTEGRATE_KEYS: dict[str, str] = {
    "auto_integrate_fetch_enabled": "auto_integrate_remote_enabled",
    "auto_integrate_push_enabled": "auto_integrate_remote_enabled",
    "auto_integrate_remote_sync_enabled": "auto_integrate_remote_enabled",
    "auto_integrate_remote_target": "auto_integrate_remote",
    "auto_integrate_fetch_timeout_seconds": "FETCH_TIMEOUT_SECONDS",
    "auto_integrate_push_timeout_seconds": "PUSH_TIMEOUT_SECONDS",
    "auto_integrate_resolve_timeout_seconds": "RESOLVE_TIMEOUT_SECONDS",
    "auto_integrate_remote_sync_interval_seconds": "REMOTE_SYNC_INTERVAL_SECONDS",
    "auto_integrate_remote_backoff_max_seconds": "REMOTE_BACKOFF_MAX_SECONDS",
    "auto_integrate_remote_wait_seconds": "removed without replacement",
}


def _warn_and_remove_retired_auto_integrate_keys(data: dict[str, object]) -> None:
    """Warn once per retired key in a source layer, then ignore it."""
    for key, replacement in _RETIRED_AUTO_INTEGRATE_KEYS.items():
        if key in data:
            data.pop(key)
            logger.warning(
                "`{}` is no longer supported and is ignored; use `{}` instead.", key, replacement
            )

def _warn_reserved_provider_fallback(data: dict[str, object]) -> None:
    """Explain the legacy knob when a user actually sets it."""
    if "provider_fallback" in data:
        logger.warning(
            "`general.provider_fallback` is accepted but read by nothing and never will be — "
            "delete it; agent fallback lives in [agent_chains] as an ordered fallback list."
        )


def _apply_cli_overrides(
    data: dict[str, object], cli_overrides: dict[str, object] | None
) -> dict[str, object]:
    """Apply optional highest-precedence CLI settings."""
    return deep_merge(data, cli_overrides) if cli_overrides else data


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    workspace_scope: WorkspaceScope | None = None,
    unknown_field_warning: Callable[[str], None] | None = None,
) -> UnifiedConfig:
    """Build merged UnifiedConfig from all layers.

    Merge order (lowest to highest priority):
      1. Embedded defaults (Pydantic field defaults)
      2. ~/.config/ralph-workflow-agents.toml (agent CLI definitions)
      3. ~/.config/ralph-workflow.toml
      4. .agent/ralph-workflow.toml (project-local)
      5. CLI flag overrides

    Args:
        config_path: Optional path to local config file. Defaults to .agent/ralph-workflow.toml.
        cli_overrides: Optional dictionary of CLI flag overrides.

    Returns:
        Validated UnifiedConfig instance.

    Raises:
        SystemExit: If configuration validation fails.
    """
    agents_path = _global_agents_config_path()
    agents_data = load_toml(agents_path)
    global_data = load_toml(_global_config_path())
    # Track each propagated path's data separately so warn_unknown_fields
    # can name the exact source file when a typo is found in an inherited
    # config. The merged ``propagated_data`` below stays aggregated for
    # the effective-config merge; the per-path list is only for warnings.
    propagated_entries: list[tuple[Path, dict[str, object]]] = []
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
                propagated_data_for_path = load_toml(propagated_path)
                propagated_entries.append((propagated_path, propagated_data_for_path))
                propagated_data = deep_merge(propagated_data, propagated_data_for_path)
    local_data = load_toml(local_path)

    # Convert legacy config format and discard retired keys at their original
    # source layer, so each warning names one file and never becomes unknown.
    global_data = _convert_legacy_config(global_data)
    local_data = _convert_legacy_config(local_data)
    for layer_data in (global_data, local_data):
        general_layer = layer_data.get("general")
        if isinstance(general_layer, dict):
            _warn_and_remove_retired_auto_integrate_keys(general_layer)
            _warn_reserved_provider_fallback(general_layer)
    propagated_data = {}
    for index, (propagated_path, propagated_path_data) in enumerate(propagated_entries):
        converted_entry = _convert_legacy_config(propagated_path_data)
        general_layer = converted_entry.get("general")
        if isinstance(general_layer, dict):
            _warn_and_remove_retired_auto_integrate_keys(general_layer)
            _warn_reserved_provider_fallback(general_layer)
        propagated_entries[index] = (propagated_path, converted_entry)
        propagated_data = deep_merge(propagated_data, converted_entry)
    warning_layers = [
        (agents_data, agents_path),
        (global_data, _global_config_path()),
        *((data, path) for path, data in propagated_entries),
        (local_data, local_path),
    ]
    for layer_data, layer_path in warning_layers:
        warn_unknown_fields(layer_data, layer_path)
        if unknown_field_warning is not None:
            for warning in collect_unknown_config_fields(layer_data, layer_path):
                unknown_field_warning(warning)

    # Merge: agents -> global -> propagated -> local. The agents file sits
    # lowest so an [agents.*] table still present in an operator's existing
    # ralph-workflow.toml keeps its precedence across the upgrade.
    merged = deep_merge(agents_data, global_data)
    merged = deep_merge(merged, propagated_data)
    merged = deep_merge(merged, local_data)

    # Apply CLI overrides last
    merged = _apply_cli_overrides(merged, cli_overrides)

    try:
        config = UnifiedConfig.model_validate(merged)
        logger.debug("Configuration validated successfully")
        return config
    except ValidationError as exc:
        offending_path = _attribute_validation_error_to_layer(
            exc,
            local_path,
            local_data,
            _global_config_path(),
            global_data,
            propagated_entries,
            cli_overrides,
        )
        logger.error(format_config_validation_error(exc, offending_path))
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
    general = data.get("general")
    if isinstance(general, dict):
        _warn_and_remove_retired_auto_integrate_keys(general)
        _warn_reserved_provider_fallback(general)
    warn_unknown_fields(data, config_path)
    try:
        return UnifiedConfig.model_validate(data)
    except ValidationError as exc:
        logger.error(format_config_validation_error(exc, config_path))
        raise SystemExit(1) from exc


def _attribute_validation_error_to_layer(
    exc: ValidationError,
    local_path: Path,
    local_data: dict[str, object],
    global_path: Path,
    global_data: dict[str, object],
    propagated_entries: list[tuple[Path, dict[str, object]]],
    cli_overrides: dict[str, object] | None,
) -> Path:
    """Return the config file that actually owns a failing field.

    Walks every ValidationError ``loc`` tuple and asks: which layer
    actually set that field? Layer precedence (highest first):
    CLI overrides > project-local > propagated > user-global > defaults.
    The first layer that sets the failing field is the one to blame,
    so the operator does not get sent to edit the wrong file.

    When the bad value is set in multiple layers (the merge result
    carries the highest-precedence layer's value but the bad value
    ALSO appears in a lower layer), the message names the highest
    layer that contributed the bad value -- if the layer attribution
    is ambiguous across errors, we fall back to the most-frequently
    named layer, and as a last resort the project-local path.
    """
    details = cast("list[dict[str, object]]", exc.errors())
    if not details:
        return local_path

    # Highest-precedence layer first; each entry is (layer_name, path).
    layer_paths: list[tuple[str, Path | None]] = [
        ("cli", None),  # CLI overrides are not a file
    ]
    layer_paths.append(("local", local_path))
    for prop_path, _ in propagated_entries:
        layer_paths.append(("propagated", prop_path))
    layer_paths.append(("global", global_path))

    votes: dict[str, int] = {}
    for detail in details:
        loc = cast("tuple[object, ...]", detail.get("loc") or ())
        layer = _layer_for_loc(
            loc,
            local_data,
            global_data,
            propagated_entries,
            cli_overrides,
        )
        votes[layer] = votes.get(layer, 0) + 1

    # Pick the highest-precedence layer that was voted for at all.
    for name, path in layer_paths:
        if votes.get(name, 0) > 0 and path is not None:
            return path

    return local_path


def _layer_for_loc(
    loc: tuple[object, ...],
    local_data: dict[str, object],
    global_data: dict[str, object],
    propagated_entries: list[tuple[Path, dict[str, object]]],
    cli_overrides: dict[str, object] | None,
) -> str:
    """Return the layer name that owns ``loc`` (highest precedence first)."""
    if cli_overrides:
        dotted = ".".join(str(part) for part in loc if part)
        top = loc[0] if loc else ""
        if dotted in cli_overrides or top in cli_overrides:
            return "cli"
    if _loc_in_data(loc, local_data):
        return "local"
    for _prop_path, prop_data in propagated_entries:
        if _loc_in_data(loc, prop_data):
            return "propagated"
    if _loc_in_data(loc, global_data):
        return "global"
    return "local"  # ambiguous: blame the highest-precedence file


def _loc_in_data(loc: tuple[object, ...], data: dict[str, object]) -> bool:
    """Return True when the dotted ``loc`` is present anywhere in ``data``."""
    if not loc:
        return False
    current: object = data
    for part in loc:
        if not isinstance(current, dict):
            return False
        if part not in current:
            return False
        current = current[part]
    return True
