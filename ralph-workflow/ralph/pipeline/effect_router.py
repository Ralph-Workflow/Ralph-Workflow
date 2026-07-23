"""Effect routing: determine which effect to apply given the current pipeline state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.operations import has_uncommitted_changes
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
)
from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_PATH,
    PlanArtifactValidationError,
    is_noop_plan,
    normalize_plan_artifact_content,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.pipeline.effects import (
    CommitEffect,
    EarlySkipCommitEffect,
    ExhaustedAnalysisPhaseAdvanceEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.handoffs import resolve_exhausted_analysis_bypass, resolve_phase_drain
from ralph.pipeline.work_units import (
    WorkUnit,
    WorkUnitsPlan,
    WorkUnitsValidationError,
    parse_work_units_from_artifact,
    validate_for_same_workspace,
)
from ralph.prompts.materialize import prompt_file_for_phase
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

MIN_WORK_UNITS_FOR_PARALLELIZATION = 2


def determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope | None = None,
    *,
    config: UnifiedConfig | None = None,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
) -> Effect:
    """Select the next pipeline effect based on current state and policy."""
    terminal = _terminal_phase_effect(state, policy_bundle.pipeline)
    if terminal is not None:
        return terminal

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return ExitFailureEffect(reason=f"Unknown phase: {state.phase}")

    exhausted = _current_analysis_phase_advance_effect(state, phase_def, policy_bundle.pipeline)
    if exhausted is not None:
        return exhausted

    if phase_def.skip_invocation is True:
        return _skip_invocation_effect(state, phase_def, policy_bundle.pipeline)

    if phase_def.role == "commit":
        scope = workspace_scope or resolve_workspace_scope()
        return _commit_phase_effect(
            state,
            policy_bundle,
            phase_def,
            scope,
            config=config,
            has_uncommitted_changes_fn=has_uncommitted_changes_fn,
        )

    return _parallel_or_agent_effect(state, phase_def, policy_bundle, config, workspace_scope)


def _skip_invocation_effect(
    state: PipelineState,
    phase_def: PhaseDefinition,
    pipeline_policy: PipelinePolicy,
) -> Effect:
    on_success = phase_def.transitions.on_success
    if on_success == pipeline_policy.terminal_phase:
        return ExitSuccessEffect()
    return PreparePromptEffect(
        phase=on_success,
        previous_phase=state.phase,
        skip_materialization=True,
    )


def _parallel_or_agent_effect(
    state: PipelineState,
    phase_def: PhaseDefinition,
    policy_bundle: PolicyBundle,
    config: UnifiedConfig | None,
    workspace_scope: WorkspaceScope | None = None,
) -> Effect:
    work_units = state.work_units
    if not work_units and phase_def.parallelization is not None:
        scope = workspace_scope or resolve_workspace_scope()
        work_units = _work_units_from_plan_artifact(scope.root)
    if len(work_units) >= MIN_WORK_UNITS_FOR_PARALLELIZATION:
        phase_para = phase_def.parallelization
        if phase_para is not None and phase_para.dispatch_mode == "agent_subagents":
            logger.warning(
                "Ralph-managed fan-out is dormant in this build; the executing AI agent is "
                "expected to dispatch its own sub-agents per the plan. The declared "
                "work_units are informational; the agent will read them as parallelization "
                "intent."
            )
            logger.debug(
                "Falling through to InvokeAgentEffect for phase={phase} (dispatch_mode="
                "agent_subagents, work_units={count})",
                phase=state.phase,
                count=len(work_units),
            )
        else:
            return _fan_out_effect(state, phase_def, work_units)
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, config=config)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for phase '{state.phase}'")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _fan_out_effect(
    state: PipelineState,
    phase_def: PhaseDefinition,
    work_units: tuple[WorkUnit, ...],
) -> Effect:
    phase_para = phase_def.parallelization
    if phase_para is None:
        return ExitFailureEffect(
            reason=(
                f"Phase {state.phase!r} does not declare parallelization but the plan "
                f"declares {len(work_units)} work_units; either declare "
                f"[phases.{state.phase}.parallelization] or remove the work_units from the plan"
            )
        )
    try:
        validate_for_same_workspace(WorkUnitsPlan(work_units=list(work_units)))
    except WorkUnitsValidationError as exc:
        offending = (
            ", ".join(u.unit_id for u in work_units if not u.allowed_directories) or "(see details)"
        )
        return ExitFailureEffect(
            reason=f"parallel preflight rejected plan: {exc} (offending units: {offending})"
        )
    return FanOutEffect(
        work_units=work_units,
        max_workers=phase_para.max_parallel_workers,
        run_post_fanout_verification=phase_para.post_fanout_verification,
        phase=str(state.phase),
    )


def _work_units_from_plan_artifact(workspace_root: Path) -> tuple[WorkUnit, ...]:
    """Best-effort read of work_units from the on-disk plan artifact.

    Returns an empty tuple when the plan is absent, a no-op, declares no
    work_units, or fails to parse. Planning-phase validation already gated
    validity, so a routing-time failure means corrupted on-disk state — the
    serial single-agent fallback preserves prior behavior in that case.
    """
    workspace = FsWorkspace(workspace_root)
    try:
        artifact_wrapper = load_phase_artifact(workspace, PLAN_ARTIFACT_PATH)
        content = unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
        if is_noop_plan(content):
            return ()
        artifact = (
            content
            if "work_units" in content and "summary" not in content
            else normalize_plan_artifact_content(content)
        )
        parsed = parse_work_units_from_artifact(artifact)
    except (
        PhaseArtifactError,
        json.JSONDecodeError,
        PlanArtifactValidationError,
        ValueError,
        WorkUnitsValidationError,
    ) as exc:
        logger.warning(
            "Could not derive work_units from plan artifact at {}: {} — "
            "falling back to serial execution",
            PLAN_ARTIFACT_PATH,
            exc,
        )
        return ()
    if parsed is None:
        return ()
    return tuple(parsed.work_units)


def _terminal_phase_effect(state: PipelineState, pipeline_policy: PipelinePolicy) -> Effect | None:
    if state.phase == pipeline_policy.terminal_phase:
        return ExitSuccessEffect()
    if state.phase == pipeline_policy.recovery.failed_route:
        return _recovery_prepare_effect(state, pipeline_policy)
    return None


def _recovery_prepare_effect(
    state: PipelineState, pipeline_policy: PipelinePolicy
) -> PreparePromptEffect:
    previous_phase = state.previous_phase if isinstance(state.previous_phase, str) else None
    failed_route = pipeline_policy.recovery.failed_route
    policy_entry_phase = (
        state.policy_entry_phase
        if isinstance(state.policy_entry_phase, str)
        else pipeline_policy.entry_phase
    )
    target_phase = previous_phase or policy_entry_phase
    if target_phase == failed_route:
        target_phase = policy_entry_phase
    drain = resolve_phase_drain(target_phase, pipeline_policy)
    return PreparePromptEffect(
        phase=target_phase,
        drain=drain,
        previous_phase=previous_phase,
    )


def _current_analysis_phase_advance_effect(
    state: PipelineState,
    phase_def: PhaseDefinition,
    pipeline_policy: PipelinePolicy,
) -> ExhaustedAnalysisPhaseAdvanceEffect | None:
    """Return a phase-advance effect when the current analysis phase is exhausted."""
    if phase_def.role != "analysis":
        return None
    bypass = resolve_exhausted_analysis_bypass(state, state.phase, pipeline_policy)
    if not bypass.skipped:
        return None
    return ExhaustedAnalysisPhaseAdvanceEffect(phase=state.phase)


def _commit_phase_effect(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    phase_def: PhaseDefinition,
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
) -> Effect:
    if state.commit.agent_invoked:
        return CommitEffect(message_file=str(workspace_scope.root / COMMIT_MESSAGE_ARTIFACT))
    if _should_early_skip_commit(
        workspace_scope.root,
        has_uncommitted_changes_fn=has_uncommitted_changes_fn,
    ):
        delete_commit_message_artifacts(workspace_scope.root)
        return EarlySkipCommitEffect()
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, config=config)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for commit phase '{state.phase}'")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _should_early_skip_commit(
    workspace_root: Path,
    *,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
) -> bool:
    try:
        return not has_uncommitted_changes_fn(workspace_root)
    except Exception:
        return False


def _agent_name_for_phase_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    *,
    config: UnifiedConfig | None = None,
) -> str | None:
    current_agent = state.current_agent()
    if current_agent is not None:
        return current_agent

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return None

    config_agents = _config_agents_for_phase(
        config,
        phase=state.phase,
        policy_drain=phase_def.drain,
    )
    if config_agents:
        return config_agents[0]

    drain_binding = policy_bundle.agents.agent_drains.get(phase_def.drain)
    if drain_binding is None:
        return None

    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
        return None

    return chain_config.agents[0]


def _agents_for_phase(
    config: UnifiedConfig,
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> list[str]:
    policy_drain: str | None = None
    if pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            policy_drain = phase_def.drain

    config_agents = _config_agents_for_phase(config, phase=phase, policy_drain=policy_drain)
    if config_agents:
        return config_agents

    if agents_policy is None or pipeline_policy is None:
        return []

    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None:
        return []

    drain_binding = agents_policy.agent_drains.get(phase_def.drain)
    if drain_binding is None:
        return []

    chain_config = agents_policy.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return []

    return list(chain_config.agents)


def _config_drain_candidates(*, phase: str, policy_drain: str | None) -> tuple[str, ...]:
    deduped: list[str] = []
    for candidate in (policy_drain, phase):
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def _config_agents_for_phase(
    config: UnifiedConfig | None,
    *,
    phase: str,
    policy_drain: str | None,
) -> list[str]:
    if config is None:
        return []

    for drain_name in _config_drain_candidates(phase=phase, policy_drain=policy_drain):
        drain_cfg = config.agent_drains.get(drain_name)
        if drain_cfg is not None:
            chain_name = drain_cfg if isinstance(drain_cfg, str) else drain_cfg.chain
            chain_cfg = config.agent_chains.get(chain_name)
            if chain_cfg is not None:
                agents = chain_cfg if isinstance(chain_cfg, list) else chain_cfg.agents
                return list(agents)
        direct_chain_cfg = config.agent_chains.get(drain_name)
        if direct_chain_cfg is not None:
            agents = (
                direct_chain_cfg if isinstance(direct_chain_cfg, list) else direct_chain_cfg.agents
            )
            return list(agents)
    return []


agents_for_phase = _agents_for_phase
config_agents_for_phase = _config_agents_for_phase
