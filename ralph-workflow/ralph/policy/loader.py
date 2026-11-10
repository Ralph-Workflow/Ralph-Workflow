"""TOML policy loader with fallback to bundled defaults.

Loads agents.toml, pipeline.toml, and artifacts.toml from the user's .agent/
config directory, falling back to the packaged defaults when files are absent.

All loading goes through Pydantic validation so any malformed config surfaces
as a PolicyValidationError with field-level detail.

User-global policy overrides prefer branded filenames
(`ralph-workflow-pipeline.toml`, `ralph-workflow-artifacts.toml`) while
still accepting the legacy unprefixed names for backward compatibility.

This module owns the layering rules -- which file wins, how a workspace
inherits, and which bundle Ralph gets back. The per-file half (reading a
policy TOML, compiling the block-authored pipeline graph, validating the
agents/pipeline/artifacts schemas) lives in
:mod:`ralph.policy._policy_validation` and is re-exported here under its
public names.
"""

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

#: Backward-compatible alias for :func:`validate_pipeline`. The public name is
#: the seam; the private spelling stays until the remaining private-attribute
#: callers move to the public name.
_validate_pipeline = validate_pipeline


_GLOBAL_POLICY_FILENAME_MAP = {
    "pipeline.toml": "ralph-workflow-pipeline.toml",
    "artifacts.toml": "ralph-workflow-artifacts.toml",
}


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


def _merge_pipeline_defaults(
    defaults: Mapping[str, object], overrides: Mapping[str, object]
) -> dict[str, object]:
    """Merge pipeline defaults while treating omitted override phases as removals.

    Older generated pipeline files are typically full phase graphs. When they
    intentionally omit a default-only phase, recursively unioning the ``phases``
    mapping revives that phase and can make the resulting graph unreachable.
    For pipeline phase overlays, preserve deep field inheritance *within* each
    declared override phase, but do not inherit default phases that the override
    omitted entirely.
    """
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
    """Merge one graph section, treating entries the override omits as removed.

    Returns ``None`` when either side does not declare the section, leaving the
    plain union in place. ``blocks`` needs the same treatment as ``phases``:
    without it a block-authored file that renames or drops a block still
    inherits every bundled block, which compiles into the graph as unreachable
    phases and fails validation on the runtime load path — while the
    ``--check-config`` path, which does not merge, reports the file as fine.
    """
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


#: Public alias: overlay merge semantics decide whether a workspace file can
#: rename or delete parts of the bundled graph, so they are worth pinning
#: directly rather than only through a full policy load.
merge_pipeline_defaults = _merge_pipeline_defaults


def _is_phase_authored_pipeline_data(data: Mapping[str, object]) -> bool:
    """Return whether a pipeline TOML uses the legacy phase-authored schema."""
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


def _declares_timebox(data: Mapping[str, object]) -> bool:
    return "cycle_timebox" in data or "development_timebox" in data


def _resolve_pipeline_data(
    *,
    default_policy_dir: Path,
    pipeline_path: Path,
    global_pipeline_path: Path | None,
    strict_optional_files: bool = False,
) -> tuple[dict[str, object], bool]:
    """Resolve merged pipeline data and whether cycle_timebox was explicit.

    Returns ``(pipeline_data, timeboxes_explicit)``. The flag is True when an
    override supplies either timebox block; otherwise inherited defaults are
    disabled when they do not fit a custom graph.
    """
    default_pipeline_data = load_toml(
        default_policy_dir / "pipeline.toml", kind="pipeline", strict=True
    )
    local_pipeline_data = load_toml(pipeline_path, kind="pipeline", strict=strict_optional_files)
    if global_pipeline_path is None:
        return local_pipeline_data or default_pipeline_data, _declares_timebox(local_pipeline_data)

    global_pipeline_data = load_toml(
        global_pipeline_path, kind="pipeline", strict=strict_optional_files
    )
    if global_pipeline_data and _is_phase_authored_pipeline_data(global_pipeline_data):
        if strict_optional_files:
            _reject_phase_authored_pipeline_override(scope="User-global", path=global_pipeline_path)
        else:
            _emit_fallback_warning(
                global_pipeline_path,
                PolicyValidationError(
                    f"User-global pipeline.toml at '{global_pipeline_path}' uses the obsolete "
                    "phase-authored schema and must not be merged with the bundled "
                    "block-authored default pipeline. Remove the outdated file so "
                    "Ralph Workflow falls back to the current default pipeline template.",
                    source="pipeline",
                ),
                kind="pipeline",
            )
            global_pipeline_data = {}

    if local_pipeline_data and _is_phase_authored_pipeline_data(local_pipeline_data):
        if strict_optional_files:
            _reject_phase_authored_pipeline_override(scope="Workspace-local", path=pipeline_path)
        else:
            _emit_fallback_warning(
                pipeline_path,
                PolicyValidationError(
                    f"Workspace-local pipeline.toml at '{pipeline_path}' uses the obsolete "
                    "phase-authored schema and must not be merged with the bundled "
                    "block-authored default pipeline. Remove the outdated file so "
                    "Ralph Workflow falls back to the current default pipeline template.",
                    source="pipeline",
                ),
                kind="pipeline",
            )
            local_pipeline_data = {}

    pipeline_data = _merge_pipeline_defaults(default_pipeline_data, global_pipeline_data)
    if local_pipeline_data:
        pipeline_data = _merge_pipeline_defaults(pipeline_data, local_pipeline_data)
    explicit = _declares_timebox(global_pipeline_data) or _declares_timebox(local_pipeline_data)
    return pipeline_data, explicit


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
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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


_DEFAULT_AGENTS_POLICY_CACHE: list[
    AgentsPolicy
] = []  # bounded-accumulator-ok: at most one cached default AgentsPolicy


def cached_default_agents_policy() -> AgentsPolicy:
    """Return the bundled default agents policy, parsed once per process.

    The default policy is the same for every workspace, so it is parsed once
    and cached; the cache holds at most one entry.
    """
    if not _DEFAULT_AGENTS_POLICY_CACHE:
        _DEFAULT_AGENTS_POLICY_CACHE.append(
            validate_agents(load_toml(default_dir() / "agents.toml", kind="agents", strict=True))
        )
    return _DEFAULT_AGENTS_POLICY_CACHE[0]


def _alias_out_of_graph_drain(
    user_chains: dict[str, AgentChainConfig],
    user_drains: dict[str, AgentDrainConfig],
    *,
    drain: str,
    drain_class: str,
    fallback_drain: str,
) -> None:
    """Bind an undeclared out-of-graph ``drain`` to a user chain, in place.

    A user's own chain of the same name wins. Otherwise the drain follows the
    chain behind ``fallback_drain`` -- the in-graph drain of the same class --
    so a user who redirected their whole pipeline to a different agent gets
    that agent for the out-of-graph policy phases too.

    ``fallback_drain`` is always same-class on purpose. The shipped pipeline
    has NO review drain (see :mod:`ralph.project_policy.remediation`), so a
    legacy ``review`` drain in a user config must never capture either policy
    phase.
    """
    if drain in user_drains:
        return
    if drain in user_chains:
        user_drains[drain] = AgentDrainConfig(chain=drain, drain_class=drain_class)
        return
    fallback_binding = user_drains.get(fallback_drain)
    if fallback_binding is not None and fallback_binding.chain in user_chains:
        user_drains[drain] = AgentDrainConfig(chain=fallback_binding.chain, drain_class=drain_class)


def _merge_agents_policy_onto_defaults(user_policy: AgentsPolicy) -> AgentsPolicy:
    """Merge a user agents policy onto the bundled defaults.

    Mirrors the pipeline/artifacts layering: user entries win per chain and
    drain name; anything not overridden keeps its bundled default. This is
    what makes internal chains (``policy_remediation``,
    ``policy_remediation_analysis``) survive user configs written before those
    chains existed.

    Before the merge, each undeclared out-of-graph policy drain is aliased to
    the chain behind the user's same-class in-graph drain. See
    :func:`_alias_out_of_graph_drain`.
    """
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
    # Auto-integration's conflict resolver runs out of graph too, and an
    # unresolved drain here does not fail loudly: the resolver simply
    # declines, every rebase conflict falls back to an abort, and
    # auto-rebase silently stops working for exactly the workspaces that
    # customised their agents. Registering the alias is what stops a
    # user-supplied agents.toml from disabling the feature by omission.
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
    """Load the agents policy, then apply any ``--developer-agent`` pin from ``config``."""
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
    """Load only the agents policy, using config synthesis when available.

    This is for call sites that need drain/chain declarations without requiring a
    full pipeline/artifact bundle.
    """
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
    """Load agents policy for a workspace with worktree-aware inheritance."""
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
) -> PolicyBundle:
    """Load a policy bundle from explicit file paths."""
    global_pipeline_path, global_artifacts_path = (
        global_policy_paths if global_policy_paths is not None else (None, None)
    )
    default_policy_dir = default_dir()
    pipeline_data, timeboxes_explicit = _resolve_pipeline_data(
        default_policy_dir=default_policy_dir,
        pipeline_path=pipeline_path,
        global_pipeline_path=global_pipeline_path,
        strict_optional_files=strict_optional_files,
    )

    default_pipeline_data = load_toml(
        default_policy_dir / "pipeline.toml", kind="pipeline", strict=True
    )
    default_pipeline_policy = validate_pipeline(default_pipeline_data, timeboxes_explicit=False)

    default_artifacts_data = load_toml(
        default_policy_dir / "artifacts.toml", kind="artifacts", strict=True
    )
    default_artifacts_policy = validate_artifacts(default_artifacts_data)

    local_artifacts_data = load_toml(artifacts_path, kind="artifacts", strict=strict_optional_files)
    if global_artifacts_path is None:
        global_artifacts_data = {}
        if local_artifacts_data:
            artifacts_data = _merge_mapping_defaults(default_artifacts_data, local_artifacts_data)
        else:
            artifacts_data = default_artifacts_data
    else:
        global_artifacts_data = load_toml(
            global_artifacts_path, kind="artifacts", strict=strict_optional_files
        )
        artifacts_data = _merge_mapping_defaults(default_artifacts_data, global_artifacts_data)
        if local_artifacts_data:
            artifacts_data = _merge_mapping_defaults(artifacts_data, local_artifacts_data)

    agents_policy = _load_agents_policy_from_path(
        agents_path, config=config, strict_optional_files=strict_optional_files
    )

    def _validate_pipeline_wrapper(data: dict[str, object]) -> PipelinePolicy:
        return validate_pipeline(data, timeboxes_explicit=timeboxes_explicit)

    pipeline_policy = validate_or_default(
        pipeline_path,
        pipeline_data,
        _validate_pipeline_wrapper,
        default_pipeline_policy,
        kind="pipeline",
        strict=strict_optional_files,
    )

    artifacts_policy = validate_or_default(
        artifacts_path,
        artifacts_data,
        validate_artifacts,
        default_artifacts_policy,
        kind="artifacts",
        strict=strict_optional_files,
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
    """Load all three policy TOML files and return a validated PolicyBundle.

    Files are loaded from ``config_dir`` (the .agent/ directory). Any absent
    or corrupt file falls back to the bundled default (no global layering).
    """
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
    """Load ``config_dir``'s policy layered like :func:`load_policy_for_workspace_scope`."""
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
    """Load policy for a workspace with worktree-aware per-file inheritance."""
    return _load_layered_policy(
        workspace_scope.resolve_agent_file,
        config,
        strict_optional_files=strict_optional_files,
    )


def default_dir() -> Path:
    """Return the path to the bundled default policy files."""
    return Path(ralph.policy.__file__).parent / "defaults"


def _global_policy_path(filename: str) -> Path:
    """Return the canonical user-global path for a runtime policy TOML."""
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
    """Load policy, exiting with a user-friendly message on failure.

    Args:
        config_dir: Path to the .agent/ configuration directory.
        config: Optional unified config.
        strict_optional_files: If True, re-raise errors instead of falling back.

    Returns:
        Validated PolicyBundle.
    """
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
