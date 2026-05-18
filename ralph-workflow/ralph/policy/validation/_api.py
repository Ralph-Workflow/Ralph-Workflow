"""Public policy validation API functions."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph.onboarding import (
    STARTER_PROMPT_SENTINEL,
    missing_prompt_validation_hint,
    starter_prompt_validation_hint,
)
from ralph.policy.validation._checkpoint_policy_mismatch_error import (
    CheckpointPolicyMismatchError,
)
from ralph.policy.validation._phase_validators import (
    _validate_analysis_phase,
    _validate_commit_phase_loop_resets,
    _validate_commit_phase_post_commit_routes,
    _validate_parallelization_consistency,
    _validate_review_phase,
    _validate_skip_invocation_has_on_success,
    _validate_terminal_phase,
    _validate_verification_phase,
)
from ralph.policy.validation._pipeline_validators import (
    _validate_no_legacy_phase_constants,
    _validate_post_commit_routes_complete,
    _validate_reachability,
    _validate_recovery_failed_route,
    _validate_review_phase_outcome_complete,
    _validate_shared_drain_history_consistency,
    _validate_terminal_failure_phase_declared,
    _validate_tracked_counters_have_positive_max,
)
from ralph.policy.validation._policy_validation_error import PolicyValidationError, PolicyViolation

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.registry import AgentRegistry
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.work_units import WorkUnitsPlan
    from ralph.policy.models._pipeline_policy import PipelinePolicy
    from ralph.policy.models._policy_bundle import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    class _WorkUnitsModule(Protocol):
        WorkUnitsValidationError: type[Exception]
        validate_for_same_workspace: Callable[[object], object]


def validate_phase_exists_in_policy(
    phase: str,
    policy: PipelinePolicy,
) -> None:
    """Validate that a phase name is present in the current pipeline policy."""
    if phase not in policy.phases:
        raise CheckpointPolicyMismatchError(
            checkpoint_phase=phase,
            valid_phases=set(policy.phases.keys()),
        )


def validate_checkpoint_compatible(
    checkpoint_phase: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that a checkpoint phase is compatible with the current policy bundle."""
    validate_phase_exists_in_policy(checkpoint_phase, bundle.pipeline)


def validate_drain_bound(
    drain: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that a drain name has a binding in the current policy."""
    if drain not in bundle.agents.agent_drains:
        raise ValueError(
            f"Drain '{drain}' is not bound in agents.toml. "
            f"Available drains: {sorted(bundle.agents.agent_drains.keys())}"
        )


def validate_chain_exists(
    chain: str,
    bundle: PolicyBundle,
) -> None:
    """Validate that an agent chain is defined."""
    if chain not in bundle.agents.agent_chains:
        raise ValueError(
            f"Chain '{chain}' is not defined in agents.toml. "
            f"Available chains: {sorted(bundle.agents.agent_chains.keys())}"
        )


def validate_drain_contracts(bundle: PolicyBundle) -> None:
    """Validate drain contracts and enforce strict binding rules."""
    if not bundle.agents.forbid_sibling_drain_inference:
        return

    required_drains: set[str] = {
        phase_def.drain
        for phase_name, phase_def in bundle.pipeline.phases.items()
        if phase_name != bundle.pipeline.terminal_phase and phase_def.role != "terminal"
    }

    unbound_drains: list[str] = [
        drain for drain in required_drains if drain not in bundle.agents.agent_drains
    ]

    if unbound_drains:
        raise PolicyValidationError(
            f"Implicit sibling-drain inference is forbidden, but the following "
            f"pipeline drains lack explicit chain bindings: {sorted(unbound_drains)}. "
            f"Each drain used by a non-terminal pipeline phase must have an explicit "
            f"'chain' binding in agents.toml when forbid_sibling_drain_inference=true."
        )

    drains_without_class: list[str] = [
        drain
        for drain in required_drains
        if drain in bundle.agents.agent_drains
        and bundle.agents.agent_drains[drain].drain_class is None
    ]

    if drains_without_class:
        raise PolicyValidationError(
            f"Implicit sibling-drain inference is forbidden, but the following "
            f"pipeline drains have no explicit drain_class: {sorted(drains_without_class)}. "
            f"Set drain_class on each drain in agents.toml "
            f"(one of: planning, development, analysis, review, fix, commit)."
        )


def _work_units_validation_deps() -> tuple[type[Exception], Callable[[object], object]]:
    module = cast("_WorkUnitsModule", import_module("ralph.pipeline.work_units"))
    return (module.WorkUnitsValidationError, module.validate_for_same_workspace)


def validate_cli_counter_overrides(
    policy: PipelinePolicy,
    cli_counter_overrides: dict[str, int],
    errors: list[str],
) -> None:
    """Validate that every CLI counter override names a declared budget counter."""
    declared = set(policy.budget_counters.keys())
    unknown = sorted(k for k in cli_counter_overrides if k not in declared)
    if unknown:
        declared_list = sorted(declared) if declared else ["(none declared)"]
        errors.append(
            f"--counter override(s) {unknown} are not declared in pipeline.budget_counters. "
            f"Declared counters: {declared_list}. "
            f"Add [budget_counters.<name>] to pipeline.toml or remove the --counter flag."
        )


def validate_policy_completeness(
    bundle: PolicyBundle,
    *,
    cli_counter_overrides: dict[str, int] | None = None,
) -> None:
    """Validate that the policy bundle is semantically complete for policy-driven orchestration."""
    errors: list[str] = []
    policy = bundle.pipeline
    terminal_phase = policy.terminal_phase

    for phase_name, phase_def in policy.phases.items():
        if phase_name == terminal_phase or phase_def.role == "terminal":
            _validate_terminal_phase(phase_name, phase_def, errors)
            continue

        role = phase_def.role
        if role is None:
            errors.append(
                f"phases.{phase_name}: 'role' is required. "
                f"Set role='execution'|'analysis'|'review'|'commit'|'verification'|'terminal'. "
                f"Run `ralph --regenerate-config` to get an updated pipeline.toml template."
            )
            continue

        if role == "analysis":
            _validate_analysis_phase(phase_name, phase_def, bundle, errors)

        if role == "review":
            _validate_review_phase(phase_name, phase_def, errors)

        if role == "commit":
            if phase_def.commit_policy is None:
                errors.append(
                    f"phases.{phase_name}: role='commit' requires commit_policy "
                    f"(requires_artifact, increments_counter, loop_resets)"
                )
            else:
                _validate_commit_phase_loop_resets(phase_name, phase_def, policy, errors)
                _validate_commit_phase_post_commit_routes(phase_name, phase_def, policy, errors)

        if role == "verification":
            _validate_verification_phase(phase_name, phase_def, policy, errors)

        _validate_skip_invocation_has_on_success(phase_name, phase_def, errors)
        _validate_parallelization_consistency(phase_name, phase_def, errors)

    _validate_recovery_failed_route(policy, errors)
    _validate_no_legacy_phase_constants(policy, errors)
    _validate_reachability(policy, errors)
    _validate_post_commit_routes_complete(policy, errors)
    _validate_review_phase_outcome_complete(policy, errors)
    _validate_terminal_failure_phase_declared(policy, errors)
    _validate_tracked_counters_have_positive_max(policy, errors)
    _validate_shared_drain_history_consistency(policy, errors)

    if cli_counter_overrides:
        validate_cli_counter_overrides(policy, cli_counter_overrides, errors)

    if errors:
        raise PolicyValidationError(
            "Policy completeness validation failed:\n" + "\n".join(f"  {e}" for e in errors),
            source="completeness",
        )


def get_drain_resolution_matrix(bundle: PolicyBundle) -> dict[str, dict[str, str]]:
    """Generate a normalized drain resolution matrix."""
    matrix: dict[str, dict[str, str]] = {}
    for drain_name in bundle.agents.agent_drains:
        drain_config = bundle.agents.agent_drains[drain_name]
        chain_name = drain_config.chain
        chain_config = bundle.agents.agent_chains.get(chain_name)

        matrix[drain_name] = {
            "chain": chain_name,
            "agents": ",".join(chain_config.agents) if chain_config else "",
            "max_retries": str(chain_config.max_retries) if chain_config else "",
        }
    return matrix


def validate_work_units_against_policy(
    work_units: WorkUnitsPlan,
    pipeline_policy: PipelinePolicy,
    *,
    phase: str,
) -> None:
    """Validate parsed planning work_units against the active phase's parallelization policy."""
    if len(work_units.work_units) <= 1:
        return

    phase_def = pipeline_policy.phases.get(phase)
    parallel_policy = phase_def.parallelization if phase_def is not None else None

    if parallel_policy is None:
        work_units_count = len(work_units.work_units)
        raise PolicyValidationError(
            f"Phase {phase!r} does not declare parallelization but the plan declares "
            f"{work_units_count} work_units; the active transition policy must explicitly "
            f"enable same-workspace fan-out via [phases.{phase}.parallelization]"
        )

    work_units_count = len(work_units.work_units)

    if work_units_count > parallel_policy.max_work_units:
        raise PolicyViolation(
            f"work_units count {work_units_count} exceeds cap {parallel_policy.max_work_units}"
        )

    if work_units_count > parallel_policy.max_parallel_workers:
        raise PolicyValidationError(
            "Planning artifact declares "
            f"{work_units_count} work_units, exceeding "
            f"max_parallel_workers={parallel_policy.max_parallel_workers}"
        )

    if parallel_policy.require_allowed_directories:
        for unit in work_units.work_units:
            if not unit.allowed_directories:
                raise PolicyValidationError(
                    f"Work unit '{unit.unit_id}' must declare allowed_directories"
                )

    work_units_validation_error, validate_for_same_workspace = _work_units_validation_deps()
    try:
        validate_for_same_workspace(work_units)
    except work_units_validation_error as exc:
        raise PolicyValidationError(str(exc)) from exc


def validate_agent_chains_satisfiable(
    bundle: PolicyBundle,
    agent_registry: AgentRegistry,
) -> None:
    """Validate that every agent referenced in every chain exists in the registry."""
    unknown_agents: list[str] = []
    for chain_name, chain_config in bundle.agents.agent_chains.items():
        unknown_agents.extend(
            f"chain '{chain_name}' references unknown agent '{agent_name}'"
            for agent_name in chain_config.agents
            if agent_registry.get(agent_name) is None
        )
    if unknown_agents:
        raise PolicyValidationError(
            "Agent chains reference unknown agents (check configuration, not PATH): "
            + "; ".join(unknown_agents)
        )


def validate_recovery_config(bundle: PolicyBundle) -> None:
    """Validate recovery-related configuration in the policy bundle."""
    for chain_name, chain_config in bundle.agents.agent_chains.items():
        if chain_config.max_retries < 0:
            raise PolicyValidationError(
                f"Chain '{chain_name}' has invalid "
                f"max_retries={chain_config.max_retries}; must be >= 0"
            )


def validate_checkpoint_against_policy(
    state: PipelineState,
    bundle: PolicyBundle,
) -> None:
    """Validate a checkpoint state against the current policy bundle."""
    validate_phase_exists_in_policy(state.phase, bundle.pipeline)
    if state.current_drain is not None and state.current_drain not in bundle.agents.agent_drains:
        raise PolicyValidationError(
            f"Checkpoint references drain '{state.current_drain}' which is not bound "
            f"in agents.toml. Available drains: {sorted(bundle.agents.agent_drains.keys())}"
        )


def validate_required_inputs(
    workspace_scope: WorkspaceScope,
    inline_prompt: str | None = None,
) -> None:
    """Validate that required input files exist and are readable."""
    if inline_prompt is not None:
        return
    prompt_path = workspace_scope.root / "PROMPT.md"
    if not prompt_path.exists():
        raise PolicyValidationError(
            f"Required input file not found: {prompt_path}. " + missing_prompt_validation_hint()
        )
    if not prompt_path.is_file():
        raise PolicyValidationError(f"Required input is not a file: {prompt_path}")
    if not prompt_path.stat().st_size > 0:
        raise PolicyValidationError(
            f"Required input file is empty: {prompt_path}. "
            "Run `ralph --init` to scaffold a starter template, then edit it with your task."
        )
    content = prompt_path.read_text(encoding="utf-8")
    if STARTER_PROMPT_SENTINEL in content:
        raise PolicyValidationError(
            f"PROMPT.md at {prompt_path} is still the `ralph --init` starter template. "
            + starter_prompt_validation_hint()
        )
