from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop, runner
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.effects.commit_effect import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_resolution import (
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.parallel.worker_runtime import run_worker_auto_integration
from ralph.pipeline.plumbing.commit_integration import prepare_commit_integration
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, PolicyBundle
from ralph.workspace.scope import WorkspaceScope


class _StopInnerLoopError(Exception):
    pass


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def _eval_startup_inspection(
    tmp_path: Path,
    shared_rebase: RebaseState,
    shared_state: PipelineState,
    policy: PipelinePolicy,
    record: IntegrationRecord,
) -> PhaseDecision:
    verdict = inspect_integration_resolution(tmp_path, shared_rebase)
    assert not verdict.dispatch_allowed
    assert verdict.status == IntegrationResolutionStatus.RECOVERABLE
    return decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )


def _eval_phase_boundary_run_loop(
    tmp_path: Path,
    shared_state: PipelineState,
    policy: PipelinePolicy,
    record: IntegrationRecord,
    bundle: PolicyBundle,
) -> tuple[PhaseDecision, MagicMock]:
    latest_states: list[PipelineState] = []
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(tmp_path)
    ctx.config = UnifiedConfig()
    ctx.sleep = MagicMock(side_effect=_StopInnerLoopError)
    ctx.policy_bundle = bundle
    ctx.pipeline_deps = None
    ctx.latest_state = latest_states
    ctx.snapshot_registry = None

    with contextlib.suppress(_StopInnerLoopError):
        run_loop._run_inner_loop(shared_state, ctx, "development")

    st_inner: PipelineState = latest_states[0] if latest_states else shared_state
    assert st_inner.is_waiting_state is True
    assert st_inner.rebase.recovery_record_retained is True
    assert st_inner.rebase.operation_display_state == "waiting"
    return (
        decide_phase_transition(
            st_inner, policy, integration_record=record, rebase_state=st_inner.rebase
        ),
        ctx,
    )


def _eval_commit_handling(
    tmp_path: Path,
    shared_state: PipelineState,
    bundle: PolicyBundle,
) -> PhaseDecision:
    deps = MagicMock()
    deps.policy_bundle = bundle
    deps.policy_bundle_factory = None
    verdict_commit = prepare_commit_integration(
        tmp_path,
        UnifiedConfig(),
        deps,
        MagicMock(),
        MagicMock(),
        conflict_resolver=MagicMock(),
        rebase_stop_resolver=MagicMock(),
    )
    assert not verdict_commit.dispatch_allowed
    assert verdict_commit.status == IntegrationResolutionStatus.RECOVERABLE

    phase_def = PhaseDefinition(
        role="commit",
        drain="development",
        transitions=PhaseTransition(on_success="complete"),
    )
    st_commit_rebase = runner._maybe_auto_integrate(
        effect=CommitEffect(message_file="msg.txt"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=phase_def,
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(tmp_path),
        state=shared_state,
        display=MagicMock(),
    )
    assert st_commit_rebase is not None
    assert st_commit_rebase.recovery_record_retained is True
    assert st_commit_rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value
    return PhaseDecision(st_commit_rebase.last_decision)


def _eval_cooldown_recovery(
    tmp_path: Path,
    shared_state: PipelineState,
    ctx: MagicMock,
) -> PhaseDecision:
    st_recovery = recover_incomplete_integration(WorkspaceScope(tmp_path))
    assert st_recovery is not None
    assert recovery_retained_record(st_recovery) is True
    assert st_recovery.recovery_record_retained is True
    assert st_recovery.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    st_cooldown_resumed = run_loop._resume_after_cooldown_wait(
        shared_state.copy_with(is_waiting_state=True),
        ctx,
        "development",
        "all agents unavailable; waiting for cooldown expiry",
        1000,
    )
    assert not inspect_integration_resolution(tmp_path, st_cooldown_resumed.rebase).dispatch_allowed
    return PhaseDecision(st_recovery.last_decision)


def _eval_parallel_join(
    tmp_path: Path,
    shared_state: PipelineState,
    shared_rebase: RebaseState,
    bundle: PolicyBundle,
) -> PhaseDecision:
    st_worker_join = run_worker_auto_integration(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=bundle,
        registry=MagicMock(),
        pipeline_deps=None,
        display_context=MagicMock(),
        recover_first=True,
        state=shared_rebase,
    )
    assert st_worker_join is not None
    assert st_worker_join.recovery_record_retained is True
    assert st_worker_join.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    st_fanout_join = runner._integrate_after_fan_out(
        state=shared_state,
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(tmp_path),
        display=MagicMock(),
        policy_bundle=bundle,
        registry=MagicMock(),
    )
    assert st_fanout_join.rebase.recovery_record_retained is True
    assert st_fanout_join.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value
    return PhaseDecision(st_worker_join.last_decision)


def _eval_terminal_handling(
    tmp_path: Path,
    shared_rebase: RebaseState,
    policy: PipelinePolicy,
    record: IntegrationRecord,
) -> PhaseDecision:
    terminal_state = PipelineState(phase="complete", rebase=shared_rebase)
    assert is_terminal_success_eligible(terminal_state, record, shared_rebase) is False

    terminal_blocked = (
        policy.terminal_phase != terminal_state.phase
        or retained_integration_reason(tmp_path) is not None
        or not is_terminal_success_eligible(
            terminal_state,
            read_record(tmp_path),
            terminal_state.rebase,
        )
    )
    assert terminal_blocked is True
    return decide_phase_transition(
        terminal_state, policy, integration_record=record, rebase_state=terminal_state.rebase
    )


def test_entry_point_matrix_evaluates_consistent_phase_decision(tmp_path: Path) -> None:
    # Set up a single integration scenario with an active record on disk
    (tmp_path / ".git").mkdir()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    policy = _policy()
    shared_rebase = RebaseState()
    shared_state = PipelineState(phase="development", rebase=shared_rebase)

    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})

    decision_inspect = _eval_startup_inspection(
        tmp_path, shared_rebase, shared_state, policy, record
    )
    decision_inner_loop, ctx = _eval_phase_boundary_run_loop(
        tmp_path, shared_state, policy, record, bundle
    )
    decision_commit = _eval_commit_handling(tmp_path, shared_state, bundle)
    decision_cooldown = _eval_cooldown_recovery(tmp_path, shared_state, ctx)
    decision_parallel_join = _eval_parallel_join(tmp_path, shared_state, shared_rebase, bundle)
    decision_terminal = _eval_terminal_handling(tmp_path, shared_rebase, policy, record)

    decisions = [
        decision_inspect,
        decision_inner_loop,
        decision_commit,
        decision_cooldown,
        decision_parallel_join,
        decision_terminal,
    ]
    # All 6 must agree on the exact same PhaseDecision
    assert all(d == PhaseDecision.BLOCK_FOR_RECOVERY for d in decisions)
    assert len(set(decisions)) == 1
