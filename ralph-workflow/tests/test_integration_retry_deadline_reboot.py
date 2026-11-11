from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import IntegrationAction
from ralph.pipeline.auto_integrate_backoff import (
    MAX_RETRY_DEADLINE_HORIZON_SECONDS,
    compute_retry_deadline,
    retry_deadline_is_live,
)
from ralph.pipeline.auto_integrate_conflict_budget import apply_conflict_budget
from ralph.pipeline.auto_integrate_recovery import busy_refusal_outcome
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState

_PREVIOUS_BOOT_DEADLINE = 5 * 24 * 3600.0 + 2.0
_AFTER_REBOOT = 30.0


def test_busy_deadline_from_a_previous_boot_is_stale_intent() -> None:
    action = busy_refusal_outcome(_PREVIOUS_BOOT_DEADLINE, clock=lambda: _AFTER_REBOOT)

    assert action is RecordFactOutcome.STALE_INTENT


@pytest.mark.parametrize(
    ("remaining", "live"),
    [
        pytest.param(2.0, True, id="ordinary-retry-delay"),
        pytest.param(MAX_RETRY_DEADLINE_HORIZON_SECONDS, True, id="at-the-horizon"),
        pytest.param(MAX_RETRY_DEADLINE_HORIZON_SECONDS + 0.5, False, id="past-the-horizon"),
        pytest.param(0.0, False, id="expired-now"),
        pytest.param(-5.0, False, id="expired-earlier"),
    ],
)
def test_busy_reader_accepts_a_deadline_only_within_the_horizon(
    remaining: float, live: bool
) -> None:
    now = 1_000.0
    deadline = now + remaining
    action = busy_refusal_outcome(deadline, clock=lambda: now)

    assert retry_deadline_is_live(deadline, now) is live
    assert (action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY) is live


def test_writers_never_arm_a_deadline_past_the_horizon() -> None:
    clock = FakeClock(start=100.0)
    record = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT, last_target="main")

    budgeted = apply_conflict_budget(
        record,
        prior=RebaseState(),
        target="main",
        resolver_suppressed=False,
        clock=clock.monotonic,
        cooldown_seconds=10 * MAX_RETRY_DEADLINE_HORIZON_SECONDS,
    )
    computed = compute_retry_deadline(
        1, clock=clock, cooldown_seconds=10 * MAX_RETRY_DEADLINE_HORIZON_SECONDS
    )

    assert budgeted.retry_deadline == 100.0 + MAX_RETRY_DEADLINE_HORIZON_SECONDS
    assert computed == 100.0 + MAX_RETRY_DEADLINE_HORIZON_SECONDS
    assert (
        busy_refusal_outcome(budgeted.retry_deadline, clock=clock.monotonic)
        is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    )
