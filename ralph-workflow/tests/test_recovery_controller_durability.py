"""AgentBudgetRegistry and RecoveryController durability tests.

Split from :mod:`tests.test_retry_budget_durability` so neither module
carries more than one top-level test class (repo-structure policy).
"""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.checked_accessors import as_int, as_mapping
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
            backoff_attempts={"claude": 2},
            unavailable_timeouts={"development:claude": 120_000},
            clock=clock,
        )
        controller = RecoveryController(options=options)
        snap = controller.snapshot()

        assert snap["cycle_cap"] == 50
        backoff_attempts = {
            k: as_int(v, field=f"backoff_attempts.{k}")
            for k, v in as_mapping(snap["backoff_attempts"], field="backoff_attempts").items()
        }
        unavailable_timeouts = {
            k: as_int(v, field=f"unavailable_timeouts.{k}")
            for k, v in as_mapping(
                snap["unavailable_timeouts"], field="unavailable_timeouts"
            ).items()
        }
        assert backoff_attempts["claude"] == 2
        assert unavailable_timeouts["development:claude"] == 120_000

        restored_controller = RecoveryController.from_snapshot(snap, clock=clock)
        restored_snap = restored_controller.snapshot()
        restored_backoff = {
            k: as_int(v, field=f"backoff_attempts.{k}")
            for k, v in as_mapping(
                restored_snap["backoff_attempts"], field="backoff_attempts"
            ).items()
        }
        assert restored_snap["cycle_cap"] == 50
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
