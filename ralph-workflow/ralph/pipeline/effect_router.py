
from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.commit_review import is_recognized_secret_path
from ralph.git.operations import has_staged_changes, has_uncommitted_changes, list_changed_paths
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_artifact,
)
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_PATH
from ralph.mcp.artifacts.plan._validation import (
    is_noop_plan,
    normalize_plan_artifact_content,
)
from ralph.phases._agent_internal_paths import is_agent_internal_path
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.pipeline.effects import (
    CommitEffect,
    EmptyCommitEffect,
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
    canonical_plan_references,
    dispatchable_work_units,
    has_unextractable_work_units,
    parse_work_units_from_artifact,
)
from ralph.prompts.materialize import prompt_file_for_phase
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.effects.empty_commit_effect import EmptyCommitPhaseRole
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import (
        AgentsPolicy,
        PhaseDefinition,
        PhaseParallelization,
        PipelinePolicy,
        PolicyBundle,
    )
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope

MIN_WORK_UNITS_FOR_PARALLELIZATION = 2


def determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope | None = None,
    *,
    config: UnifiedConfig | None = None,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
    recovery: RecoveryController | None = None,
) -> Effect:
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

    if phase_def.role in ("commit", "commit_review"):
        scope = workspace_scope or resolve_workspace_scope()
        return _commit_phase_effect(
            state,
            policy_bundle,
            phase_def,
            scope,
            config=config,
            has_uncommitted_changes_fn=has_uncommitted_changes_fn,
            recovery=recovery,
        )

    return _parallel_or_agent_effect(
        state, phase_def, policy_bundle, config, workspace_scope, recovery=recovery
    )


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
    *,
    recovery: RecoveryController | None = None,
    proposal_identity: tuple[str, str] | None = None,
) -> Effect:
    work_units = state.work_units
    if not work_units and phase_def.parallelization is not None:
        scope = workspace_scope or resolve_workspace_scope()
        work_units = _work_units_from_plan_artifact(scope.root)
    dispatchable_units = dispatchable_work_units(work_units)
    phase_parallelization = phase_def.parallelization
    if (
        phase_parallelization is not None
        and len(dispatchable_units) == len(work_units)
        and len(dispatchable_units) >= MIN_WORK_UNITS_FOR_PARALLELIZATION
    ):
        if phase_parallelization.dispatch_mode == "agent_subagents":
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
            return _fan_out_effect(state, phase_parallelization, dispatchable_units)
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, recovery=recovery)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for phase '{state.phase}'")
    run_id, iteration = proposal_identity if proposal_identity is not None else ("", "")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
        run_id=run_id,
        iteration=iteration,
    )


def _fan_out_effect(
    state: PipelineState,
    phase_parallelization: PhaseParallelization,
    work_units: tuple[WorkUnit, ...],
) -> Effect:
    return FanOutEffect(
        work_units=work_units,
        max_workers=phase_parallelization.max_parallel_workers,
        run_post_fanout_verification=phase_parallelization.post_fanout_verification,
        phase=str(state.phase),
    )


def _work_units_from_plan_artifact(workspace_root: Path) -> tuple[WorkUnit, ...]:
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
    except (PhaseArtifactError, ValueError) as exc:
        logger.warning(
            "Could not derive work_units from plan artifact at {}: {} — "
            "falling back to serial execution",
            PLAN_ARTIFACT_PATH,
            exc,
        )
        return ()
    if parsed is None:
        return ()
    if has_unextractable_work_units(artifact):
        return ()
    steps, _units, owned_steps = canonical_plan_references(artifact)
    if steps != owned_steps:
        return ()
    return tuple(parsed.work_units)


def _terminal_phase_effect(state: PipelineState, pipeline_policy: PipelinePolicy) -> Effect | None:
    if state.phase in (pipeline_policy.terminal_phase,):
        return ExitSuccessEffect()
    if state.phase in (pipeline_policy.recovery.failed_route,):
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
    if phase_def.role != "analysis":
        return None
    bypass = resolve_exhausted_analysis_bypass(state, state.phase, pipeline_policy)
    if not bypass.skipped:
        return None
    return ExhaustedAnalysisPhaseAdvanceEffect(phase=state.phase)


def _commit_review_phase_effect(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    phase_def: PhaseDefinition,
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None,
    has_uncommitted_changes_fn: Callable[[Path], bool],
    recovery: RecoveryController | None,
) -> Effect:
    completed_effect = _empty_commit_review_effect(
        workspace_scope.root,
        has_uncommitted_changes_fn=has_uncommitted_changes_fn,
    )
    if completed_effect is not None:
        return completed_effect

    from ralph.phases._commit_review_events import (
        CommitReviewRejected,
    )
    from ralph.phases.commit_review import run_commit_review_stage1_preflight
    from ralph.pipeline.effects.commit_review_reject_effect import CommitReviewRejectEffect

    run_id, iteration = commit_review_proposal_identity(state)
    preflight_outcome = run_commit_review_stage1_preflight(
        workspace_scope.root,
        run_id,
        iteration,
        policy_bundle.pipeline,
        state.phase,
    )
    if isinstance(preflight_outcome, CommitReviewRejected):
        return CommitReviewRejectEffect(
            phase=state.phase,
            reason=preflight_outcome.reason,
            ref=preflight_outcome.ref,
            hard=preflight_outcome.hard,
        )

    return _parallel_or_agent_effect(
        state,
        phase_def,
        policy_bundle,
        config,
        workspace_scope,
        recovery=recovery,
        proposal_identity=(run_id, iteration),
    )


def commit_review_proposal_identity(state: PipelineState) -> tuple[str, str]:
    cycle = state.get_outer_progress("iteration") + 1
    attempt = state.commit_review_rejections + 1
    return "run", f"{cycle}-{attempt}"


def _commit_phase_effect(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    phase_def: PhaseDefinition,
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
    recovery: RecoveryController | None = None,
) -> Effect:
    if phase_def.role == "commit_review":
        return _commit_review_phase_effect(
            state,
            policy_bundle,
            phase_def,
            workspace_scope,
            config=config,
            has_uncommitted_changes_fn=has_uncommitted_changes_fn,
            recovery=recovery,
        )
    completed_effect = _empty_commit_effect(
        workspace_scope.root,
        phase_role="commit",
        has_uncommitted_changes_fn=has_uncommitted_changes_fn,
    )
    if completed_effect is not None:
        return completed_effect
    if state.commit.agent_invoked and read_commit_message_artifact(workspace_scope.root):
        return CommitEffect(
            message_file=str(workspace_scope.root / COMMIT_MESSAGE_ARTIFACT),
            expected_tree_sha=state.approved_tree_sha,
            require_approved_tree=state.commit_approval_required,
        )
    if state.commit.agent_invoked:
        logger.warning(
            "Commit invocation state has no valid durable message artifact; regenerating it"
        )
    agent_name = _agent_name_for_phase_from_policy(state, policy_bundle, recovery=recovery)
    if agent_name is None:
        return ExitFailureEffect(reason=f"No agent configured for commit phase '{state.phase}'")
    return InvokeAgentEffect(
        agent_name=agent_name,
        phase=state.phase,
        prompt_file=prompt_file_for_phase(state.phase),
        drain=phase_def.drain,
    )


def _empty_commit_review_effect(
    workspace_root: Path,
    *,
    has_uncommitted_changes_fn: Callable[[Path], bool],
) -> EmptyCommitEffect | ExitFailureEffect | None:
    try:
        has_pending_work = has_uncommitted_changes_fn(workspace_root)
    except Exception:
        return None
    if not has_pending_work:
        return EmptyCommitEffect(phase_role="commit_review")
    return None


def _empty_commit_effect(
    workspace_root: Path,
    *,
    phase_role: EmptyCommitPhaseRole = "commit",
    has_uncommitted_changes_fn: Callable[[Path], bool],
) -> EmptyCommitEffect | ExitFailureEffect | None:
    empty_commit = _is_empty_commit_phase(
        workspace_root, has_uncommitted_changes_fn=has_uncommitted_changes_fn
    )
    if empty_commit is None:
        delete_commit_message_artifacts(workspace_root)
        return ExitFailureEffect(reason="Unable to inspect effective commit work")
    if empty_commit:
        delete_commit_message_artifacts(workspace_root)
        return EmptyCommitEffect(phase_role=phase_role)
    return None


def _is_empty_commit_phase(
    workspace_root: Path,
    *,
    has_uncommitted_changes_fn: Callable[[Path], bool] = has_uncommitted_changes,
    has_staged_changes_fn: Callable[[Path], bool] = has_staged_changes,
) -> bool | None:
    try:
        fast_path = (
            has_uncommitted_changes_fn is has_uncommitted_changes
            and has_staged_changes_fn is has_staged_changes
        )
        changed_paths = list_changed_paths(workspace_root) if fast_path else None
        if changed_paths is not None:
            if not changed_paths:
                return True
        elif not has_uncommitted_changes_fn(workspace_root):
            return True

        with suppress(Exception):
            if has_staged_changes_fn(workspace_root):
                return False

        effective_changed = (
            changed_paths if changed_paths is not None else list_changed_paths(workspace_root)
        )
        return not any(
            not is_recognized_secret_path(path) and not is_agent_internal_path(path)
            for path in effective_changed
        )
    except Exception:
        return None


def _agent_name_for_phase_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    *,
    recovery: RecoveryController | None = None,
) -> str | None:
    current_agent = state.current_agent()
    if current_agent is not None and recovery is None:
        return current_agent

    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    if phase_def is None:
        return None

    drain_binding = policy_bundle.agents.agent_drains.get(phase_def.drain)
    if drain_binding is None:
        return None

    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
        return None

    if recovery is None or not hasattr(recovery, "preferred_agent_index"):
        return chain_config.agents[0]

    phase_chain = state.chain_for_phase(state.phase)
    agents = (
        phase_chain.agents
        if phase_chain is not None and phase_chain.agents
        else chain_config.agents
    )
    selection = recovery.preferred_agent_index(str(state.phase), agents)
    return selection.agent


def _agents_for_phase(
    _config: UnifiedConfig,
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> list[str]:
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


agents_for_phase = _agents_for_phase
