from __future__ import annotations

from typing import NamedTuple

import pytest

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.dead_agent_verdict import TypedSignal
from ralph.recovery.end_attempt import end_attempt


class _OutcomeCase(NamedTuple):
    name: str
    facts: AttemptFacts
    attempt_failed: bool
    expected_kind: AttemptOutcomeKind
    typed_signal: TypedSignal | None = None
    probe_outcome: ProbeOutcome | None = None
    peer_failures_identical: bool = False
    retryable_reason: str | None = None
    expected_issuer: TerminationIssuer | None = None


_CASES = (
    _OutcomeCase(
        name="completed",
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
        expected_kind=AttemptOutcomeKind.COMPLETED,
    ),
    _OutcomeCase(
        name="retryable_failure",
        facts=AttemptFacts(),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.RETRYABLE_FAILURE,
        probe_outcome=ProbeOutcome.PASSED,
        retryable_reason="transient transport failure",
    ),
    _OutcomeCase(
        name="runtime_ended_post_exit_watchdog",
        facts=AttemptFacts(
            liveness_observed=True,
            termination=TerminationRecord.create(TerminationIssuer.POST_EXIT_WATCHDOG),
        ),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.RUNTIME_ENDED,
        expected_issuer=TerminationIssuer.POST_EXIT_WATCHDOG,
    ),
    _OutcomeCase(
        name="runtime_ended_quota_detector",
        facts=AttemptFacts(
            liveness_observed=True,
            termination=TerminationRecord.create(TerminationIssuer.QUOTA_DETECTOR),
        ),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.RUNTIME_ENDED,
        expected_issuer=TerminationIssuer.QUOTA_DETECTOR,
    ),
    _OutcomeCase(
        name="task_or_environment_failure_valid_artifact",
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE,
    ),
    _OutcomeCase(
        name="task_or_environment_failure_identical_peers",
        facts=AttemptFacts(event_kinds=("unseen_failure",)),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE,
        peer_failures_identical=True,
    ),
    _OutcomeCase(
        name="agent_dead_typed_provider_signal",
        facts=AttemptFacts(),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.AGENT_DEAD,
        typed_signal=TypedSignal(
            name="ProviderUnavailable",
            provider_originated=True,
            agent_initiated=True,
        ),
    ),
    _OutcomeCase(
        name="unknown_event_shape",
        facts=AttemptFacts(event_kinds=("future_event_shape",)),
        attempt_failed=True,
        expected_kind=AttemptOutcomeKind.UNKNOWN,
    ),
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_end_attempt_maps_every_outcome_kind(case: _OutcomeCase) -> None:
    outcome = end_attempt(
        case.facts,
        attempt_failed=case.attempt_failed,
        typed_signal=case.typed_signal,
        probe_outcome=case.probe_outcome,
        peer_failures_identical=case.peer_failures_identical,
        retryable_reason=case.retryable_reason,
    )

    assert outcome.kind is case.expected_kind
    assert outcome.issuer is case.expected_issuer
    assert outcome.benches_agent is (case.expected_kind is AttemptOutcomeKind.AGENT_DEAD)
    assert outcome.retries_same_agent is (
        case.expected_kind is AttemptOutcomeKind.RETRYABLE_FAILURE
    )
    assert outcome.counts_toward_agent_state is (
        case.expected_kind
        not in {
            AttemptOutcomeKind.COMPLETED,
            AttemptOutcomeKind.RUNTIME_ENDED,
            AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE,
        }
    )
    if case.expected_kind is AttemptOutcomeKind.AGENT_DEAD:
        assert outcome.verdict is not None
