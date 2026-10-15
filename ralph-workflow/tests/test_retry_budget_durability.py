"""Tests verifying retry counters and cooldowns are durable, restart-surviving,
bounded, and respect clock boundaries.
"""

from __future__ import annotations

from ralph.pipeline._retry_progress_guard import RetryProgressGuard
from ralph.pipeline._runner_session import apply_session_capture, set_last_captured_session_id
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.effect_executor import set_last_captured_retry_intent
from ralph.pipeline.state import PipelineState
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
        set_last_captured_retry_intent(intent)

        updated_state = apply_session_capture(initial_state)
        assert updated_state.progress_guard == snap
        updated_chain = updated_state.chain_for_phase("development")
        assert updated_chain is not None
        assert updated_chain.progress_guard == snap

        # Success clears the saved progress guard
        set_last_captured_session_id("sess-success")
        success_state = apply_session_capture(updated_state)
        assert success_state.progress_guard is None
        success_chain = success_state.chain_for_phase("development")
        assert success_chain is not None
        assert success_chain.progress_guard is None
