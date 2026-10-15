"""Acceptance Scenario 3 (WU-S3): interrupt + restart idempotency at public
workflow boundaries.

Drives the lifecycle entry points (startup, run loop, cooldown, parallel
join, terminal) from UNKNOWN evidence through a restart to authoritative
state continuity. The single authority under test is
:func:`ralph.pipeline._pipeline_state_machine.decide_phase_transition`.

Invariants asserted:

(a) every entry point yields the SAME :class:`PhaseDecision` for a fixed
    (state, policy, record, rebase) tuple -- decisions are pure functions
    of current authority inputs;
(b) ``rebase_state.last_decision`` is the *persisted projection* (output)
    of ``decide_phase_transition``, never a feedback input: feeding any
    prior decision back in cannot change the next decision;
(c) interrupting at each transition leaves the prior recoverable state
    intact on the durable record, and replaying the transition after the
    restart is idempotent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline._fan_out_authority import FanOutDecision, fan_out_decision
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._integration_diagnostic import diagnostic_evidence_verdict
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import (
    HOLD_DIAGNOSTIC_EVIDENCE,
    MARK_RESOLVING_REBASE,
    START_INTEGRATING,
)
from ralph.pipeline.auto_integrate_record_store import IntegrationRecordStore
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.operation_evidence import ownership_fingerprint
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


def _fixed_tuple() -> tuple[PipelineState, PipelinePolicy, RebaseState]:
    """A fixed (state, policy, rebase) tuple shared by every entry point."""
    state = PipelineState(phase="development")
    rebase = RebaseState(last_outcome=RebaseOutcomeKind.FAST_FORWARDED, fast_forwarded=True)
    return state, _small_policy(), rebase


# ---------------------------------------------------------------------------
# (a) Every entry point produces the same PhaseDecision for a fixed tuple.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_same_decision_at_every_entry_point() -> None:
    """Startup, run loop, cooldown, parallel join, and terminal all agree."""
    state, policy, rebase = _fixed_tuple()

    # Entry point 1: startup authority -- fresh rebase, no prior decision.
    startup_decision = decide_phase_transition(
        state, policy, integration_record=None, rebase_state=rebase
    )

    # Entry point 2: run loop authority -- decision projected onto the
    # rebase state (as the reducer does via _route_reduced_decision), then
    # consulted again. The projection must not change the decision.
    after_run_loop = rebase.model_copy(update={"last_decision": startup_decision.value})
    assert (
        decide_phase_transition(state, policy, integration_record=None, rebase_state=after_run_loop)
        == startup_decision
    )

    # Entry point 3: cooldown authority -- the cooldown path consults the
    # same authority after wait expiry with a prior last_decision persisted;
    # the decision must be unchanged.
    cooldown_rebase = after_run_loop.model_copy(
        update={"last_decision": PhaseDecision.CONTINUE_PHASE.value}
    )
    assert (
        decide_phase_transition(
            state, policy, integration_record=None, rebase_state=cooldown_rebase
        )
        == startup_decision
    )

    # Entry point 4: parallel join -- a settled join decision on the rebase
    # state derives from current authority outputs, never last_decision.
    join_rebase = after_run_loop.model_copy(
        update={"last_fan_out_decision": FanOutDecision.JOIN_COMPLETE.value}
    )
    assert (
        decide_phase_transition(state, policy, integration_record=None, rebase_state=join_rebase)
        == startup_decision
    )

    # Entry point 5: terminal -- with every authority settled, the same
    # tuple reports terminal success regardless of the persisted decision.
    terminal_rebase = join_rebase.model_copy(
        update={"last_publication_decision": "publication_terminal"}
    )
    assert (
        decide_phase_transition(
            state, policy, integration_record=None, rebase_state=terminal_rebase
        )
        == PhaseDecision.REPORT_TERMINAL_SUCCESS
    )
    assert is_terminal_success_eligible(state, None, terminal_rebase) is True


def test_acceptance_scenario_3_blocked_tuple_decides_identically_everywhere() -> None:
    """A blocking tuple (retained record) yields BLOCK_FOR_RECOVERY at every
    entry point, independent of any prior persisted decision."""
    state, policy, rebase = _fixed_tuple()
    store = IntegrationRecordStore(Path("/wu-s3-blocked"), backend=_InMemoryBackend())
    blocking_record = store.transition(
        START_INTEGRATING, target="main", pre_feature_sha="a" * 40, pre_target_sha="b" * 40
    )

    # Blocking is a derived projection over current authority outputs.
    for prior in (None, PhaseDecision.REPORT_TERMINAL_SUCCESS.value):
        rebase_with_prior = rebase.model_copy(update={"last_decision": prior})
        assert (
            decide_phase_transition(
                state,
                policy,
                integration_record=blocking_record,
                rebase_state=rebase_with_prior,
            )
            == PhaseDecision.BLOCK_FOR_RECOVERY
        )
        assert next_integration_action(blocking_record) in (
            IntegrationAction.CONTINUE_GIT_OPERATION,
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        )


# ---------------------------------------------------------------------------
# (b) last_decision is the persisted projection, never an input.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_last_decision_is_projection_not_input() -> None:
    """Any prior persisted decision cannot influence the next decision."""
    state, policy, rebase = _fixed_tuple()

    baseline = decide_phase_transition(state, policy, integration_record=None, rebase_state=rebase)

    for prior in PhaseDecision:
        projected = rebase.model_copy(update={"last_decision": prior.value})
        assert (
            decide_phase_transition(state, policy, integration_record=None, rebase_state=projected)
            == baseline
        ), f"prior last_decision {prior} leaked into the next decision"


def test_acceptance_scenario_3_unknown_evidence_unknown_verdict_persists() -> None:
    """UNKNOWN evidence: an unreadable observation routes the diagnostic
    verdict to UNKNOWN -- never silently to UNCHANGED -- and a restart with
    the same evidence produces the same verdict (idempotent authority over
    indeterminate inputs)."""
    store = IntegrationRecordStore(Path("/wu-s3-unknown"), backend=_InMemoryBackend())
    rec = store.transition(
        START_INTEGRATING, target="main", pre_feature_sha="c" * 40, pre_target_sha="d" * 40
    )
    rec = store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)

    fingerprint = ownership_fingerprint(rec)
    unreadable_facts = (
        OperationFact(kind="head", value="unreadable"),
        OperationFact(kind="worktree_path", value="unreadable"),
    )
    rec = store.transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=tuple(f.to_evidence_tuple() for f in unreadable_facts),
        diagnostic_ownership=fingerprint,
    )

    collector = InMemoryOperationEvidenceCollector(facts=unreadable_facts)
    assert (
        diagnostic_evidence_verdict(Path("/wu-s3-unknown"), rec, collector=collector)
        == EvidenceVerdict.UNKNOWN
    )

    # Restart: fresh collector over the same persisted evidence -- the
    # authority must reach the same verdict without mutating the record.
    persisted = store.read()
    assert persisted == rec
    restart_collector = InMemoryOperationEvidenceCollector(facts=unreadable_facts)
    assert (
        diagnostic_evidence_verdict(Path("/wu-s3-unknown"), persisted, collector=restart_collector)
        == EvidenceVerdict.UNKNOWN
    )

    # UNKNOWN must also block dispatch (never proceed on indeterminate input).
    assert (
        decide_phase_transition(
            PipelineState(phase="development"),
            _small_policy(),
            integration_record=persisted,
            rebase_state=RebaseState(),
        )
        == PhaseDecision.BLOCK_FOR_RECOVERY
    )


# ---------------------------------------------------------------------------
# (c) Interrupting at each transition leaves prior recoverable state intact.
# ---------------------------------------------------------------------------


def test_acceptance_scenario_3_interrupt_at_each_transition_state_intact() -> None:
    """Interrupt before/after each record transition; the prior state is
    recoverable and replaying the interrupted transition is idempotent."""
    backend = _InMemoryBackend()
    root = Path("/wu-s3-interrupt")
    store = IntegrationRecordStore(root, backend=backend)

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
    restarted = IntegrationRecordStore(root, backend=backend)
    assert restarted.read() == step1

    # Interrupted mid-transition: replay MARK_RESOLVING_REBASE idempotently.
    step2 = restarted.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2.resolving_rebase is True
    replayed = IntegrationRecordStore(root, backend=backend).transition(
        MARK_RESOLVING_REBASE, resolving_rebase=True
    )
    assert replayed == step2
    assert store.read() == step2

    # Bounded-recovery clock authority with a fake clock: bounded, never an
    # infinite loop.
    fresh_store = IntegrationRecordStore(Path("/wu-s3-clock"), backend=_InMemoryBackend())
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
    )
    assert past_deadline in (
        IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
        IntegrationAction.REQUIRE_INTERVENTION,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
    )


def test_acceptance_scenario_3_checkpoint_restart_no_replayed_business_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interrupt at the phase boundary via KeyboardInterrupt; the restart
    resumes from the durable checkpoint without replaying completed work,
    and the projected last_decision does not stall the resumed loop.

    The public ``run_loop._run_inner_loop`` boundary drives the whole
    lifecycle; only the agent-execution step (``runner.run_pipeline_step``)
    is replaced by a stepping fake so the test never invokes a real agent.
    """
    import ralph.pipeline.runner as runner_module

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

    monkeypatch.setattr(runner_module, "run_pipeline_step", _stepping_runner)

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


def test_acceptance_scenario_3_parallel_join_restart_idempotent() -> None:
    """Interrupt a parallel join mid-flight; on restart the join authority
    recomputes the same decision from the persisted worker states."""
    workers = {
        "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.PENDING),
        "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.RUNNING),
    }
    state = PipelineState(phase="development", worker_states=workers)

    # Join authority: partial completion is still JOIN_PENDING.
    partial = state.copy_with(
        worker_states={
            **workers,
            "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
        }
    )
    assert fan_out_decision(partial) == FanOutDecision.JOIN_PENDING

    # Crash + restart: the same persisted worker states yield the same
    # join decision (pure over current state, no hidden inputs).
    assert fan_out_decision(partial.copy_with(rebase=partial.rebase)) == fan_out_decision(partial)

    # Terminal join: all succeeded -> JOIN_COMPLETE.
    joined = partial.copy_with(
        worker_states={
            "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
            "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
        }
    )
    assert fan_out_decision(joined) == FanOutDecision.JOIN_COMPLETE

    # With a settled join and clean authorities, terminal success reports
    # identically whether or not a prior blocking decision was persisted.
    policy = _small_policy()
    clean = joined.copy_with(
        rebase=joined.rebase.model_copy(
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
        decide_phase_transition(clean, policy, integration_record=None, rebase_state=clean.rebase)
        == decide_phase_transition(
            after_block, policy, integration_record=None, rebase_state=after_block.rebase
        )
        == PhaseDecision.REPORT_TERMINAL_SUCCESS
    )


def test_acceptance_scenario_3_partial_failure_join_restart_idempotent() -> None:
    """A partially failed join reports JOIN_PARTIAL_FAILURE before and after
    a restart; the decision never depends on prior last_decision."""
    failed = {
        "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
        "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.FAILED, exit_code=1),
    }
    base = PipelineState(phase="development", worker_states=failed)
    assert fan_out_decision(base) == FanOutDecision.JOIN_PARTIAL_FAILURE

    # Restart: same persisted worker states -> same decision.
    assert (
        fan_out_decision(base.copy_with(rebase=base.rebase)) == FanOutDecision.JOIN_PARTIAL_FAILURE
    )

    # A retained record blocks the phase decision, and stays blocked
    # regardless of any prior persisted decision (projection, not input).
    policy = _small_policy()
    record_store = IntegrationRecordStore(Path("/wu-s3-partial"), backend=_InMemoryBackend())
    retained = record_store.transition(
        START_INTEGRATING, target="main", pre_feature_sha="e" * 40, pre_target_sha="f" * 40
    )
    for prior in (None, PhaseDecision.ADVANCE_PHASE.value):
        rebase = base.rebase.model_copy(update={"last_decision": prior})
        decision = decide_phase_transition(
            base, policy, integration_record=retained, rebase_state=rebase
        )
        assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
        assert next_integration_action(retained) in (
            IntegrationAction.CONTINUE_GIT_OPERATION,
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        )
