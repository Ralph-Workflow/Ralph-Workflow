"""AgentBudgetRegistry and RecoveryController durability tests.

Split from :mod:`tests.test_retry_budget_durability` so neither module
carries more than one top-level test class (repo-structure policy).
"""

from __future__ import annotations

from typing import cast

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions


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


class TestRecoveryControllerDurability:
    """RecoveryController snapshot, restoration, and durability across restart."""

    def test_controller_snapshot_and_from_snapshot(self) -> None:
        clock = FakeClock(start=100.0)
        options = RecoveryControllerOptions(
            cycle_cap=50,
            technical_retry_cap=5,
            backoff_attempts={"claude": 2},
            unavailable_timeouts={"development:claude": 120_000},
            clock=clock,
        )
        controller = RecoveryController(options=options)
        snap = controller.snapshot()

        assert snap["cycle_cap"] == 50
        assert snap["technical_retry_cap"] == 5
        backoff_attempts = cast("dict[str, int]", snap["backoff_attempts"])
        unavailable_timeouts = cast("dict[str, int]", snap["unavailable_timeouts"])
        assert backoff_attempts["claude"] == 2
        assert unavailable_timeouts["development:claude"] == 120_000

        restored_controller = RecoveryController.from_snapshot(snap, clock=clock)
        restored_snap = restored_controller.snapshot()
        restored_backoff = cast("dict[str, int]", restored_snap["backoff_attempts"])
        assert restored_snap["cycle_cap"] == 50
        assert restored_snap["technical_retry_cap"] == 5
        assert restored_backoff["claude"] == 2

    def test_exhausted_agent_remains_spent_in_preferred_agent_selection(self) -> None:
        clock = FakeClock(start=100.0)
        budgets = {
            "development:claude": {
                "max_retries": 2,
                "consumed": 2,
                "remaining": 0,
                "exhausted": 1,
            }
        }
        options = RecoveryControllerOptions(
            budgets=budgets,
            clock=clock,
        )
        controller = RecoveryController(options=options)

        selection = controller.preferred_agent_index("development", ["claude", "gemini"])
        # claude's budget is exhausted, so claude is spent; gemini should be selected
        assert selection.agent == "gemini"
