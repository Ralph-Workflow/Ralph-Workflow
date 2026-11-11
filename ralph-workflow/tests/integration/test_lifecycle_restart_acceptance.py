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
    EVIDENCE_LEASE_SECONDS as _EVIDENCE_LEASE_SECONDS,
)
from ralph.pipeline._integration_diagnostic import (
    diagnostic_evidence_verdict,
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
)
from ralph.pipeline.operation_evidence_verdict import EvidenceVerdict
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope
from tests.test_checkpoint_and_record_persistence_boundary import _InMemoryBackend

pytestmark = pytest.mark.timeout_seconds(5)


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
    return run_loop._LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": False,
                    "agent_max_consecutive_unproductive": 1,
                },
            }
        ),
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


def test_acceptance_scenario_3_blocked_tuple_decides_identically(
    tmp_path: Path,
) -> None:
    backend = _InMemoryBackend()
    store = IntegrationRecordStore(tmp_path, backend=backend)

    blocking_record = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="e" * 40,
        pre_target_sha="f" * 40,
    )
    assert blocking_record.phase == "integrating"

    for prior in (None, PhaseDecision.REPORT_TERMINAL_SUCCESS.value):
        persisted = store.read()
        assert persisted is not None

        rebase_with_prior = RebaseState(
            last_decision=prior,
            last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            fast_forwarded=True,
        )
        state_with_prior = PipelineState(phase="development").copy_with(rebase=rebase_with_prior)

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


def test_acceptance_scenario_3_last_decision_projection_not_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    for prior in (
        PhaseDecision.BLOCK_FOR_RECOVERY,
        PhaseDecision.REPORT_TERMINAL_SUCCESS,
        PhaseDecision.ADVANCE_PHASE,
        PhaseDecision.REPORT_TERMINAL_FAILURE,
    ):
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

    persisted = store.read()
    assert persisted == rec

    restart_verdict = diagnostic_evidence_verdict(tmp_path, persisted)
    assert restart_verdict == EvidenceVerdict.UNKNOWN

    blocked_state = PipelineState(phase="development")
    blocked_decision = decide_phase_transition(
        blocked_state,
        _small_policy(),
        integration_record=persisted,
        rebase_state=RebaseState(),
    )
    assert blocked_decision == PhaseDecision.BLOCK_FOR_RECOVERY


def test_acceptance_scenario_3_interrupt_at_each_transition_state_intact(
    tmp_path: Path,
) -> None:
    backend = _InMemoryBackend()
    store = IntegrationRecordStore(tmp_path, backend=backend)

    assert store.read() is None

    step1 = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    assert step1.phase == "integrating"
    assert store.read() == step1

    restarted = IntegrationRecordStore(tmp_path, backend=backend)
    assert restarted.read() == step1

    step2 = restarted.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2.resolving_rebase is True
    replayed = IntegrationRecordStore(tmp_path, backend=backend).transition(
        MARK_RESOLVING_REBASE, resolving_rebase=True
    )
    assert replayed == step2
    assert store.read() == step2

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


def test_acceptance_scenario_3_checkpoint_restart_no_replayed_business_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert executed_business_work == ["planning", "development"]
    assert final_state.phase == "complete"
    assert early_exit is None
    assert is_terminal_success_eligible(final_state, None, final_state.rebase) is True


def test_acceptance_scenario_3_parallel_join_restart_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")
    work_invocations: list[str] = []

    class SimulatedCrashError(Exception):
        pass

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
        if current_phase == "development":
            partial_workers = {
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
                "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.RUNNING),
            }
            checkpoint.save(state.copy_with(worker_states=partial_workers))
        return state.copy_with(phase="complete")

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

    pre_restart = checkpoint.load()
    assert pre_restart is not None
    fan_out_before = fan_out_decision(pre_restart)
    assert fan_out_before == FanOutDecision.JOIN_PENDING

    work_invocations.clear()
    monkeypatch.setattr(runner, "run_pipeline_step", _completing_runner)

    latest2: list[PipelineState] = []
    ctx2 = _loop_context(tmp_path, bundle, latest2)

    restarted_state, _, _ = run_loop._run_inner_loop(pre_restart, ctx2, prev_phase="planning")

    fan_out_after = fan_out_decision(restarted_state)
    assert fan_out_after == FanOutDecision.JOIN_PENDING

    assert "development" in work_invocations

    complete_workers = {
        "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
        "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
    }
    joined_state = pre_restart.copy_with(worker_states=complete_workers)
    assert fan_out_decision(joined_state) == FanOutDecision.JOIN_COMPLETE

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

    fan_out_before = fan_out_decision(result)
    assert fan_out_before == FanOutDecision.JOIN_PARTIAL_FAILURE

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


def test_acceptance_scenario_3_cooldown_entry_point_bounded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class SimulatedCrashError(Exception):
        pass

    clock = FakeClock(start=0.0)
    deadline = 3600.0
    checkpoint = Checkpoint(tmp_path / "checkpoint.json")

    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_outcome=RebaseOutcomeKind.CONFLICT,
            retry_deadline=deadline,
        ),
    )
    checkpoint.save(state)

    action_before_deadline = next_integration_action(state.rebase, clock=clock.monotonic)
    assert action_before_deadline == IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    crashed = False
    try:
        clock.advance(deadline + 1.0)
        raise SimulatedCrashError("simulated crash inside cooldown boundary")
    except SimulatedCrashError:
        crashed = True
    assert crashed

    reconstituted = checkpoint.load()
    assert reconstituted is not None
    action_past_deadline = next_integration_action(reconstituted.rebase, clock=clock.monotonic)
    assert action_past_deadline == IntegrationAction.REQUIRE_INTERVENTION

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


def test_acceptance_scenario_3_terminal_entry_point_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": _small_policy()})

    checkpoint = Checkpoint(tmp_path / "checkpoint.json")

    class SimulatedCrashError(Exception):
        pass

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
    assert dispatches == ["planning", "development"]
    assert business_work == ["planning", "development"]

    reconstituted = checkpoint.load()
    assert reconstituted is not None
    assert reconstituted.phase == "complete"

    dispatches.clear()
    latest2: list[PipelineState] = []
    ctx2 = _loop_context(tmp_path, bundle, latest2)

    result2, _, _ = run_loop._run_inner_loop(reconstituted, ctx2, prev_phase="planning")

    assert dispatches == []
    assert business_work == ["planning", "development"]
    assert result2.phase == "complete"

    record = IntegrationRecordStore(tmp_path, backend=_InMemoryBackend()).read()
    assert is_terminal_success_eligible(result2, record, result2.rebase) is True
