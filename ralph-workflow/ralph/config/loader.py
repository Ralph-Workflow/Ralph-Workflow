from __future__ import annotations

import tomllib
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.config._source_migrations import remove_retired_agent_can_commit_assignments
from ralph.config._toml_fallback import (
    emit_config_failure_warning,
    load_optional_toml,
    validate_or_default,
)
from ralph.config.config_error_messages import format_config_validation_error
from ralph.pydantic_validation_errors import suggest_canonical_field

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import ValidationError

    from ralph.config.models import UnifiedConfig
    from ralph.pydantic_compat import RalphBaseModel
    from ralph.workspace.scope import WorkspaceScope

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow.toml"
GLOBAL_AGENTS_CONFIG_PATH = Path.home() / ".config" / "ralph-workflow-agents.toml"
LOCAL_CONFIG_PATH = Path(".agent") / "ralph-workflow.toml"

CLI_ONLY_CONFIG_FIELDS: frozenset[str] = frozenset({"developer_agent", "developer_model"})


def _drop_cli_only_fields(*layers: dict[str, object]) -> None:
    for data in layers:
        for field in CLI_ONLY_CONFIG_FIELDS:
            data.pop(field, None)


class ConfigLoadError(ValueError):
    def __init__(self, file: Path | str, parse_error: str | None = None) -> None:
        resolved_file = Path(file) if parse_error is not None else Path("<unknown>")
        resolved_error = parse_error if parse_error is not None else str(file)
        self.file = resolved_file
        self.parse_error = resolved_error
        super().__init__(
            f"What failed: Ralph could not read {resolved_file}: {resolved_error}.\n"
            "Why it matters: settings in a malformed file are not safe to use.\n"
            f"Fix: correct the TOML syntax in {resolved_file}, then run `ralph --check-config`."
            if parse_error is not None
            else resolved_error
        )


class _ConfigTomlError(ConfigLoadError):
    pass


ConfigTomlError = _ConfigTomlError


def deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(cast("dict[str, object]", result[key]), value)
        else:
            result[key] = value
    return result


def load_toml(path: Path) -> dict[str, object]:
    try:
        if not path.exists():
            logger.debug("Config file not found, skipping: {}", path)
            return {}
        if path.is_dir():
            raise IsADirectoryError(f"{path} is a directory, not a regular file")
        source = path.read_text(encoding="utf-8")
        data: dict[str, object] = tomllib.loads(source)
    except (
        cast("type[ValueError]", tomllib.TOMLDecodeError),
        UnicodeDecodeError,
        OSError,
    ) as exc:
        raise ConfigTomlError(path, str(exc)) from exc
    logger.debug("Loaded config from {}", path)
    migrated_source = remove_retired_agent_can_commit_assignments(source)
    if migrated_source != source:
        data = tomllib.loads(migrated_source)
        _warn_retired_agent_keys(path)
    return data


def warn_unknown_fields(data: dict[str, object], path: Path) -> None:
    for line in collect_unknown_config_fields(data, path):
        logger.warning(line)


def _format_unknown_field_message(*, full_path: str, path: Path, suggestion: str | None) -> str:
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
    from ralph.config._general_workflow_flags import GeneralWorkflowFlags
    from ralph.config.agent_config import AgentConfig
    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.general_config import GeneralConfig
    from ralph.config.models import UnifiedConfig

    closed_subtable_leaves: tuple[tuple[str, type[RalphBaseModel]], ...] = (
        ("workflow", GeneralWorkflowFlags),
    )

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

    _collect_unknown_leaves(
        data,
        known_fields=set(UnifiedConfig.model_fields) - CLI_ONLY_CONFIG_FIELDS,
        path=path,
        prefix="",
        suggester=emit,
    )

    general = data.get("general")
    if isinstance(general, dict):
        _collect_unknown_leaves(
            general,
            known_fields=set(GeneralConfig.model_fields),
            path=path,
            prefix="general.",
            suggester=emit,
        )
        for sub_name, sub_model in closed_subtable_leaves:
            sub = general.get(sub_name)
            if isinstance(sub, dict):
                _collect_unknown_leaves(
                    sub,
                    known_fields=set(sub_model.model_fields),
                    path=path,
                    prefix=f"general.{sub_name}.",
                    suggester=emit,
                )

    conflict_resolution = data.get("conflict_resolution")
    if isinstance(conflict_resolution, dict):
        _collect_unknown_leaves(
            conflict_resolution,
            known_fields=set(ConflictResolutionConfig.model_fields),
            path=path,
            prefix="conflict_resolution.",
            suggester=emit,
        )

    ccs = data.get("ccs")
    if isinstance(ccs, dict):
        from ralph.config.ccs_config import CcsConfig

        _collect_unknown_leaves(
            ccs,
            known_fields=set(CcsConfig.model_fields),
            path=path,
            prefix="ccs.",
            suggester=emit,
        )

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

    return lines


def _collect_unknown_leaves(
    data: dict[str, object],
    *,
    known_fields: set[str],
    path: Path,
    prefix: str,
    suggester: Callable[[str, str | None], None],
) -> None:
    for field in data:
        if field in known_fields:
            continue
        full_path = f"{prefix}{field}"
        suggestion = suggest_canonical_field(field, sorted(known_fields))
        suggester(full_path, suggestion)


def _convert_legacy_config(data: dict[str, object]) -> dict[str, object]:
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
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "ralph-workflow.toml"
    return GLOBAL_CONFIG_PATH


def _global_agents_config_path() -> Path:
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "ralph-workflow-agents.toml"
    return GLOBAL_AGENTS_CONFIG_PATH


def _migrate_verbosity(data: dict[str, object], general: dict[str, object]) -> None:
    if "verbosity" in data:
        general["verbosity"] = data.pop("verbosity")


def _migrate_workflow_flags(data: dict[str, object], general: dict[str, object]) -> None:
    workflow: dict[str, object] = {}
    if "checkpoint_enabled" in data:
        workflow["checkpoint_enabled"] = data.pop("checkpoint_enabled")
    if workflow:
        general["workflow"] = workflow


def _migrate_simple_fields(data: dict[str, object], general: dict[str, object]) -> None:
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
        "auto_integrate_remote_enabled",
        "auto_integrate_remote",
        "auto_integrate_remote_interval_seconds",
        "auto_integrate_reclaim_target_worktree",
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
    for key, replacement in _RETIRED_AUTO_INTEGRATE_KEYS.items():
        if key in data:
            data.pop(key)
            logger.warning(
                "`{}` is no longer supported and is ignored; use `{}` instead.", key, replacement
            )


def _warn_reserved_provider_fallback(data: dict[str, object]) -> None:
    if "provider_fallback" in data:
        logger.warning(
            "`general.provider_fallback` is accepted but read by nothing and never will be — "
            "delete it; agent fallback lives in [agent_chains] as an ordered fallback list."
        )


def _apply_cli_overrides(
    data: dict[str, object], cli_overrides: dict[str, object] | None
) -> dict[str, object]:
    return deep_merge(data, cli_overrides) if cli_overrides else data


def _validate_agents_layer(data: dict[str, object]) -> dict[str, object]:
    from ralph.config.models import UnifiedConfig

    if not data:
        return data
    cleaned = dict(data)
    _drop_cli_only_fields(cleaned)
    UnifiedConfig.model_validate(cleaned)
    return data


_MAX_RETIRED_AGENT_KEY_WARNINGS = 256
_WARNED_RETIRED_AGENT_KEYS: dict[Path, None] = {}


def reset_retired_agent_key_warnings() -> None:
    _WARNED_RETIRED_AGENT_KEYS.clear()


def _warn_retired_agent_keys(config_path: Path) -> None:
    resolved = config_path.resolve()
    if resolved in _WARNED_RETIRED_AGENT_KEYS:
        return
    _WARNED_RETIRED_AGENT_KEYS[resolved] = None
    while len(_WARNED_RETIRED_AGENT_KEYS) > _MAX_RETIRED_AGENT_KEY_WARNINGS:
        oldest = next(iter(_WARNED_RETIRED_AGENT_KEYS))
        del _WARNED_RETIRED_AGENT_KEYS[oldest]
    logger.warning(
        "Retired `agent_can_commit` key found in {}; it was migrated in memory only "
        "and the file on disk was not modified. "
        "Run `ralph --regenerate-config` or edit the file to remove it.",
        config_path,
    )


def _load_agents_data(agents_path: Path, *, strict: bool) -> dict[str, object]:
    from pydantic import ValidationError

    agents_raw = load_optional_toml(
        agents_path,
        kind="agents",
        strict=strict,
    )
    if agents_path.exists() and agents_raw:
        try:
            source = agents_path.read_text(encoding="utf-8")
            migrated_source = remove_retired_agent_can_commit_assignments(source)
            if migrated_source != source:
                migrated_data: dict[str, object] = tomllib.loads(migrated_source)
                _validate_agents_layer(migrated_data)
                _warn_retired_agent_keys(agents_path)
                agents_raw = migrated_data
        except (OSError, UnicodeDecodeError, ValidationError, ValueError):
            pass
    return validate_or_default(
        agents_path,
        agents_raw,
        _validate_agents_layer,
        default={},
        kind="agents",
        strict=strict,
    )


def _clean_general_layer(layer_data: dict[str, object]) -> None:
    general_layer = layer_data.get("general")
    if isinstance(general_layer, dict):
        _warn_and_remove_retired_auto_integrate_keys(general_layer)
        _warn_reserved_provider_fallback(general_layer)


def _load_agents_layer(agents_path: Path, *, strict: bool) -> dict[str, object]:
    from pydantic import ValidationError

    try:
        return _load_agents_data(agents_path, strict=strict)
    except ValidationError as exc:
        detail = format_config_validation_error(exc, agents_path)
        if strict:
            logger.error(detail)
            raise SystemExit(1) from exc
        emit_config_failure_warning(
            agents_path,
            kind="agents",
            error_detail=detail,
            behaviour="using built-in default",
            fix_hint="Fix the file, then run `ralph --check-config`.",
            exc=exc,
        )
        return {}


def load_config(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    workspace_scope: WorkspaceScope | None = None,
    unknown_field_warning: Callable[[str], None] | None = None,
    *,
    strict_optional_files: bool = False,
) -> UnifiedConfig:
    from pydantic import ValidationError

    from ralph.config.models import UnifiedConfig

    agents_path = _global_agents_config_path()
    agents_data = _load_agents_layer(agents_path, strict=strict_optional_files)
    global_data = load_toml(_global_config_path())
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

    global_data = _convert_legacy_config(global_data)
    local_data = _convert_legacy_config(local_data)
    for layer_data in (global_data, local_data):
        _clean_general_layer(layer_data)
    propagated_data = {}
    for index, (propagated_path, propagated_path_data) in enumerate(propagated_entries):
        converted_entry = _convert_legacy_config(propagated_path_data)
        _clean_general_layer(converted_entry)
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

    _drop_cli_only_fields(agents_data, global_data, propagated_data, local_data)

    merged = deep_merge(agents_data, global_data)
    merged = deep_merge(merged, propagated_data)
    merged = deep_merge(merged, local_data)

    merged = _apply_cli_overrides(merged, cli_overrides)

    try:
        config = UnifiedConfig.model_validate(merged)
        if config.conflict_resolution.total_resolution_cap_seconds is not None:
            logger.warning(
                "conflict_resolution.total_resolution_cap_seconds is enabled: "
                "active resolution may be stopped by the operator cap"
            )
        logger.debug("Configuration validated successfully")
        return config
    except ValidationError as exc:
        offending_path = _attribute_validation_error_to_layer(
            exc,
            local_path,
            [
                (local_path, local_data),
                *reversed(propagated_entries),
                (_global_config_path(), global_data),
                (agents_path, agents_data),
            ],
            cli_overrides,
        )
        logger.error(format_config_validation_error(exc, offending_path))
        raise SystemExit(1) from exc


def load_local_only(config_path: Path) -> UnifiedConfig:
    from pydantic import ValidationError

    from ralph.config.models import UnifiedConfig

    data = load_toml(config_path)
    data = _convert_legacy_config(data)
    general = data.get("general")
    if isinstance(general, dict):
        _warn_and_remove_retired_auto_integrate_keys(general)
        _warn_reserved_provider_fallback(general)
    warn_unknown_fields(data, config_path)
    _drop_cli_only_fields(data)
    try:
        config = UnifiedConfig.model_validate(data)
        if config.conflict_resolution.total_resolution_cap_seconds is not None:
            logger.warning(
                "conflict_resolution.total_resolution_cap_seconds is enabled: "
                "active resolution may be stopped by the operator cap"
            )
        return config
    except ValidationError as exc:
        logger.error(format_config_validation_error(exc, config_path))
        raise SystemExit(1) from exc


def _attribute_validation_error_to_layer(
    exc: ValidationError,
    local_path: Path,
    file_layers: list[tuple[Path, dict[str, object]]],
    cli_overrides: dict[str, object] | None,
) -> Path:
    details = cast("list[dict[str, object]]", exc.errors())
    blamed: set[int] = set()
    for detail in details:
        loc = cast("tuple[object, ...]", detail.get("loc") or ())
        if cli_overrides and _loc_in_data(loc, cli_overrides):
            continue
        owner = next(
            (index for index, (_path, data) in enumerate(file_layers) if _loc_in_data(loc, data)),
            None,
        )
        if owner is not None:
            blamed.add(owner)
    return file_layers[min(blamed)][0] if blamed else local_path


def _loc_in_data(loc: tuple[object, ...], data: dict[str, object]) -> bool:
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
