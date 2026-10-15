from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import START_INTEGRATING, IntegrationRecord, write_record
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import (
    _handle_all_workers_complete,
    _handle_commit_success,
    _handle_complete,
    _handle_phase_loopback,
    _route_reduced_decision,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope


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


def test_entry_point_matrix_evaluates_consistent_phase_decision(tmp_path: Path) -> None:
    # Set up a single integration scenario with an active record on disk
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

    # 1. inspect_integration_resolution entry point
    verdict = inspect_integration_resolution(tmp_path, shared_rebase)
    assert not verdict.dispatch_allowed
    decision_inspect = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )

    # 2. _run_inner_loop entry point via synthetic _LoopContext
    class _StopInnerLoopError(Exception):
        pass

    latest_states: list[PipelineState] = []
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})
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
    decision_inner_loop = decide_phase_transition(
        st_inner, policy, integration_record=record, rebase_state=st_inner.rebase
    )

    # 3. Commit handler entry point (without calling reduce)
    st_commit, _ = _handle_commit_success(shared_state, policy)
    st_commit_routed = _route_reduced_decision(
        shared_state, st_commit, policy, integration_record=record
    )
    assert st_commit_routed.rebase.last_decision is not None
    decision_commit = PhaseDecision(st_commit_routed.rebase.last_decision)

    # 4. Cooldown handler entry point (without calling reduce)
    st_cooldown, _ = _handle_phase_loopback(shared_state, policy)
    st_cooldown_routed = _route_reduced_decision(
        shared_state, st_cooldown, policy, integration_record=record
    )
    assert st_cooldown_routed.rebase.last_decision is not None
    decision_cooldown = PhaseDecision(st_cooldown_routed.rebase.last_decision)

    # 5. Parallel-join handler entry point (without calling reduce)
    st_join, _ = _handle_all_workers_complete(shared_state, policy)
    st_join_routed = _route_reduced_decision(
        shared_state, st_join, policy, integration_record=record
    )
    assert st_join_routed.rebase.last_decision is not None
    decision_parallel_join = PhaseDecision(st_join_routed.rebase.last_decision)

    # 6. Terminal handler entry point (without calling reduce)
    terminal_state = PipelineState(phase="complete", rebase=shared_rebase)
    st_terminal, _ = _handle_complete(terminal_state, policy)
    st_terminal_routed = _route_reduced_decision(
        terminal_state, st_terminal, policy, integration_record=record
    )
    assert st_terminal_routed.rebase.last_decision is not None
    decision_terminal = PhaseDecision(st_terminal_routed.rebase.last_decision)

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

    # Terminal success eligibility is rejected while active integration record is present
    assert is_terminal_success_eligible(terminal_state, record, shared_rebase) is False
