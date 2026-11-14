from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _controller() -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))


def test_u2_regression_stalled_no_progress_is_typed_unavailability() -> None:
    failure = _controller().classify_conflict_attempt(
        None,
        agent="primary",
        outcome=ConflictAttemptOutcome.STALLED,
        unresolved_count=2,
    )

    assert failure.is_unavailable is True
    assert failure.unavailability_reason is UnavailabilityReason.DEAD_AGENT_NO_PROGRESS
    assert failure.counts_against_budget is True
    assert failure.must_charge_ledger is True


def test_u2_regression_no_activity_uses_no_output_at_start() -> None:
    failure = _controller().classify_conflict_attempt(
        None,
        agent="primary",
        outcome=ConflictAttemptOutcome.NO_ACTIVITY,
    )

    assert failure.is_unavailable is True
    assert failure.unavailability_reason is UnavailabilityReason.NO_OUTPUT_AT_START
    assert failure.counts_against_budget is True


def test_u2_regression_progress_remains_non_penalising() -> None:
    failure = _controller().classify_conflict_attempt(
        None,
        agent="primary",
        outcome=ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        unresolved_count=1,
    )

    assert failure.is_unavailable is False
    assert failure.unavailability_reason is None
    assert failure.counts_against_budget is False
