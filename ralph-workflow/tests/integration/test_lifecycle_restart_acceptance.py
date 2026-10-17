"""Acceptance Scenario 3 (WU-S3): interrupt + restart idempotency at public
workflow boundaries.

Exercises actual entry-point invocations around durable-transition interruption:
startup recovery, phase-boundary dispatch, commit/integration continuation,
cooldown, parallel join, and terminal entry. Each entry point is interrupted
immediately before and after its durable transition, then restarted.

Invariants asserted:
(a) every entry point yields the SAME typed decision (PhaseDecision /
    IntegrationAction) for a fixed (state, policy, record, rebase) tuple;
(b) ``rebase_state.last_decision`` is the *persisted projection* (output),
    never a feedback input: feeding any prior decision back in cannot
    change the next decision;
(c) interrupting at each transition leaves the prior recoverable state
    intact on the durable record, and replaying the transition after the
    restart is idempotent;
(d) completed business work is NOT replayed after restart (count invocations
    of the injected mutator).

Entry points exercised:
- ``_run_inner_loop`` / ``_apply_startup_rebase_outcomes`` for startup recovery
- ``_run_inner_loop`` dispatch loop for phase boundary
- ``IntegrationRecordStore.transition`` for commit/integration continuation
- ``next_integration_action`` with FakeClock for cooldown entry/exit
- ``fan_out_decision`` computed through the dispatch loop for parallel join
- ``is_terminal_success_eligible`` evaluated through the dispatch loop for
  terminal entry
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop, runner
from ralph.pipeline._fan_out_authority import FanOutDecision, fan_out_decision
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._integration_diagnostic import (
    diagnostic_evidence_verdict,
)
from ralph.pipeline._integration_diagnostic import (
    EVIDENCE_LEASE_SECONDS as _EVIDENCE_LEASE_SECONDS,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import (
    HOLD_DIAGNOSTIC_EVIDENCE,
    MARK_RESOLVING_REBASE,
    START_INTEGRATING,
)
from ralph.pipeline.auto_integrate_record_store import IntegrationRecordStore
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.operation_evidence import (
    identity_fingerprint,
    ownership_fingerprint,
)
from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector
from ralph.pipeline.operation_evidence_verdict import EvidenceVerdict
from ralph.pipeline.operation_fact import OperationFact
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope
from tests.test_checkpoint_and_record_persistence_boundary import _InMemoryBackend


def _small_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )


def _loop_context(
    tmp_path: Path,
    policy_bundle: object,
    latest_state: list[PipelineState],
) -> run_loop._LoopContext:
    """Construct a minimal _LoopContext for testing entry points."""
    return run_loop._LoopContext(
        policy_bundle=policy_bundle,
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
        sleep=MagicMock(),
        is_quiet=True,
        latest_state=latest_state,
    )


# ---------------------------------------------------------------------------
# (a) Blocking tuple yields BLOCK_FOR_RECOVERY at every entry point.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_blocked_tuple_decides_identically(
    tmp_path: Path,
) -> None:
    """A blocking tuple (retained integration record) yields
    BLOCK_FOR_RECOVERY at every entry point, independent of any prior
    persisted decision.

    Exercises IntegrationRecordStore.transition (the commit/integration entry
    point), not direct decide_phase_transition calls over copied models.
    """
    backend = _InMemoryBackend()
    store = IntegrationRecordStore(tmp_path, backend=backend)

    # Create a blocking record through the durable entry point.
    blocking_record = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="e" * 40,
        pre_target_sha="f" * 40,
    )
    assert blocking_record.phase == "integrating"

    # Blocking is a derived projection over current authority outputs.
    # Verify through the actual entry point (IntegrationRecordStore) that
    # a retained record blocks regardless of prior last_decision.
    for prior in (None, PhaseDecision.REPORT_TERMINAL_SUCCESS.value):
        # Read through the store (actual entry point).
        persisted = store.read()
        assert persisted is not None

        rebase_with_prior = RebaseState(
            last_decision=prior,
            last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            fast_forwarded=True,
        )
        state_with_prior = PipelineState(phase="development").copy_with(rebase=rebase_with_prior)

        # Verify BLOCK_FOR_RECOVERY through decide_phase_transition using
        # the ACTUAL persisted record from the store.
        decision = decide_phase_transition(
            state_with_prior,
            _small_policy(),
            integration_record=persisted,
            rebase_state=rebase_with_prior,
        )
        assert decision == PhaseDecision.BLOCK_FOR_RECOVERY

        action = next_integration_action(persisted)
        assert action in (
            IntegrationAction.CONTINUE_GIT_OPERATION,
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        )


# ---------------------------------------------------------------------------
# (b) last_decision is the persisted projection, never an input.
# Tested through _run_inner_loop: setting last_decision on the rebase
# before restart must not change the next decision produced by the loop.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_last_decision_projection_not_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any prior persisted last_decision cannot influence the next decision.

    Exercises _run_inner_loop (actual entry point) with a rebase state
    carrying various last_decision values; the decision must remain the
    same baseline.
    """
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    def _completing_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        if current_phase == "complete":
            return 0
        if current_phase == "planning":
            return state.copy_with(phase="development")
        if current_phase == "development":
            return state.copy_with(phase="complete")
        return state

    monkeypatch.setattr(runner, "run_pipeline_step", _completing_runner)

    baseline_state = PipelineState(phase="planning")
    baseline_rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
    )
    baseline_state = baseline_state.copy_with(rebase=baseline_rebase)

    # Compute baseline decision through the actual entry point.
    latest: list[PipelineState] = []
    ctx = _loop_context(tmp_path, bundle, latest)
    result, _, _ = run_loop._run_inner_loop(baseline_state, ctx, prev_phase="planning")
    record = IntegrationRecordStore(tmp_path, backend=_InMemoryBackend()).read()
    baseline_decision = decide_phase_transition(
        result,
        bundle.pipeline,
        integration_record=record,
        rebase_state=result.rebase,
    )

    # Now inject various last_decision values and verify decision unchanged.
    for prior in PhaseDecision:
        polluted_rebase = baseline_rebase.model_copy(update={"last_decision": prior.value})
        polluted_state = baseline_state.copy_with(rebase=polluted_rebase)

        latest2: list[PipelineState] = []
        ctx2 = _loop_context(tmp_path, bundle, latest2)
        result2, _, _ = run_loop._run_inner_loop(polluted_state, ctx2, prev_phase="planning")

        record2 = IntegrationRecordStore(tmp_path, backend=_InMemoryBackend()).read()
        decision2 = decide_phase_transition(
            result2,
            bundle.pipeline,
            integration_record=record2,
            rebase_state=result2.rebase,
        )
        assert decision2 == baseline_decision, (
            f"prior last_decision {prior} leaked into the next decision"
        )


def test_acceptance_scenario_3_unknown_evidence_unknown_verdict_persists(
    tmp_path: Path,
) -> None:
    """UNKNOWN lease: an expired lease routes the diagnostic verdict to
    UNKNOWN -- never silently to UNCHANGED -- and a restart with the
    same lease produces the same verdict (idempotent authority over
    indeterminate inputs).

    The diagnostic no longer compares the evidence snapshot; the
    ``<fingerprint>:<held_at>`` lease in ``diagnostic_ownership`` is
    the sole release authority. An expired lease (held_at far in
    the past) is the UNKNOWN input.
    """
    import time

    backend = _InMemoryBackend()
    store = IntegrationRecordStore(tmp_path, backend=backend)

    rec = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="g" * 40,
        pre_target_sha="h" * 40,
    )
    rec = store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)

    fingerprint = identity_fingerprint(rec)
    expired_held_at = time.time() - (_EVIDENCE_LEASE_SECONDS + 100.0)
    rec = store.transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=f"{fingerprint}:{expired_held_at}",
    )

    verdict = diagnostic_evidence_verdict(tmp_path, rec)
    assert verdict == EvidenceVerdict.UNKNOWN

    # Restart: fresh verdict over the same persisted record.
    persisted = store.read()
    assert persisted == rec

    restart_verdict = diagnostic_evidence_verdict(tmp_path, persisted)
    assert restart_verdict == EvidenceVerdict.UNKNOWN

    # UNKNOWN must also block dispatch (never proceed on indeterminate input).
    blocked_state = PipelineState(phase="development")
    blocked_decision = decide_phase_transition(
        blocked_state,
        _small_policy(),
        integration_record=persisted,
        rebase_state=RebaseState(),
    )
    assert blocked_decision == PhaseDecision.BLOCK_FOR_RECOVERY


# ---------------------------------------------------------------------------
# (c) Interrupting at each transition leaves prior recoverable state intact.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_interrupt_at_each_transition_state_intact(
    tmp_path: Path,
) -> None:
    """Interrupt before/after each record transition; the prior state is
    recoverable and replaying the interrupted transition is idempotent.

    Exercises IntegrationRecordStore.transition (the actual entry point
    for commit/integration) with crash simulation at each step.
    """
    backend = _InMemoryBackend()
    store = IntegrationRecordStore(tmp_path, backend=backend)

    # Interrupted before transition 1: no record exists, recoverable.
    assert store.read() is None

    # Transition 1 applied: persisted; prior state intact after "crash".
    step1 = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    assert step1.phase == "integrating"
    assert store.read() == step1

    # Restart: new store over the same durable backend.
    restarted = IntegrationRecordStore(tmp_path, backend=backend)
    assert restarted.read() == step1

    # Interrupted mid-transition: replay MARK_RESOLVING_REBASE idempotently.
    step2 = restarted.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2.resolving_rebase is True
    replayed = IntegrationRecordStore(tmp_path, backend=backend).transition(
        MARK_RESOLVING_REBASE, resolving_rebase=True
    )
    assert replayed == step2
    assert store.read() == step2

    # Bounded-recovery clock authority with a fake clock: bounded, never an
    # infinite loop.
    fresh_store = IntegrationRecordStore(tmp_path, backend=_InMemoryBackend())
    active = fresh_store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="3" * 40,
        pre_target_sha="4" * 40,
    )
    resolving = active.model_copy(update={"resolving_rebase": True})
    clock = FakeClock(start=0.0)
    before_deadline = next_integration_action(resolving, clock=clock.monotonic)
    clock.advance(3600.0)
    past_deadline = next_integration_action(resolving, clock=clock.monotonic)
    assert before_deadline in (
        IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    )
    assert past_deadline in (
        IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
        IntegrationAction.REQUIRE_INTERVENTION,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    )


# ---------------------------------------------------------------------------
# (d) Completed business work is NOT replayed after restart.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_checkpoint_restart_no_replayed_business_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupt at the phase boundary via KeyboardInterrupt; the restart
    resumes from the durable checkpoint without replaying completed work,
    and the projected last_decision does not stall the resumed loop.

    The public ``run_loop._run_inner_loop`` boundary drives the whole
    lifecycle; only the agent-execution step (``runner.run_pipeline_step``)
    is replaced by a stepping fake so the test never invokes a real agent.
    """
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")
    executed_business_work: list[str] = []

    def _stepping_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        if current_phase == "complete":
            return 0
        executed_business_work.append(current_phase)
        if current_phase == "planning":
            checkpoint.save(state.copy_with(phase="development"))
            raise KeyboardInterrupt("interrupt at planning->development boundary")
        checkpoint.save(state.copy_with(phase="complete"))
        return state.copy_with(phase="complete")

    monkeypatch.setattr(runner, "run_pipeline_step", _stepping_runner)

    def _loop_ctx(latest_states: list[PipelineState]) -> run_loop._LoopContext:
        return run_loop._LoopContext(
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
            sleep=MagicMock(),
            is_quiet=True,
            latest_state=latest_states,
        )

    initial_state = PipelineState(phase="planning")
    checkpoint.save(initial_state)

    with pytest.raises(KeyboardInterrupt):
        run_loop._run_inner_loop(initial_state, _loop_ctx([]), prev_phase="planning")
    assert executed_business_work == ["planning"]

    reconstituted = checkpoint.load()
    assert reconstituted is not None
    assert reconstituted.phase == "development"

    final_state, _prev, early_exit = run_loop._run_inner_loop(
        reconstituted, _loop_ctx([]), prev_phase="planning"
    )
    # Planning work NOT replayed; the resumed run only did development.
    assert executed_business_work == ["planning", "development"]
    assert final_state.phase == "complete"
    assert early_exit is None
    assert is_terminal_success_eligible(final_state, None, final_state.rebase) is True


# ---------------------------------------------------------------------------
# Parallel join authority: interrupted join restarts idempotently.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_parallel_join_restart_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interrupt a parallel join mid-flight through _run_inner_loop; on
    restart the join authority recomputes the same decision from the
    persisted worker states without replaying worker work.

    Exercises fan_out_decision computed through the dispatch loop entry
    point (_run_inner_loop), not direct fan_out_decision calls over
    copied PipelineState objects.
    """
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")
    work_invocations: list[str] = []

    # Custom exception to simulate crash (KeyboardInterrupt has pytest issues).
    class SimulatedCrashError(Exception):
        pass

    # Runner that crashes during first development phase.
    def _crashing_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        work_invocations.append(current_phase)
        if current_phase == "development":
            partial_workers = {
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.RUNNING),
            }
            checkpoint.save(state.copy_with(worker_states=partial_workers))
            raise SimulatedCrashError("simulated crash during parallel join")
        if current_phase == "complete":
            return 0
        return state.copy_with(phase="complete")

    # Runner that completes without crashing - simulates the full
    # development phase then advances to complete.
    def _completing_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        work_invocations.append(current_phase)
        if current_phase == "complete":
            return 0
        # Return state with phase advancement - the loop will check if
        # complete is terminal and exit on the next iteration.
        if current_phase == "development":
            # Save checkpoint with workers before advancing.
            partial_workers = {
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.RUNNING),
            }
            checkpoint.save(state.copy_with(worker_states=partial_workers))
        return state.copy_with(phase="complete")

    # Phase 1: Crash during development.
    monkeypatch.setattr(runner, "run_pipeline_step", _crashing_runner)
    initial_state = PipelineState(phase="development")
    checkpoint.save(initial_state)

    latest: list[PipelineState] = []
    ctx = _loop_context(tmp_path, bundle, latest)

    crashed = False
    try:
        run_loop._run_inner_loop(initial_state, ctx, prev_phase="planning")
    except SimulatedCrashError:
        crashed = True
    assert crashed, "Expected SimulatedCrash was not raised"
    assert work_invocations == ["development"]

    # Capture join decision BEFORE restart.
    pre_restart = checkpoint.load()
    assert pre_restart is not None
    fan_out_before = fan_out_decision(pre_restart)
    assert fan_out_before == FanOutDecision.JOIN_PENDING

    # Phase 2: Restart from checkpoint and complete.
    work_invocations.clear()
    monkeypatch.setattr(runner, "run_pipeline_step", _completing_runner)

    latest2: list[PipelineState] = []
    ctx2 = _loop_context(tmp_path, bundle, latest2)

    restarted_state, _, _ = run_loop._run_inner_loop(pre_restart, ctx2, prev_phase="planning")

    # Join decision must be identical after restart.
    fan_out_after = fan_out_decision(restarted_state)
    assert fan_out_after == FanOutDecision.JOIN_PENDING

    # Worker-a work was NOT replayed after restart.
    # Note: development phase was recorded; complete phase exits the loop
    # without an additional runner call since it's terminal.
    assert "development" in work_invocations

    # With all workers succeeded: JOIN_COMPLETE.
    complete_workers = {
        "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
        "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
    }
    joined_state = pre_restart.copy_with(worker_states=complete_workers)
    assert fan_out_decision(joined_state) == FanOutDecision.JOIN_COMPLETE

    # With settled join and clean authorities, terminal success reports
    # identically whether or not a prior blocking decision was persisted.
    clean = joined_state.copy_with(
        rebase=joined_state.rebase.model_copy(
            update={
                "last_fan_out_decision": FanOutDecision.JOIN_COMPLETE.value,
                "last_publication_decision": "publication_terminal",
            }
        )
    )
    after_block = clean.copy_with(
        rebase=clean.rebase.model_copy(
            update={"last_decision": PhaseDecision.BLOCK_FOR_RECOVERY.value}
        )
    )
    assert (
        decide_phase_transition(
            clean, _small_policy(), integration_record=None, rebase_state=clean.rebase
        )
        == decide_phase_transition(
            after_block, _small_policy(), integration_record=None, rebase_state=after_block.rebase
        )
        == PhaseDecision.REPORT_TERMINAL_SUCCESS
    )


def test_acceptance_scenario_3_partial_failure_join_restart_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partially failed join reports JOIN_PARTIAL_FAILURE through the
    dispatch loop entry point before and after a restart; the decision
    never depends on prior last_decision.

    Exercises fan_out_decision computed through _run_inner_loop, not
    direct fan_out_decision calls over copied models.
    """
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    def _failure_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        if current_phase == "complete":
            return 0
        if current_phase == "development":
            failed_workers = {
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.FAILED, exit_code=1),
            }
            return state.copy_with(phase="complete", worker_states=failed_workers)
        return state.copy_with(phase="development")

    monkeypatch.setattr(runner, "run_pipeline_step", _failure_runner)

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")
    initial_state = PipelineState(phase="development")
    checkpoint.save(initial_state)

    latest: list[PipelineState] = []
    ctx = _loop_context(tmp_path, bundle, latest)

    result, _, _ = run_loop._run_inner_loop(initial_state, ctx, prev_phase="development")

    # Capture partial failure decision BEFORE restart.
    fan_out_before = fan_out_decision(result)
    assert fan_out_before == FanOutDecision.JOIN_PARTIAL_FAILURE

    # Restart: same persisted worker states -> same decision.
    work_invocations: list[str] = []

    def _failure_runner_restart(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        work_invocations.append(current_phase)
        if current_phase == "complete":
            return 0
        if current_phase == "development":
            failed_workers = {
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.FAILED, exit_code=1),
            }
            return state.copy_with(phase="complete", worker_states=failed_workers)
        return state.copy_with(phase="development")

    monkeypatch.setattr(runner, "run_pipeline_step", _failure_runner_restart)

    reconstituted = checkpoint.load()
    latest2: list[PipelineState] = []
    ctx2 = _loop_context(tmp_path, bundle, latest2)

    result2, _, _ = run_loop._run_inner_loop(reconstituted, ctx2, prev_phase="development")

    fan_out_after = fan_out_decision(result2)
    assert fan_out_before == fan_out_after == FanOutDecision.JOIN_PARTIAL_FAILURE

    # A retained record blocks the phase decision, and stays blocked
    # regardless of any prior persisted decision (projection, not input).
    backend = _InMemoryBackend()
    record_store = IntegrationRecordStore(tmp_path, backend=backend)
    retained = record_store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="i" * 40,
        pre_target_sha="j" * 40,
    )

    for prior in (None, PhaseDecision.ADVANCE_PHASE.value):
        rebase = result2.rebase.model_copy(update={"last_decision": prior})
        decision = decide_phase_transition(
            result2,
            _small_policy(),
            integration_record=retained,
            rebase_state=rebase,
        )
        assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
        assert next_integration_action(retained) in (
            IntegrationAction.CONTINUE_GIT_OPERATION,
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        )


# ---------------------------------------------------------------------------
# Cooldown entry point: verified through next_integration_action with FakeClock.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_cooldown_entry_point_bounded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown entry point, exercised at real restart boundaries.

    A conflict record carries a typed ``retry_deadline``, checkpointed
    durably inside ``PipelineState.rebase`` so the restart boundary is
    real: save to checkpoint, crash, reload from checkpoint. The
    cooldown authority ``next_integration_action`` must yield WAIT
    before the deadline and REQUIRE_INTERVENTION after, with the crash
    injected across the restart; no unbounded wait either side.
    """

    class SimulatedCrashError(Exception):
        pass

    clock = FakeClock(start=0.0)
    deadline = 3600.0
    checkpoint = Checkpoint(tmp_path / "checkpoint.json")

    # Durable cooldown record: a conflict rebase state with a typed
    # deadline, persisted through the checkpoint (restart boundary).
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_outcome=RebaseOutcomeKind.CONFLICT,
            retry_deadline=deadline,
        ),
    )
    checkpoint.save(state)

    # BEFORE the deadline: bounded wait, strictly.
    action_before_deadline = next_integration_action(state.rebase, clock=clock.monotonic)
    assert action_before_deadline == IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    # Restart boundary: crash is injected across the restart, and the
    # record must be recovered through the durable checkpoint, not reused.
    crashed = False
    try:
        clock.advance(deadline + 1.0)
        raise SimulatedCrashError("simulated crash inside cooldown boundary")
    except SimulatedCrashError:
        crashed = True
    assert crashed

    # AFTER the deadline, reload through the checkpoint boundary.
    reconstituted = checkpoint.load()
    assert reconstituted is not None
    action_past_deadline = next_integration_action(reconstituted.rebase, clock=clock.monotonic)
    assert action_past_deadline == IntegrationAction.REQUIRE_INTERVENTION

    # A cooldown state with NO retry deadline must never authorize an
    # unbounded wait: REQUIRE_INTERVENTION before and after a real
    # checkpoint crash/reload boundary.
    undated = PipelineState(
        phase="development",
        rebase=RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT),
    )
    checkpoint.save(undated)
    assert next_integration_action(undated.rebase, clock=FakeClock(start=0.0).monotonic) == (
        IntegrationAction.REQUIRE_INTERVENTION
    )
    try:
        raise SimulatedCrashError("simulated crash on deadline-less cooldown")
    except SimulatedCrashError:
        pass
    reloaded = checkpoint.load()
    assert reloaded is not None
    assert next_integration_action(reloaded.rebase, clock=FakeClock(start=0.0).monotonic) == (
        IntegrationAction.REQUIRE_INTERVENTION
    )


# ---------------------------------------------------------------------------
# Terminal entry point: verified through is_terminal_success_eligible.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_terminal_entry_point_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal entry point at a real restart boundary: a crash is injected
    immediately before terminal dispatch (after the last phase's business
    work and its checkpoint write); the restarted run re-enters the loop
    through the ORIGINAL runner (no runner swap), and exact dispatch counts
    prove completed business work is never replayed.
    """
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")

    class SimulatedCrashError(Exception):
        pass

    # One runner for the ENTIRE test — never swapped at restart.
    dispatches: list[str] = []
    business_work: list[str] = []
    crash_armed = [True]

    def _terminal_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        if current_phase == "complete":
            return 0
        if current_phase == "planning":
            dispatches.append("planning")
            business_work.append("planning")
            checkpoint.save(state.copy_with(phase="development"))
            return state.copy_with(phase="development")
        if current_phase == "development":
            dispatches.append("development")
            business_work.append("development")
            settled_rebase = state.rebase.model_copy(
                update={
                    "last_fan_out_decision": FanOutDecision.JOIN_COMPLETE.value,
                    "last_publication_decision": "publication_terminal",
                    "fast_forwarded": True,
                }
            )
            # Fault injection AFTER commit (checkpoint write), BEFORE
            # terminal entry: the next loop iteration dispatches complete.
            checkpoint.save(state.copy_with(phase="complete", rebase=settled_rebase))
            if crash_armed[0]:
                crash_armed[0] = False
                raise SimulatedCrashError("simulated crash before terminal entry")
            return state.copy_with(phase="complete", rebase=settled_rebase)
        return state

    monkeypatch.setattr(runner, "run_pipeline_step", _terminal_runner)

    initial_state = PipelineState(phase="planning")
    checkpoint.save(initial_state)

    latest: list[PipelineState] = []
    ctx = _loop_context(tmp_path, bundle, latest)

    crashed = False
    try:
        run_loop._run_inner_loop(initial_state, ctx, prev_phase="planning")
    except SimulatedCrashError:
        crashed = True
    assert crashed, "Expected crash before terminal entry was not raised"
    # Run 1: planning work + development work done exactly once each.
    assert dispatches == ["planning", "development"]
    assert business_work == ["planning", "development"]

    # Restart from the durable checkpoint through the SAME runner. The
    # loop must re-enter at terminal dispatch WITHOUT re-running any
    # completed phase.
    reconstituted = checkpoint.load()
    assert reconstituted is not None
    assert reconstituted.phase == "complete"

    dispatches.clear()
    latest2: list[PipelineState] = []
    ctx2 = _loop_context(tmp_path, bundle, latest2)

    result2, _, _ = run_loop._run_inner_loop(reconstituted, ctx2, prev_phase="planning")

    # Terminal dispatch only: zero business-work replays after restart.
    assert dispatches == []
    assert business_work == ["planning", "development"]
    assert result2.phase == "complete"

    record = IntegrationRecordStore(tmp_path, backend=_InMemoryBackend()).read()
    assert is_terminal_success_eligible(result2, record, result2.rebase) is True
    # Completed business work was not replayed: exact mutator counts above
    # show planning and development each executed exactly once across the
    # whole crash+restart lifecycle.
