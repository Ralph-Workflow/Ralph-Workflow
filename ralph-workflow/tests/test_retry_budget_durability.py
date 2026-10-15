"""Tests verifying retry counters and cooldowns are durable, restart-surviving,
bounded, and respect clock boundaries.
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._retry_progress_guard import RetryProgressGuard
from ralph.pipeline._runner_session import apply_session_capture, set_last_captured_session_id
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
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
from ralph.pipeline.effect_executor import _set_last_captured_retry_intent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
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

    def test_malformed_snapshots_fail_closed(self) -> None:
        """Non-mapping, missing required fields, or invalid streak/max fail closed."""
        # Non-mapping
        g1 = RetryProgressGuard.from_dict("not a dict")
        assert g1.is_exhausted is True
        assert g1.requires_operator_intervention is True
        assert g1.has_failed_closed is True
        assert g1.streak == g1.max_identical

        g_none = RetryProgressGuard.from_dict(None)
        assert g_none.is_exhausted is True
        assert g_none.has_failed_closed is True

        # Missing required fields
        g2 = RetryProgressGuard.from_dict({})
        assert g2.is_exhausted is True
        assert g2.has_failed_closed is True

        g3 = RetryProgressGuard.from_dict({"streak": 1})
        assert g3.is_exhausted is True
        assert g3.has_failed_closed is True

        # Negative streak
        g4 = RetryProgressGuard.from_dict({"max_identical": 3, "streak": -1})
        assert g4.is_exhausted is True
        assert g4.has_failed_closed is True

        # Invalid max_identical (< 1 or non-integer)
        g5 = RetryProgressGuard.from_dict({"max_identical": 0, "streak": 0})
        assert g5.is_exhausted is True
        assert g5.has_failed_closed is True

        g6 = RetryProgressGuard.from_dict({"max_identical": "invalid", "streak": 0})
        assert g6.is_exhausted is True
        assert g6.has_failed_closed is True

        # Non-integer streak
        g7 = RetryProgressGuard.from_dict({"max_identical": 3, "streak": "abc"})
        assert g7.is_exhausted is True
        assert g7.has_failed_closed is True

    def test_contradictory_snapshots_fail_closed(self) -> None:
        """Contradictory is_exhausted flags fail closed to intervention."""
        # is_exhausted=True but streak=0 (contradiction: claims exhausted but streak is 0)
        c1 = RetryProgressGuard.from_dict({"max_identical": 3, "streak": 0, "is_exhausted": True})
        assert c1.is_exhausted is True
        assert c1.has_failed_closed is True
        assert c1.streak == 3
        assert c1.requires_operator_intervention is True

        # is_exhausted=False but streak=3 (contradiction: streak reached max but claims not exhausted)
        c2 = RetryProgressGuard.from_dict({"max_identical": 3, "streak": 3, "is_exhausted": False})
        assert c2.is_exhausted is True
        assert c2.has_failed_closed is True
        assert c2.streak == 3
        assert c2.requires_operator_intervention is True

    def test_exhausted_guard_does_not_replenish_on_different_signature(self) -> None:
        """Once exhausted, recording a different signature does NOT replenish budget."""
        guard = RetryProgressGuard(max_identical=2)
        guard.record("sig1")
        guard.record("sig1")
        assert guard.is_exhausted is True

        # A different signature must NOT un-exhaust the guard
        assert guard.record("sig2") is True
        assert guard.is_exhausted is True
        assert guard.requires_operator_intervention is True

        # Genuine forward progress via reset clears exhaustion
        guard.reset()
        assert guard.is_exhausted is False
        assert guard.streak == 0
        assert guard.last_signature is None

    def test_pipeline_state_and_chain_state_guard_roundtrip(self) -> None:
        """State schemas persist RetryProgressGuard snapshots through serialization."""
        guard = RetryProgressGuard(max_identical=3)
        guard.record("sig1")
        guard.record("sig1")
        snap = guard.snapshot()

        chain = AgentChainState(agents=["claude"], current_index=0, retries=2, progress_guard=snap)
        assert chain.progress_guard == snap

        # with_retry_increment preserves progress_guard
        inc_chain = chain.with_retry_increment()
        assert inc_chain.progress_guard == snap
        assert inc_chain.retries == 3

        # with_advance clears progress_guard
        adv_chain = chain.with_advance()
        assert adv_chain.progress_guard is None
        assert adv_chain.retries == 0

        # PipelineState serialization preserves progress_guard in phase_chains and on state
        state = PipelineState(
            phase="development",
            policy_entry_phase="development",
            phase_chains={"development": chain},
            progress_guard=snap,
        )
        json_data = state.model_dump_json()
        restored = PipelineState.model_validate_json(json_data)

        assert restored.progress_guard is not None
        assert restored.progress_guard["streak"] == 2
        restored_chain = restored.chain_for_phase("development")
        assert restored_chain is not None
        assert restored_chain.progress_guard is not None
        assert restored_chain.progress_guard["streak"] == 2

    def test_apply_session_capture_durability(self) -> None:
        """apply_session_capture writes captured guard to state and chain."""
        guard = RetryProgressGuard(max_identical=2)
        guard.record("fail")
        guard.record("fail")
        snap = guard.snapshot()

        initial_state = PipelineState(
            phase="development",
            policy_entry_phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"])},
        )

        intent = AgentRetryIntent(
            action="resume",
            session_id="sess-1",
            progress_guard=snap,
        )
        _set_last_captured_retry_intent(intent)

        updated_state = apply_session_capture(initial_state)
        assert updated_state.progress_guard == snap
        assert updated_state.chain_for_phase("development") is not None
        assert updated_state.chain_for_phase("development").progress_guard == snap  # type: ignore[union-attr]

        # Success clears the saved progress guard
        set_last_captured_session_id("sess-success")
        success_state = apply_session_capture(updated_state)
        assert success_state.progress_guard is None
        assert success_state.chain_for_phase("development") is not None
        assert success_state.chain_for_phase("development").progress_guard is None  # type: ignore[union-attr]


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
