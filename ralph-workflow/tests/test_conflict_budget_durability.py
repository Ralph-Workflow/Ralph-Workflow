"""Tests verifying retry counters and cooldowns are durable, restart-surviving,
bounded, and respect clock boundaries.
"""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory


def _make_failure(
    *,
    category: FailureCategory = FailureCategory.AGENT,
    reason: str = "transient error",
    agent: str = "claude",
    phase: str = "development",
    counts: bool = True,
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        reason,
    )


class TestApplyConflictBudgetDurability:
    """apply_conflict_budget durability and clock boundary tests."""

    def test_deadline_computed_from_injected_clock(self) -> None:
        clock = FakeClock(start=1000.0)
        identity = ConflictIdentity(scope="f.py", stage_oids=("a", "b"))
        prior = RebaseState(
            consecutive_conflicts=1,
            last_target="main",
            last_conflict_scope="f.py",
            last_conflict_stage_oids=("a", "b"),
        )
        record = RebaseState(
            last_outcome=RebaseOutcomeKind.CONFLICT,
        )

        result = apply_conflict_budget(
            record,
            prior=prior,
            target="main",
            resolver_suppressed=False,
            identity=identity,
            clock=clock.monotonic,
            cooldown_seconds=10.0,
        )

        assert result.consecutive_conflicts == 2
        assert result.retry_deadline == 1010.0

    def test_fast_forward_clears_deadline(self) -> None:
        prior = RebaseState(
            consecutive_conflicts=2,
            retry_deadline=500.0,
        )
        record = RebaseState(
            fast_forwarded=True,
            retry_deadline=500.0,
        )
        result = apply_conflict_budget(
            record,
            prior=prior,
            target="main",
            resolver_suppressed=False,
        )
        assert result.consecutive_conflicts == 0
        assert result.retry_deadline is None
