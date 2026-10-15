from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
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
                    on_success="review",
                    on_loopback="development",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
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
) -> tuple[PhaseDecision, run_loop._LoopContext]:
    latest_states: list[PipelineState] = []
    fake_sleep = MagicMock(side_effect=_StopInnerLoopError)
    ctx = run_loop._LoopContext(
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry={},
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=fake_sleep,
        is_quiet=True,
        latest_state=latest_states,
    )

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
    ctx: run_loop._LoopContext,
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


@pytest.mark.parametrize(
    (
        "scenario_name",
        "has_record",
        "record_intervention",
        "rebase_kwargs",
        "initial_phase",
        "expected_decision",
        "expected_terminal_eligible",
    ),
    [
        (
            "clean_active_development",
            False,
            False,
            {},
            "development",
            PhaseDecision.ADVANCE_PHASE,
            True,
        ),
        (
            "active_integration_record",
            True,
            False,
            {},
            "development",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
        (
            "diagnostic_intervention_record",
            True,
            True,
            {},
            "development",
            PhaseDecision.REPORT_INTERVENTION_REQUIRED,
            False,
        ),
        (
            "unresolved_rebase",
            False,
            False,
            {"last_outcome": RebaseOutcomeKind.CONFLICT},
            "development",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
        (
            "recovery_record_retained",
            False,
            False,
            {"recovery_record_retained": True},
            "development",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
        (
            "intervention_required_rebase",
            False,
            False,
            {"last_outcome": RebaseOutcomeKind.INTERVENTION_REQUIRED},
            "development",
            PhaseDecision.REPORT_INTERVENTION_REQUIRED,
            False,
        ),
        (
            "terminal_phase_eligible",
            False,
            False,
            {},
            "complete",
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
            True,
        ),
        (
            "terminal_phase_ineligible_due_to_record",
            True,
            False,
            {},
            "complete",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
        (
            "terminal_phase_ineligible_due_to_unresolved",
            False,
            False,
            {"last_outcome": RebaseOutcomeKind.CONFLICT},
            "complete",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
        (
            "terminal_phase_ineligible_due_to_retained",
            False,
            False,
            {"recovery_record_retained": True},
            "complete",
            PhaseDecision.BLOCK_FOR_RECOVERY,
            False,
        ),
    ],
)
def test_public_runner_run_loop_boundary_parity(
    tmp_path: Path,
    scenario_name: str,
    has_record: bool,
    record_intervention: bool,
    rebase_kwargs: dict[str, object],
    initial_phase: str,
    expected_decision: PhaseDecision,
    expected_terminal_eligible: bool,
) -> None:
    del scenario_name
    policy = _policy()
    rebase = RebaseState(**rebase_kwargs)
    state = PipelineState(phase=initial_phase, rebase=rebase)
    record: IntegrationRecord | None = None
    if has_record:
        (tmp_path / ".git").mkdir(exist_ok=True)
        record = IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="abc111",
            pre_target_sha="def222",
            engine="git-merge",
            owning_worktree=str(tmp_path),
            diagnostic_intervention_required=record_intervention,
        ).with_transition(START_INTEGRATING)
        write_record(tmp_path, record)

    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})

    # 1. Authoritative state machine decisions
    authority_decision = decide_phase_transition(
        state, policy, integration_record=record, rebase_state=rebase
    )
    assert authority_decision == expected_decision
    assert is_terminal_success_eligible(state, record, rebase) is expected_terminal_eligible

    # 2. Public run_loop boundary
    latest_states: list[PipelineState] = [state]
    fake_sleep = MagicMock(side_effect=_StopInnerLoopError)
    ctx = run_loop._LoopContext(
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry={},
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=fake_sleep,
        is_quiet=True,
        latest_state=latest_states,
    )
    # WU-G6 parity contract: the reducer is the sole last_decision writer, so
    # every public boundary must agree with the authority decision computed in
    # step 1 rather than re-asserting a run_loop-side persisted write.
    if initial_phase == "complete":
        if expected_decision == PhaseDecision.REPORT_TERMINAL_SUCCESS:
            exit_code = run_loop._execute_with_cleanup(
                state, ctx, "complete", lambda: None, lambda: None, lambda: None
            )
            assert exit_code == 0
            assert (
                ctx.latest_state[0].rebase.operation_display_state
                == "terminal"
            )
        else:
            with contextlib.suppress(_StopInnerLoopError):
                run_loop._execute_with_cleanup(
                    state, ctx, "complete", lambda: None, lambda: None, lambda: None
                )
            st_terminal = ctx.latest_state[0] if ctx.latest_state else state
            assert st_terminal.rebase.last_decision != PhaseDecision.REPORT_TERMINAL_SUCCESS.value
            assert decide_phase_transition(
                st_terminal, policy, integration_record=read_record(tmp_path),
                rebase_state=st_terminal.rebase,
            ) == expected_decision
    elif expected_decision in (
        PhaseDecision.BLOCK_FOR_RECOVERY,
        PhaseDecision.REPORT_INTERVENTION_REQUIRED,
    ):
        with contextlib.suppress(_StopInnerLoopError):
            run_loop._run_inner_loop(state, ctx, "development")
        st_inner = latest_states[0] if latest_states else state
        assert decide_phase_transition(
            st_inner, policy, integration_record=read_record(tmp_path),
            rebase_state=st_inner.rebase,
        ) == expected_decision

    # 3. Public runner boundary
    if initial_phase == "complete" and expected_terminal_eligible:
        runner_result = runner.run_pipeline_step(
            state=state,
            config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=bundle,
            registry={},
            display=MagicMock(),
            display_context=MagicMock(),
            verbosity=MagicMock(),
            pipeline_subscriber=None,
            pipeline_deps=None,
        )
        assert runner_result == 0
    elif (
        has_record
        or rebase.integration_unresolved
        or rebase.recovery_record_retained
        or rebase.is_intervention_required
    ):
        runner_result = runner.run_pipeline_step(
            state=state,
            config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=bundle,
            registry={},
            display=MagicMock(),
            display_context=MagicMock(),
            verbosity=MagicMock(),
            pipeline_subscriber=None,
            pipeline_deps=None,
        )
        assert isinstance(runner_result, PipelineState)
        assert "IntegrationDispatchBlockedError" in str(runner_result.last_error)
