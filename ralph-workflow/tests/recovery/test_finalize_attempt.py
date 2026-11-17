from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import TypedSignal
from ralph.recovery.unavailability_reason import UnavailabilityReason

PHASE = "development"
AGENT_A = "agent_a"
AGENT_B = "agent_b"
FINGERPRINT = "fp-1"
OTHER_FINGERPRINT = "fp-2"
SESSION_REF = {
    "run_id": "run-1",
    "phase": PHASE,
    "agent": AGENT_A,
    "entry_point": "main_phase_loop",
}


def _termination(issuer: TerminationIssuer = TerminationIssuer.AGENT_EXIT) -> TerminationRecord:
    return TerminationRecord.create(issuer, observed_returncode=1, reported_returncode=1)


def _failed_facts(
    *,
    artifact_valid: bool = False,
    workspace_changed: bool | None = False,
    exit_status: str | None = "exit_1",
    liveness_observed: bool | None = None,
    termination: TerminationRecord | None = None,
) -> AttemptFacts:
    return AttemptFacts(
        artifact_valid=artifact_valid,
        workspace_changed=workspace_changed,
        exit_status=exit_status,
        liveness_observed=liveness_observed,
        termination=termination if termination is not None else _termination(),
    )


def _controller(
    *,
    probe_outcome: ProbeOutcome | None = ProbeOutcome.PASSED,
    clock: FakeClock | None = None,
    probe_raises: bool = False,
) -> RecoveryController:
    def probe_runner(_phase: str, _agent: str) -> ProbeOutcome | None:
        if probe_raises:
            raise RuntimeError("probe infrastructure unavailable")
        return probe_outcome

    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock or FakeClock(start=0.0),
            dead_agent_probe_runner=probe_runner,
            agent_max_consecutive_unproductive=1,
        )
    )


def _tracker(controller: RecoveryController) -> AgentUnavailabilityTracker:
    store = controller.unavailability_store
    assert isinstance(store, AgentUnavailabilityTracker)
    return store


def _cooldown_until_ms(controller: RecoveryController, agent: str) -> int | None:
    status = controller.ledger.status(PHASE, agent)
    if isinstance(status, Cooldown):
        return status.until_ms
    return None


def test_ac3_a_runtime_issued_end_with_liveness_is_runtime_ended_and_never_charged() -> None:
    controller = _controller()
    facts = _failed_facts(
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.QUOTA_DETECTOR,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=facts,
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.RUNTIME_ENDED
    assert outcome.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True
    assert isinstance(controller.ledger.status(PHASE, AGENT_A), Eligible)


def test_ac14_an_unattributed_live_end_with_a_passing_probe_is_unknown_and_charged() -> None:
    controller = _controller()
    facts = AttemptFacts(
        artifact_valid=False,
        exit_status="exit_0",
        liveness_observed=True,
        elapsed_to_terminal_ms=97900,
    )

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=facts,
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.UNKNOWN
    assert outcome.benches_agent is False
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True
    assert _cooldown_until_ms(controller, AGENT_A) is not None
    assert controller.ledger.status(PHASE, AGENT_A).is_spent is False


def test_ac7_a_probe_that_cannot_run_leaves_the_attempt_unknown_and_unbenched() -> None:
    controller = _controller(probe_raises=True)

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.UNKNOWN
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True
    assert controller.false_bench_feedback.count == 0


def test_ac10_a_typed_signal_benches_the_agent_on_the_first_failure() -> None:
    controller = _controller()
    signal = TypedSignal(name="quota_exhausted", provider_originated=True, agent_initiated=True)

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
        typed_signal=signal,
    )

    assert outcome.kind is AttemptOutcomeKind.AGENT_DEAD
    assert outcome.benches_agent is True
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is False
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons")
    assert isinstance(reasons, dict)
    assert reasons.get(AGENT_A) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac8_peers_failing_identically_are_surfaced_as_a_task_or_environment_failure() -> None:
    controller = _controller()
    controller.register_phase_chain(PHASE, (AGENT_A, AGENT_B))
    facts = _failed_facts()

    first = controller.finalize_attempt(PHASE, AGENT_A, facts=facts, attempt_failed=True)
    second = controller.finalize_attempt(PHASE, AGENT_B, facts=facts, attempt_failed=True)

    assert first.kind is AttemptOutcomeKind.UNKNOWN
    assert second.kind is AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
    assert second.benches_agent is False
    assert _cooldown_until_ms(controller, AGENT_A) is not None
    assert isinstance(controller.ledger.status(PHASE, AGENT_B), Eligible)
    assert controller.unavailability_store.is_available(PHASE, AGENT_B) is True


def test_ac8_a_failed_attempt_with_a_valid_artifact_is_a_task_or_environment_failure() -> None:
    controller = _controller()

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(artifact_valid=True),
        attempt_failed=True,
    )

    assert outcome.kind is AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE
    assert outcome.benches_agent is False
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True


def test_a_repeated_call_with_the_same_attempt_key_changes_nothing() -> None:
    controller = _controller()
    facts = _failed_facts()

    first = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=facts,
        attempt_failed=True,
        attempt_key="main_phase_loop:development:agent_a:1",
    )
    charged_until = _cooldown_until_ms(controller, AGENT_A)
    second = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=facts,
        attempt_failed=True,
        attempt_key="main_phase_loop:development:agent_a:1",
    )

    assert second is first
    assert _cooldown_until_ms(controller, AGENT_A) == charged_until


def test_an_unkeyed_call_claims_the_keyed_outcome_once_and_then_finalizes_fresh() -> None:
    controller = _controller()
    facts = _failed_facts()

    keyed = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=facts,
        attempt_failed=True,
        attempt_key="main_phase_loop:development:agent_a:1",
    )
    charged_until = _cooldown_until_ms(controller, AGENT_A)
    claimed = controller.finalize_attempt(PHASE, AGENT_A, facts=facts, attempt_failed=True)

    assert claimed is keyed
    assert _cooldown_until_ms(controller, AGENT_A) == charged_until

    fresh = controller.finalize_attempt(PHASE, AGENT_A, facts=facts, attempt_failed=True)

    assert fresh is not keyed
    assert (_cooldown_until_ms(controller, AGENT_A) or 0) > (charged_until or 0)


def test_a_success_after_a_bench_counts_one_false_bench_under_the_same_fingerprint() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock=clock)
    signal = TypedSignal(name="quota_exhausted", provider_originated=True, agent_initiated=True)

    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
        typed_signal=signal,
        attempt_key="main_phase_loop:development:agent_a:1",
        config_fingerprint=FINGERPRINT,
        session_ref=SESSION_REF,
    )
    assert controller.false_bench_feedback.count == 0

    clock.advance(10.1)
    _tracker(controller).prune_expired()

    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
        attempt_key="main_phase_loop:development:agent_a:2",
        config_fingerprint=FINGERPRINT,
    )

    feedback = controller.false_bench_feedback
    assert feedback.count == 1
    event = feedback.events[0]
    assert event["agent"] == AGENT_A
    assert event["config_fingerprint"] == FINGERPRINT
    assert event["session_ref"] == SESSION_REF
    assert event["verdict_ref"] == controller.last_verdict_record


def test_a_success_under_another_fingerprint_counts_no_false_bench() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock=clock)
    signal = TypedSignal(name="quota_exhausted", provider_originated=True, agent_initiated=True)

    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
        typed_signal=signal,
        attempt_key="main_phase_loop:development:agent_a:1",
        config_fingerprint=FINGERPRINT,
    )
    clock.advance(10.1)
    _tracker(controller).prune_expired()
    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
        attempt_key="main_phase_loop:development:agent_a:2",
        config_fingerprint=OTHER_FINGERPRINT,
    )

    assert controller.false_bench_feedback.count == 0


def test_the_false_bench_feedback_survives_a_snapshot_round_trip() -> None:
    controller = _controller()
    signal = TypedSignal(name="quota_exhausted", provider_originated=True, agent_initiated=True)

    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
        typed_signal=signal,
        attempt_key="main_phase_loop:development:agent_a:1",
        config_fingerprint=FINGERPRINT,
    )
    controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
        attempt_key="main_phase_loop:development:agent_a:2",
        config_fingerprint=FINGERPRINT,
    )

    snapshot = controller.snapshot()
    feedback_snapshot = snapshot["false_bench_feedback"]
    assert isinstance(feedback_snapshot, dict)
    assert feedback_snapshot["count"] == 1

    restored = RecoveryController(
        options=RecoveryControllerOptions.from_snapshot(snapshot),
    )

    assert restored.false_bench_feedback.count == 1
    assert restored.false_bench_feedback.events[0]["config_fingerprint"] == FINGERPRINT


def test_a_probe_mode_attempt_neither_benches_nor_charges() -> None:
    controller = _controller()
    signal = TypedSignal(name="quota_exhausted", provider_originated=True, agent_initiated=True)

    outcome = controller.finalize_attempt(
        PHASE,
        AGENT_A,
        facts=_failed_facts(),
        attempt_failed=True,
        typed_signal=signal,
        probe_mode=True,
    )

    assert outcome.kind is AttemptOutcomeKind.AGENT_DEAD
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True
    assert isinstance(controller.ledger.status(PHASE, AGENT_A), Eligible)
    assert controller.false_bench_feedback.count == 0
