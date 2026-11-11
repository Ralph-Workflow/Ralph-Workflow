
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger
from pydantic import ValidationError

import ralph.policy
from ralph.config._developer_override import apply_developer_override
from ralph.config._toml_fallback import (
    _emit_fallback_warning,
    validate_or_default,
)
from ralph.policy._policy_validation import (
    PIPELINE_POLICY_FIELDS,
    ValidationErrorDetail,
    ValidationErrorDetails,
    disable_incompatible_inherited_cycle_timebox,
    format_validation_error_detail,
    format_validation_error_messages,
    format_validation_location,
    format_validation_message,
    load_toml,
    validate_agents,
    validate_artifacts,
    validate_pipeline,
)
from ralph.policy.conflict_chain_warnings import (
    warn_if_conflict_resolution_chain_has_no_fallback,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME
from ralph.policy.validation import (
    PolicyValidationError,
    validate_drain_contracts,
    validate_policy_completeness,
)

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "PIPELINE_POLICY_FIELDS",
    "ValidationErrorDetail",
    "ValidationErrorDetails",
    "build_agents_policy_from_config",
    "cached_default_agents_policy",
    "cached_default_artifacts_data",
    "cached_default_artifacts_policy",
    "cached_default_pipeline_data",
    "cached_default_pipeline_policy",
    "cached_default_policy_bundle",
    "default_dir",
    "disable_incompatible_inherited_cycle_timebox",
    "format_validation_error_detail",
    "format_validation_error_messages",
    "format_validation_location",
    "format_validation_message",
    "load_agents_policy",
    "load_agents_policy_for_workspace_scope",
    "load_policy",
    "load_policy_for_config_dir",
    "load_policy_for_workspace_scope",
    "load_policy_or_die",
    "load_toml",
    "merge_pipeline_defaults",
    "validate_agents",
    "validate_artifacts",
    "validate_pipeline",
]

_validate_pipeline = validate_pipeline


_GLOBAL_POLICY_FILENAME_MAP = {
    "pipeline.toml": "ralph-workflow-pipeline.toml",
    "artifacts.toml": "ralph-workflow-artifacts.toml",
}


def _merge_mapping_defaults(
    defaults: Mapping[str, object], overrides: Mapping[str, object]
) -> dict[str, object]:
    merged: dict[str, object] = dict(defaults)
    for key, override_value in overrides.items():
        default_value = merged.get(key)
        if isinstance(default_value, Mapping) and isinstance(override_value, Mapping):
            merged[key] = _merge_mapping_defaults(default_value, override_value)
            continue
        merged[key] = override_value
    return merged


def _merge_pipeline_defaults(
    defaults: Mapping[str, object], overrides: Mapping[str, object]
) -> dict[str, object]:
    merged = _merge_mapping_defaults(defaults, overrides)
    for section in ("phases", "blocks"):
        merged_section = _merge_declared_entries(defaults, overrides, section)
        if merged_section is not None:
            merged[section] = merged_section
    return merged


def _merge_declared_entries(
    defaults: Mapping[str, object],
    overrides: Mapping[str, object],
    section: str,
) -> dict[str, object] | None:
    override_entries = overrides.get(section)
    default_entries = defaults.get(section)
    if not isinstance(override_entries, Mapping) or not isinstance(default_entries, Mapping):
        return None

    merged_entries: dict[str, object] = {}
    for name, override_entry in override_entries.items():
        default_entry = default_entries.get(name)
        if isinstance(default_entry, Mapping) and isinstance(override_entry, Mapping):
            merged_entries[name] = _merge_mapping_defaults(default_entry, override_entry)
        else:
            merged_entries[name] = override_entry
    return merged_entries


merge_pipeline_defaults = _merge_pipeline_defaults


def _is_phase_authored_pipeline_data(data: Mapping[str, object]) -> bool:
    if not data:
        return False
    has_phases = isinstance(data.get("phases"), Mapping)
    has_blocks = isinstance(data.get("blocks"), Mapping)
    has_entry_block = isinstance(data.get("entry_block"), str)
    return has_phases and not has_blocks and not has_entry_block


def _reject_phase_authored_pipeline_override(*, scope: str, path: Path) -> None:
    raise PolicyValidationError(
        f"{scope} pipeline.toml at '{path}' uses the obsolete phase-authored schema and must "
        f"not be merged with the bundled block-authored default pipeline. Remove the outdated "
        f"file so Ralph Workflow falls back to the current default pipeline template.",
        source="pipeline",
    )


def _handle_phase_authored_pipeline(
    data: Mapping[str, object],
    path: Path,
    *,
    scope: str,
    strict: bool,
) -> bool:
    if not (data and _is_phase_authored_pipeline_data(data)):
        return False
    if strict:
        _reject_phase_authored_pipeline_override(scope=scope, path=path)
    _emit_fallback_warning(
        path,
        PolicyValidationError(
            f"{scope} pipeline.toml at '{path}' uses the obsolete phase-authored schema "
            "and must not be merged with the bundled block-authored default pipeline. "
            "Remove the outdated file so Ralph Workflow falls back to the current "
            "default pipeline template.",
            source="pipeline",
        ),
        kind="pipeline",
    )
    return True


def _declares_timebox(data: Mapping[str, object]) -> bool:
    return "cycle_timebox" in data or "development_timebox" in data


def _resolve_pipeline_policy(
    *,
    default_policy_dir: Path,
    pipeline_path: Path,
    global_pipeline_path: Path | None,
    strict_optional_files: bool = False,
) -> PipelinePolicy:
    default_pipeline_data = cached_default_pipeline_data()
    default_pipeline_policy = cached_default_pipeline_policy()

    if global_pipeline_path is None:
        local_pipeline_data = load_toml(
            pipeline_path, kind="pipeline", strict=strict_optional_files
        )
        if not local_pipeline_data:
            return default_pipeline_policy
        return validate_or_default(
            pipeline_path,
            local_pipeline_data,
            lambda d: validate_pipeline(
                d, timeboxes_explicit=_declares_timebox(local_pipeline_data)
            ),
            default_pipeline_policy,
            kind="pipeline",
            strict=strict_optional_files,
        )

    active_pipeline_data = default_pipeline_data
    active_pipeline_policy = default_pipeline_policy
    active_timeboxes_explicit = False

    global_pipeline_data = load_toml(
        global_pipeline_path, kind="pipeline", strict=strict_optional_files
    )
    if _handle_phase_authored_pipeline(
        global_pipeline_data,
        global_pipeline_path,
        scope="User-global",
        strict=strict_optional_files,
    ):
        global_pipeline_data = {}

    if global_pipeline_data:
        candidate_global_data = _merge_pipeline_defaults(
            default_pipeline_data, global_pipeline_data
        )
        global_timeboxes_explicit = _declares_timebox(global_pipeline_data)
        validated_global_policy = validate_or_default(
            global_pipeline_path,
            candidate_global_data,
            lambda d: validate_pipeline(d, timeboxes_explicit=global_timeboxes_explicit),
            default=None,
            kind="pipeline",
            strict=strict_optional_files,
        )
        if validated_global_policy is not None:
            active_pipeline_data = candidate_global_data
            active_pipeline_policy = validated_global_policy
            active_timeboxes_explicit = global_timeboxes_explicit

    local_pipeline_data = load_toml(pipeline_path, kind="pipeline", strict=strict_optional_files)
    if _handle_phase_authored_pipeline(
        local_pipeline_data,
        pipeline_path,
        scope="Workspace-local",
        strict=strict_optional_files,
    ):
        local_pipeline_data = {}

    if local_pipeline_data:
        candidate_local_data = _merge_pipeline_defaults(active_pipeline_data, local_pipeline_data)
        local_timeboxes_explicit = active_timeboxes_explicit or _declares_timebox(
            local_pipeline_data
        )
        validated_local_policy = validate_or_default(
            pipeline_path,
            candidate_local_data,
            lambda d: validate_pipeline(d, timeboxes_explicit=local_timeboxes_explicit),
            default=None,
            kind="pipeline",
            strict=strict_optional_files,
        )
        if validated_local_policy is not None:
            return validated_local_policy
        return active_pipeline_policy

    return active_pipeline_policy


def _resolve_artifacts_policy(
    *,
    artifacts_path: Path,
    global_artifacts_path: Path | None,
    strict_optional_files: bool = False,
) -> ArtifactsPolicy:
    default_artifacts_data = cached_default_artifacts_data()
    default_artifacts_policy = cached_default_artifacts_policy()

    active_artifacts_data = default_artifacts_data
    active_artifacts_policy = default_artifacts_policy

    if global_artifacts_path is not None:
        global_artifacts_data = load_toml(
            global_artifacts_path, kind="artifacts", strict=strict_optional_files
        )
        if global_artifacts_data:
            candidate_global_artifacts = _merge_mapping_defaults(
                default_artifacts_data, global_artifacts_data
            )
            validated_global_artifacts = validate_or_default(
                global_artifacts_path,
                candidate_global_artifacts,
                validate_artifacts,
                default=None,
                kind="artifacts",
                strict=strict_optional_files,
            )
            if validated_global_artifacts is not None:
                active_artifacts_data = candidate_global_artifacts
                active_artifacts_policy = validated_global_artifacts

    local_artifacts_data = load_toml(artifacts_path, kind="artifacts", strict=strict_optional_files)
    if local_artifacts_data:
        candidate_local_artifacts = _merge_mapping_defaults(
            active_artifacts_data, local_artifacts_data
        )
        validated_local_artifacts = validate_or_default(
            artifacts_path,
            candidate_local_artifacts,
            validate_artifacts,
            default=None,
            kind="artifacts",
            strict=strict_optional_files,
        )
        if validated_local_artifacts is not None:
            return validated_local_artifacts
        return active_artifacts_policy

    return active_artifacts_policy


def _config_defines_agent_policy(config: object) -> bool:
    chains: object = getattr(config, "agent_chains", None)
    drains: object = getattr(config, "agent_drains", None)
    return (
        isinstance(chains, Mapping)
        and isinstance(drains, Mapping)
        and bool(chains)
        and bool(drains)
    )


def _coerce_agent_chain_config(
    value: object,
    *,
    retry_budget: int,
    retry_delay_ms: int,
) -> AgentChainConfig:
    if isinstance(value, AgentChainConfig):
        return value
    return AgentChainConfig(
        agents=list(
            cast("Sequence[str]", value)
        ),
        max_retries=retry_budget,
        retry_delay_ms=retry_delay_ms,
    )


def _coerce_agent_drain_config(
    drain: str,
    value: object,
    *,
    builtin_drain_classes: Mapping[str, str],
) -> AgentDrainConfig:
    if isinstance(value, AgentDrainConfig):
        return AgentDrainConfig(
            chain=value.chain,
            drain_class=value.drain_class or builtin_drain_classes.get(drain),
            capability_class=value.capability_class,
        )
    return AgentDrainConfig(
        chain=cast(
            "str", value
        ),
        drain_class=builtin_drain_classes.get(drain),
    )


def build_agents_policy_from_config(config: UnifiedConfig) -> AgentsPolicy:
    general: object = getattr(config, "general", None)
    retry_budget_value: object = getattr(general, "max_retries", 3)
    retry_delay_ms_value: object = getattr(general, "retry_delay_ms", 1000)
    retry_budget = retry_budget_value if isinstance(retry_budget_value, int) else 3
    retry_delay_ms = retry_delay_ms_value if isinstance(retry_delay_ms_value, int) else 1000
    raw_agent_chains_obj: object = getattr(config, "agent_chains", {})
    raw_agent_chains = (
        cast("Mapping[str, object]", raw_agent_chains_obj)
        if isinstance(raw_agent_chains_obj, Mapping)
        else {}
    )
    chain_configs = {
        name: _coerce_agent_chain_config(
            chain_value,
            retry_budget=retry_budget,
            retry_delay_ms=retry_delay_ms,
        )
        for name, chain_value in raw_agent_chains.items()
    }

    builtin_drain_classes: dict[str, str] = {
        "planning": "planning",
        "development": "development",
        "development_analysis": "analysis",
        "planning_analysis": "analysis",
        "review_analysis": "analysis",
        "analysis": "analysis",
        "review": "review",
        "fix": "fix",
        "development_commit": "commit",
        "review_commit": "commit",
        "commit": "commit",
    }
    raw_agent_drains_obj: object = getattr(config, "agent_drains", {})
    raw_agent_drains = (
        cast("Mapping[str, object]", raw_agent_drains_obj)
        if isinstance(raw_agent_drains_obj, Mapping)
        else {}
    )
    drain_configs = {
        drain: _coerce_agent_drain_config(
            drain,
            drain_value,
            builtin_drain_classes=builtin_drain_classes,
        )
        for drain, drain_value in raw_agent_drains.items()
    }

    return AgentsPolicy(
        agent_chains=chain_configs,
        agent_drains=drain_configs,
    )


_DEFAULT_AGENTS_POLICY_CACHE: list[
    AgentsPolicy
] = []

_DEFAULT_PIPELINE_DATA_CACHE: list[
    dict[str, object]
] = []

_DEFAULT_PIPELINE_POLICY_CACHE: list[
    PipelinePolicy
] = []

_DEFAULT_ARTIFACTS_DATA_CACHE: list[
    dict[str, object]
] = []

_DEFAULT_ARTIFACTS_POLICY_CACHE: list[
    ArtifactsPolicy
] = []

_DEFAULT_POLICY_BUNDLE_CACHE: list[
    PolicyBundle
] = []


def cached_default_agents_policy() -> AgentsPolicy:
    if not _DEFAULT_AGENTS_POLICY_CACHE:
        _DEFAULT_AGENTS_POLICY_CACHE.append(
            validate_agents(load_toml(default_dir() / "agents.toml", kind="agents", strict=True))
        )
    return _DEFAULT_AGENTS_POLICY_CACHE[0]


def cached_default_pipeline_data() -> dict[str, object]:
    if not _DEFAULT_PIPELINE_DATA_CACHE:
        _DEFAULT_PIPELINE_DATA_CACHE.append(
            load_toml(default_dir() / "pipeline.toml", kind="pipeline", strict=True)
        )
    return _DEFAULT_PIPELINE_DATA_CACHE[0]


def cached_default_pipeline_policy() -> PipelinePolicy:
    if not _DEFAULT_PIPELINE_POLICY_CACHE:
        _DEFAULT_PIPELINE_POLICY_CACHE.append(
            validate_pipeline(cached_default_pipeline_data(), timeboxes_explicit=False)
        )
    return _DEFAULT_PIPELINE_POLICY_CACHE[0]


def cached_default_artifacts_data() -> dict[str, object]:
    if not _DEFAULT_ARTIFACTS_DATA_CACHE:
        _DEFAULT_ARTIFACTS_DATA_CACHE.append(
            load_toml(default_dir() / "artifacts.toml", kind="artifacts", strict=True)
        )
    return _DEFAULT_ARTIFACTS_DATA_CACHE[0]


def cached_default_artifacts_policy() -> ArtifactsPolicy:
    if not _DEFAULT_ARTIFACTS_POLICY_CACHE:
        _DEFAULT_ARTIFACTS_POLICY_CACHE.append(validate_artifacts(cached_default_artifacts_data()))
    return _DEFAULT_ARTIFACTS_POLICY_CACHE[0]


def cached_default_policy_bundle() -> PolicyBundle:
    if not _DEFAULT_POLICY_BUNDLE_CACHE:
        default_policy_dir = default_dir()
        bundle = _load_policy_from_paths(
            agents_path=default_policy_dir / "agents.toml",
            pipeline_path=default_policy_dir / "pipeline.toml",
            artifacts_path=default_policy_dir / "artifacts.toml",
            config=None,
            global_policy_paths=None,
            strict_optional_files=False,
            _bypass_bundle_cache=True,
        )
        _DEFAULT_POLICY_BUNDLE_CACHE.append(bundle)
    return _DEFAULT_POLICY_BUNDLE_CACHE[0]


def _alias_out_of_graph_drain(
    user_chains: dict[str, AgentChainConfig],
    user_drains: dict[str, AgentDrainConfig],
    *,
    drain: str,
    drain_class: str,
    fallback_drain: str,
) -> None:
    if drain in user_drains:
        return
    if drain in user_chains:
        user_drains[drain] = AgentDrainConfig(chain=drain, drain_class=drain_class)
        return
    fallback_binding = user_drains.get(fallback_drain)
    if fallback_binding is not None and fallback_binding.chain in user_chains:
        user_drains[drain] = AgentDrainConfig(chain=fallback_binding.chain, drain_class=drain_class)


def _merge_agents_policy_onto_defaults(user_policy: AgentsPolicy) -> AgentsPolicy:
    defaults = cached_default_agents_policy()
    user_chains = dict(user_policy.agent_chains)
    user_drains = dict(user_policy.agent_drains)
    _alias_out_of_graph_drain(
        user_chains,
        user_drains,
        drain="policy_remediation",
        drain_class="development",
        fallback_drain="development",
    )
    _alias_out_of_graph_drain(
        user_chains,
        user_drains,
        drain="policy_remediation_analysis",
        drain_class="analysis",
        fallback_drain="development_analysis",
    )
    _alias_out_of_graph_drain(
        user_chains,
        user_drains,
        drain=RESOLUTION_DRAIN_NAME,
        drain_class="development",
        fallback_drain="development",
    )
    return AgentsPolicy(
        agent_chains={**defaults.agent_chains, **user_chains},
        agent_drains={**defaults.agent_drains, **user_drains},
        forbid_sibling_drain_inference=user_policy.forbid_sibling_drain_inference,
    )


def _load_agents_policy_from_path(
    agents_path: Path,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> AgentsPolicy:
    default_policy = cached_default_agents_policy()
    if config is not None and _config_defines_agent_policy(config):
        policy = _merge_agents_policy_onto_defaults(build_agents_policy_from_config(config))
    elif agents_path.exists():
        agents_data = load_toml(agents_path, kind="agents", strict=strict_optional_files)
        if agents_data:
            policy = validate_or_default(
                agents_path,
                agents_data,
                lambda d: _merge_agents_policy_onto_defaults(validate_agents(d)),
                default_policy,
                kind="agents",
                strict=strict_optional_files,
            )
        else:
            policy = default_policy
    else:
        policy = default_policy
    return apply_developer_override(policy, config)


def load_agents_policy(
    config_dir: Path,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> AgentsPolicy:
    return _load_agents_policy_from_path(
        config_dir / "agents.toml",
        config=config,
        strict_optional_files=strict_optional_files,
    )


def load_agents_policy_for_workspace_scope(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> AgentsPolicy:
    return _load_agents_policy_from_path(
        workspace_scope.resolve_agent_file("agents.toml"),
        config=config,
        strict_optional_files=strict_optional_files,
    )


def _load_policy_from_paths(
    *,
    agents_path: Path,
    pipeline_path: Path,
    artifacts_path: Path,
    config: UnifiedConfig | None = None,
    global_policy_paths: tuple[Path | None, Path | None] | None = None,
    strict_optional_files: bool = False,
    _bypass_bundle_cache: bool = False,
) -> PolicyBundle:
    default_policy_dir = default_dir()
    global_pipeline_path, global_artifacts_path = (
        global_policy_paths if global_policy_paths is not None else (None, None)
    )
    has_global = bool(
        (global_pipeline_path is not None and global_pipeline_path.exists())
        or (global_artifacts_path is not None and global_artifacts_path.exists())
    )
    can_use_default_agents = config is None or (
        not _config_defines_agent_policy(config)
        and config.developer_agent is None
        and config.developer_model is None
    )
    if (
        not _bypass_bundle_cache
        and can_use_default_agents
        and not strict_optional_files
        and not has_global
        and (
            (
                agents_path == default_policy_dir / "agents.toml"
                and pipeline_path == default_policy_dir / "pipeline.toml"
                and artifacts_path == default_policy_dir / "artifacts.toml"
            )
            or not (agents_path.exists() or pipeline_path.exists() or artifacts_path.exists())
        )
    ):
        return cached_default_policy_bundle()

    pipeline_policy = _resolve_pipeline_policy(
        default_policy_dir=default_policy_dir,
        pipeline_path=pipeline_path,
        global_pipeline_path=global_pipeline_path,
        strict_optional_files=strict_optional_files,
    )

    artifacts_policy = _resolve_artifacts_policy(
        artifacts_path=artifacts_path,
        global_artifacts_path=global_artifacts_path,
        strict_optional_files=strict_optional_files,
    )

    agents_policy = _load_agents_policy_from_path(
        agents_path, config=config, strict_optional_files=strict_optional_files
    )

    try:
        bundle = PolicyBundle(
            agents=agents_policy,
            pipeline=pipeline_policy,
            artifacts=artifacts_policy,
        )
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise PolicyValidationError(
            "Cross-policy validation failed (drain bindings / analysis contracts):\n"
            + "\n".join(msgs),
            source=None,
        ) from exc

    try:
        validate_drain_contracts(bundle)
    except PolicyValidationError as exc:
        raise PolicyValidationError(
            exc.message,
            source="agents",
        ) from exc

    try:
        validate_policy_completeness(bundle)
    except PolicyValidationError as exc:
        raise PolicyValidationError(
            exc.message,
            source=exc.source or "completeness",
        ) from exc

    import_module("ralph.phases").register_role_handlers(pipeline_policy)
    warn_if_conflict_resolution_chain_has_no_fallback(bundle)
    return bundle


def load_policy(
    config_dir: Path,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> PolicyBundle:
    return _load_policy_from_paths(
        agents_path=config_dir / "agents.toml",
        pipeline_path=config_dir / "pipeline.toml",
        artifacts_path=config_dir / "artifacts.toml",
        config=config,
        strict_optional_files=strict_optional_files,
    )


def _load_layered_policy(
    resolve: Callable[[str], Path],
    config: UnifiedConfig | None,
    *,
    strict_optional_files: bool = False,
) -> PolicyBundle:
    return _load_policy_from_paths(
        agents_path=resolve("agents.toml"),
        pipeline_path=resolve("pipeline.toml"),
        artifacts_path=resolve("artifacts.toml"),
        config=config,
        global_policy_paths=(
            _global_policy_path("pipeline.toml"),
            _global_policy_path("artifacts.toml"),
        ),
        strict_optional_files=strict_optional_files,
    )


def load_policy_for_config_dir(
    config_dir: Path,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> PolicyBundle:
    return _load_layered_policy(
        lambda name: config_dir / name,
        config,
        strict_optional_files=strict_optional_files,
    )


def load_policy_for_workspace_scope(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> PolicyBundle:
    return _load_layered_policy(
        workspace_scope.resolve_agent_file,
        config,
        strict_optional_files=strict_optional_files,
    )


def default_dir() -> Path:
    return Path(ralph.policy.__file__).parent / "defaults"


def _global_policy_path(filename: str) -> Path:
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    base_dir = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    preferred_name = _GLOBAL_POLICY_FILENAME_MAP.get(filename, filename)
    return base_dir / preferred_name


def load_policy_or_die(
    config_dir: Path,
    config: UnifiedConfig | None = None,
    *,
    strict_optional_files: bool = False,
) -> PolicyBundle:
    try:
        try:
            if config is None:
                return load_policy(
                    config_dir,
                    strict_optional_files=strict_optional_files,
                )
            return load_policy(
                config_dir,
                config=config,
                strict_optional_files=strict_optional_files,
            )
        except TypeError:
            if config is None:
                return load_policy(config_dir)
            return load_policy(config_dir, config=config)
    except PolicyValidationError as exc:
        logger.error("Policy validation failed: {}", exc.message)
        if exc.source:
            logger.error("  Source: {}", exc.source)
        raise SystemExit(1) from exc
