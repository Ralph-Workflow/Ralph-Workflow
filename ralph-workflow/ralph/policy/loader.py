"""TOML policy loader with fallback to bundled defaults.

Loads agents.toml, pipeline.toml, and artifacts.toml from the user's .agent/
config directory, falling back to the packaged defaults when files are absent.

All loading goes through Pydantic validation so any malformed config surfaces
as a PolicyValidationError with field-level detail.

User-global policy overrides prefer branded filenames
(`ralph-workflow-pipeline.toml`, `ralph-workflow-artifacts.toml`) while
still accepting the legacy unprefixed names for backward compatibility.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger
from pydantic import ValidationError

import ralph.policy
from ralph.phases import register_role_handlers
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_drain_contracts,
    validate_policy_completeness,
)

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "PolicyValidationError",
    "load_policy",
    "load_policy_or_die",
]
def _load_toml(path: Path) -> dict[str, object]:
    """Load a TOML file, returning empty dict if absent.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed TOML content or empty dict if file doesn't exist.

    Raises:
        PolicyValidationError: If TOML parsing fails.
    """
    if not path.exists():
        logger.debug("Policy file not found, using defaults: {}", path)
        return {}

    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
        logger.debug("Loaded policy from {}", path)
        return data
    except Exception as exc:
        raise PolicyValidationError(
            f"Failed to parse TOML at {path}: {exc}",
            source=str(path.name),
        ) from exc


ValidationErrorDetail = Mapping[str, object]
ValidationErrorDetails = Sequence[ValidationErrorDetail]
_GLOBAL_POLICY_FILENAME_MAP = {
    "pipeline.toml": "ralph-workflow-pipeline.toml",
    "artifacts.toml": "ralph-workflow-artifacts.toml",
}
PIPELINE_POLICY_FIELDS = frozenset(
    {
        "phases",
        "entry_phase",
        "terminal_phase",
        "loop_counters",
        "budget_counters",
        "post_commit_routes",
        "default_phase_retry_policy",
        "recovery",
    }
)


def _normalize_pipeline_data(data: dict[str, object]) -> dict[str, object]:
    """Accept legacy `[pipeline]` TOML wrappers as flat PipelinePolicy input."""
    nested_pipeline = data.get("pipeline")
    if not isinstance(nested_pipeline, Mapping):
        return data

    if PIPELINE_POLICY_FIELDS.intersection(data):
        return data

    return dict(cast("Mapping[str, object]", nested_pipeline))


def _format_validation_error_messages(exc: ValidationError) -> list[str]:
    details = cast("ValidationErrorDetails", exc.errors())
    return [_format_validation_error_detail(detail) for detail in details]


def _format_validation_error_detail(detail: ValidationErrorDetail) -> str:
    loc = detail.get("loc")
    msg = detail.get("msg")
    return f"  {_format_validation_location(loc)}: {_format_validation_message(msg)}"


def _format_validation_location(raw_loc: object | None) -> str:
    if raw_loc is None:
        return "<root>"
    if isinstance(raw_loc, list | tuple):
        if not raw_loc:
            return "<root>"
        return ".".join(str(component) for component in raw_loc)
    return str(raw_loc)


def _format_validation_message(raw_msg: object | None) -> str:
    if isinstance(raw_msg, str):
        return raw_msg
    if raw_msg is None:
        return "<missing message>"
    return str(raw_msg)


def _validate_agents(data: dict[str, object]) -> AgentsPolicy:
    """Validate and return AgentsPolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated AgentsPolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return AgentsPolicy.model_validate(data)
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "agents.toml validation failed:\n" + "\n".join(msgs),
            source="agents",
        ) from exc


def _validate_pipeline(data: dict[str, object]) -> PipelinePolicy:
    """Validate and return PipelinePolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated PipelinePolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return PipelinePolicy.model_validate(_normalize_pipeline_data(data))
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "pipeline.toml validation failed:\n" + "\n".join(msgs),
            source="pipeline",
        ) from exc


def _validate_artifacts(data: dict[str, object]) -> ArtifactsPolicy:
    """Validate and return ArtifactsPolicy.

    Args:
        data: Raw TOML dictionary.

    Returns:
        Validated ArtifactsPolicy instance.

    Raises:
        PolicyValidationError: On validation failure.
    """
    try:
        return ArtifactsPolicy.model_validate(data)
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
        raise PolicyValidationError(
            "artifacts.toml validation failed:\n" + "\n".join(msgs),
            source="artifacts",
        ) from exc


def _merge_mapping_defaults(
    defaults: Mapping[str, object], overrides: Mapping[str, object]
) -> dict[str, object]:
    """Recursively merge a project-local policy mapping onto bundled defaults.

    This preserves backward compatibility for older generated policy files that
    omit newly added fields or artifact contracts. Explicit project-local values
    still win over the bundled defaults.
    """
    merged: dict[str, object] = dict(defaults)
    for key, override_value in overrides.items():
        default_value = merged.get(key)
        if isinstance(default_value, Mapping) and isinstance(override_value, Mapping):
            merged[key] = _merge_mapping_defaults(default_value, override_value)
            continue
        merged[key] = override_value
    return merged


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
        agents=list(cast("Sequence[str]", value)),
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
        chain=cast("str", value),
        drain_class=builtin_drain_classes.get(drain),
    )


def build_agents_policy_from_config(config: UnifiedConfig) -> AgentsPolicy:
    """Synthesize the active agents policy from the main Ralph config.

    User-facing chain order and drain routing live in ``ralph-workflow.toml``.
    This helper converts the flat ``UnifiedConfig`` representation into the richer
    ``AgentsPolicy`` model used by the runtime.

    Canonical built-in drains are upgraded to explicit ``drain_class`` declarations
    here so downstream runtime code can resolve classes from policy alone without
    relying on hidden enum fallbacks.
    """
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


_DEFAULT_AGENTS_POLICY_CACHE: list[AgentsPolicy] = []


def _cached_default_agents_policy() -> AgentsPolicy:
    if not _DEFAULT_AGENTS_POLICY_CACHE:
        _DEFAULT_AGENTS_POLICY_CACHE.append(
            _validate_agents(_load_toml(_default_dir() / "agents.toml"))
        )
    return _DEFAULT_AGENTS_POLICY_CACHE[0]


def _load_agents_policy_from_path(
    agents_path: Path,
    config: UnifiedConfig | None = None,
) -> AgentsPolicy:
    agents_policy = (
        build_agents_policy_from_config(config)
        if config is not None and _config_defines_agent_policy(config)
        else None
    )
    if agents_policy is not None:
        return agents_policy

    if not agents_path.exists():
        return _cached_default_agents_policy()

    agents_data = _load_toml(agents_path)
    if not agents_data:
        return _cached_default_agents_policy()
    return _validate_agents(agents_data)


def load_agents_policy(config_dir: Path, config: UnifiedConfig | None = None) -> AgentsPolicy:
    """Load only the agents policy, using config synthesis when available.

    This is for call sites that need drain/chain declarations without requiring a
    full pipeline/artifact bundle.
    """
    return _load_agents_policy_from_path(config_dir / "agents.toml", config=config)


def load_agents_policy_for_workspace_scope(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None = None,
) -> AgentsPolicy:
    """Load agents policy for a workspace with worktree-aware inheritance."""
    return _load_agents_policy_from_path(
        workspace_scope.resolve_agent_file("agents.toml"),
        config=config,
    )


def _load_policy_from_paths(
    *,
    agents_path: Path,
    pipeline_path: Path,
    artifacts_path: Path,
    config: UnifiedConfig | None = None,
    global_policy_paths: tuple[Path | None, Path | None] | None = None,
) -> PolicyBundle:
    """Load a policy bundle from explicit file paths."""
    global_pipeline_path, global_artifacts_path = (
        global_policy_paths if global_policy_paths is not None else (None, None)
    )
    default_dir = _default_dir()
    default_pipeline_data = _load_toml(default_dir / "pipeline.toml")
    local_pipeline_data = _load_toml(pipeline_path)
    if global_pipeline_path is None:
        pipeline_data = local_pipeline_data or default_pipeline_data
    else:
        global_pipeline_data = _load_toml(global_pipeline_path)
        pipeline_data = _merge_mapping_defaults(default_pipeline_data, global_pipeline_data)
        if local_pipeline_data:
            pipeline_data = _merge_mapping_defaults(pipeline_data, local_pipeline_data)

    default_artifacts_data = _load_toml(default_dir / "artifacts.toml")
    local_artifacts_data = _load_toml(artifacts_path)
    if global_artifacts_path is None:
        if local_artifacts_data:
            artifacts_data = _merge_mapping_defaults(default_artifacts_data, local_artifacts_data)
        else:
            artifacts_data = default_artifacts_data
    else:
        global_artifacts_data = _load_toml(global_artifacts_path)
        artifacts_data = _merge_mapping_defaults(default_artifacts_data, global_artifacts_data)
        if local_artifacts_data:
            artifacts_data = _merge_mapping_defaults(artifacts_data, local_artifacts_data)

    agents_policy = _load_agents_policy_from_path(agents_path, config=config)
    pipeline_policy = _validate_pipeline(pipeline_data)
    artifacts_policy = _validate_artifacts(artifacts_data)

    try:
        bundle = PolicyBundle(
            agents=agents_policy,
            pipeline=pipeline_policy,
            artifacts=artifacts_policy,
        )
    except ValidationError as exc:
        msgs = _format_validation_error_messages(exc)
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

    register_role_handlers(pipeline_policy)
    return bundle


def load_policy(config_dir: Path, config: UnifiedConfig | None = None) -> PolicyBundle:
    """Load all three policy TOML files and return a validated PolicyBundle.

    Files are loaded from ``config_dir`` (the .agent/ directory). Any absent
    file is silently replaced with the bundled default.
    """
    return _load_policy_from_paths(
        agents_path=config_dir / "agents.toml",
        pipeline_path=config_dir / "pipeline.toml",
        artifacts_path=config_dir / "artifacts.toml",
        config=config,
    )


def load_policy_for_workspace_scope(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None = None,
) -> PolicyBundle:
    """Load policy for a workspace with worktree-aware per-file inheritance."""
    return _load_policy_from_paths(
        agents_path=workspace_scope.resolve_agent_file("agents.toml"),
        pipeline_path=workspace_scope.resolve_agent_file("pipeline.toml"),
        artifacts_path=workspace_scope.resolve_agent_file("artifacts.toml"),
        config=config,
        global_policy_paths=(
            _global_policy_path("pipeline.toml"),
            _global_policy_path("artifacts.toml"),
        ),
    )


def _default_dir() -> Path:
    """Return the path to the bundled default policy files."""
    return Path(ralph.policy.__file__).parent / "defaults"


def _global_policy_path(filename: str) -> Path:
    """Return the effective user-global policy path for a runtime policy TOML."""
    xdg_config_home = getenv("XDG_CONFIG_HOME")
    base_dir = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    preferred_name = _GLOBAL_POLICY_FILENAME_MAP.get(filename, filename)
    preferred_path = base_dir / preferred_name
    if preferred_path.exists():
        return preferred_path

    legacy_path = base_dir / filename
    if legacy_path.exists():
        return legacy_path

    return preferred_path


def load_policy_or_die(config_dir: Path, config: UnifiedConfig | None = None) -> PolicyBundle:
    """Load policy, exiting with a user-friendly message on failure.

    Args:
        config_dir: Path to the .agent/ configuration directory.

    Returns:
        Validated PolicyBundle.
    """
    try:
        if config is None:
            return load_policy(config_dir)
        return load_policy(config_dir, config=config)
    except PolicyValidationError as exc:
        logger.error("Policy validation failed: {}", exc.message)
        if exc.source:
            logger.error("  Source: {}", exc.source)
        raise SystemExit(1) from exc
