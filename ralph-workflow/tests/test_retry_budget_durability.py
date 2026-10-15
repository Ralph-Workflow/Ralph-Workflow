"""Tests verifying retry counters and cooldowns are durable, restart-surviving,
bounded, and respect clock boundaries.
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._retry_progress_guard import RetryProgressGuard
from ralph.pipeline.auto_integrate_backoff import (
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    compute_retry_deadline,
    compute_retry_delay,
)
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.recovery.seed_budget_registry import seed_budget_registry


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


class TestRetryProgressGuardDurability:
    """RetryProgressGuard durability and restart-survival tests."""

    def test_guard_progression_and_snapshot(self) -> None:
        guard = RetryProgressGuard(max_identical=3)
        assert guard.streak == 0
        assert not guard.is_exhausted

        must_stop = guard.record("sig1")
        assert not must_stop
        assert guard.streak == 1
        assert guard.last_signature == "sig1"

        snap = guard.snapshot()
        assert snap["max_identical"] == 3
        assert snap["streak"] == 1
        assert snap["last_signature"] == "sig1"
        assert not snap["is_exhausted"]

        restored = RetryProgressGuard.from_dict(snap)
        assert restored.max_identical == 3
        assert restored.streak == 1
        assert restored.last_signature == "sig1"
        assert not restored.is_exhausted

    def test_guard_exhaustion_persists_across_restart(self) -> None:
        guard = RetryProgressGuard(max_identical=2)
        assert not guard.record("err")
        assert guard.streak == 1
        # Second identical attempt on max_identical=2 triggers stop
        assert guard.record("err")
        assert guard.is_exhausted

        snap = guard.snapshot()
        assert snap["is_exhausted"]
        assert snap["streak"] == 2

        # Restarting from snapshot must NOT replenish attempts
        restored = RetryProgressGuard.from_dict(snap)
        assert restored.is_exhausted
        assert restored.record("err")


class TestAutoIntegrateBackoffClockBoundaries:
    """auto_integrate_backoff respects clock boundaries and bounds."""

    def test_compute_retry_delay_bounds(self) -> None:
        assert compute_retry_delay(0) == 0.0
        d1 = compute_retry_delay(1, jitter=lambda: 1.0)
        assert d1 == RETRY_BASE_DELAY_SECONDS
        d2 = compute_retry_delay(2, jitter=lambda: 1.0)
        assert d2 == RETRY_BASE_DELAY_SECONDS * 2.0
        d_large = compute_retry_delay(10, jitter=lambda: 1.0)
        assert d_large == RETRY_MAX_DELAY_SECONDS

    def test_compute_retry_deadline_uses_injected_clock(self) -> None:
        clock = FakeClock(start=100.0)
        deadline = compute_retry_deadline(
            1,
            clock=clock.monotonic,
            jitter=lambda: 1.0,
        )
        assert deadline == 100.0 + RETRY_BASE_DELAY_SECONDS

    def test_compute_retry_deadline_clock_object(self) -> None:
        clock = FakeClock(start=50.0)
        deadline = compute_retry_deadline(
            2,
            clock=clock,
            cooldown_seconds=5.0,
        )
        assert deadline == 55.0

    def test_compute_retry_deadline_rejects_none_clock(self) -> None:
        with pytest.raises(TypeError, match="clock must not be None"):
            compute_retry_deadline(1, clock=None)


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


class TestAgentBudgetRegistryDurability:
    """AgentBudgetRegistry serialization, deserialization, and durability."""

    def test_to_dict_and_from_dict(self) -> None:
        reg = AgentBudgetRegistry()
        reg = reg.set_budget("development", "claude", max_retries=3, consumed=1)
        failure = _make_failure(counts=True)
        reg = reg.debit("development", "claude", failure)
        state = reg.get("development", "claude")
        assert state is not None
        assert state.consumed == 2

        serialized = reg.to_dict()
        assert "development:claude" in serialized
        assert serialized["development:claude"]["consumed"] == 2
        assert serialized["development:claude"]["max_retries"] == 3

        restored = AgentBudgetRegistry.from_dict(serialized)
        restored_state = restored.get("development", "claude")
        assert restored_state is not None
        assert restored_state.consumed == 2
        assert restored_state.max_retries == 3
        assert restored_state.remaining == 1
        assert not restored.is_exhausted("development", "claude")

    def test_exhaustion_persists_across_restart(self) -> None:
        reg = AgentBudgetRegistry()
        reg = reg.set_budget("development", "claude", max_retries=1)
        failure = _make_failure(counts=True)
        reg = reg.debit("development", "claude", failure)
        assert reg.is_exhausted("development", "claude")

        serialized = reg.to_dict()
        restored = AgentBudgetRegistry.from_dict(serialized)
        # Restart must NOT replenish attempts
        assert restored.is_exhausted("development", "claude")
        res_state = restored.get("development", "claude")
        assert res_state is not None
        assert res_state.remaining == 0


class TestSeedBudgetRegistryWithPrior:
    """seed_budget_registry preserves consumed attempts from prior state."""

    def test_seed_preserves_consumed_budget(self) -> None:
        from ralph.policy.models import (
            AgentChainConfig,
            AgentDrainConfig,
            AgentsPolicy,
            ArtifactsPolicy,
            PhaseDefinition,
            PhaseTransition,
            PipelinePolicy,
            PolicyBundle,
            RecoveryPolicy,
        )

        phases = {
            "development": PhaseDefinition(
                drain="default",
                transitions=PhaseTransition(on_success="complete"),
            ),
        }
        chains = {
            "default": AgentChainConfig(agents=["claude", "gemini"], max_retries=3),
        }
        drains = {
            "default": AgentDrainConfig(chain="default"),
        }
        bundle = PolicyBundle(
            pipeline=PipelinePolicy(
                phases=phases,
                entry_phase="development",
                terminal_phase="complete",
                recovery=RecoveryPolicy(failed_route="failed_terminal"),
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
            agents=AgentsPolicy(agent_chains=chains, agent_drains=drains),
        )

        prior = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3, consumed=3)
        assert prior.is_exhausted("development", "claude")

        # Seed with prior
        seeded = seed_budget_registry(bundle, prior=prior)
        assert seeded.is_exhausted("development", "claude")
        claude_state = seeded.get("development", "claude")
        assert claude_state is not None
        assert claude_state.consumed == 3
        assert claude_state.remaining == 0

        # Unused agent starts with consumed=0
        gemini_state = seeded.get("development", "gemini")
        assert gemini_state is not None
        assert gemini_state.consumed == 0


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
