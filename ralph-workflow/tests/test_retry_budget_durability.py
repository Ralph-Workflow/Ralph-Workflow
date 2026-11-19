from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline._runner_session import apply_session_capture, set_last_captured_session_id
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.effect_executor import set_last_captured_retry_intent
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_ledger_cooldown_growth_persists_across_restart (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ledger_exhaustion_persists_across_restart with an unchanged body"
    ),
    "test_ledger_cooldown_growth_persists_across_restart": "operator rule: an agent is either on cooldown or not; hitting the unproductive limits now starts an expiring unproductive_limit cooldown instead of a permanent Spent state, so the test asserts the cooldown, its expiry, and that the persisted backoff count lengthens the next cooldown after restart",
    "test_legacy_progress_guard_translation_fail_closed": "operator rule: an agent is either on cooldown or not; an exhausted or failed-closed legacy guard now migrates to counters at the limit instead of a permanent Spent state, so the next unproductive attempt starts an expiring cooldown",
    "test_pipeline_state_legacy_guard_migration": "operator rule: an agent is either on cooldown or not; ledger snapshot entries no longer carry is_spent, so the test asserts the migrated counters instead",
}


class TestRetryLedgerDurability:
    def test_ledger_progression_and_snapshot(self) -> None:
        clock = FakeClock(start=100.0)
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)
        assert ledger.status("development", "claude").is_eligible

        outcome1 = ledger.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert not outcome1.cooldown_started
        assert outcome1.status.is_eligible

        outcome2 = ledger.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert not outcome2.cooldown_started
        assert outcome2.status.is_eligible

        snap = ledger.snapshot()
        assert snap["consecutive_limit"] == 3
        assert snap["total_limit"] == 9
        assert "entries" in snap

        restored = AgentFailureLedger.from_snapshot(snap, clock=clock)
        assert restored.status("development", "claude").is_eligible
        assert restored.consecutive_limit == 3

        outcome3 = restored.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert outcome3.cooldown_started
        assert isinstance(outcome3.status, Cooldown)

    def test_ledger_cooldown_growth_persists_across_restart(self) -> None:
        clock = FakeClock(start=100.0)
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=4)

        for _ in range(2):
            ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
        first = ledger.status("development", "claude")
        assert isinstance(first, Cooldown)
        assert first.reason == "unproductive_limit"
        assert first.until_ms == 160_000
        clock.advance(61.0)
        assert ledger.is_available("development", "claude")

        for _ in range(2):
            ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
        second = ledger.status("development", "claude")
        assert isinstance(second, Cooldown)
        assert second.until_ms == int(clock.monotonic() * 1000) + 120_000

        restored = AgentFailureLedger.from_snapshot(ledger.snapshot(), clock=clock)
        clock.advance(121.0)
        assert restored.is_available("development", "claude")
        restored.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
        third = restored.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert isinstance(third.status, Cooldown)
        assert third.status.until_ms == int(clock.monotonic() * 1000) + 240_000
        assert not restored.is_available("development", "claude")
        clock.advance(241.0)
        assert restored.is_available("development", "claude")

    def test_legacy_progress_guard_translation_fail_closed(self) -> None:
        clock = FakeClock(start=100.0)

        exhausted_legacy = {
            "max_identical": 3,
            "streak": 3,
            "is_exhausted": True,
            "failed_closed": False,
        }
        ledger_exhausted = AgentFailureLedger.from_snapshot(
            exhausted_legacy,
            clock=clock,
        )
        assert ledger_exhausted.status("development", "claude").is_eligible
        exhausted_outcome = ledger_exhausted.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert isinstance(exhausted_outcome.status, Cooldown)
        assert exhausted_outcome.status.reason == "unproductive_limit"

        failed_closed_legacy = {
            "max_identical": 3,
            "streak": 1,
            "is_exhausted": False,
            "failed_closed": True,
        }
        ledger_failed = AgentFailureLedger.from_snapshot(
            failed_closed_legacy,
            clock=clock,
        )
        assert ledger_failed.status("development", "claude").is_eligible
        failed_outcome = ledger_failed.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert failed_outcome.cooldown_started
        clock.advance(61.0)
        assert ledger_failed.is_available("development", "claude")

        active_legacy = {
            "max_identical": 3,
            "streak": 2,
            "is_exhausted": False,
            "failed_closed": False,
        }
        ledger_active = AgentFailureLedger.from_snapshot(
            active_legacy,
            clock=clock,
        )
        assert ledger_active.is_available("development", "claude")
        outcome = ledger_active.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert outcome.cooldown_started

    def test_pipeline_state_legacy_guard_migration(self) -> None:
        legacy_data = {
            "phase": "development",
            "policy_entry_phase": "development",
            "phase_chains": {
                "development": {
                    "agents": ["claude"],
                    "current_index": 0,
                    "retries": 1,
                    "progress_guard": {"streak": 2, "max_identical": 3},
                }
            },
            "progress_guard": {"streak": 2, "max_identical": 3},
        }
        state = PipelineState.model_validate(legacy_data)
        assert state.ledger_snapshot is not None
        entries = state.ledger_snapshot.get("entries", {})
        assert entries.get("development:claude") is not None
        restored = AgentFailureLedger.from_snapshot(state.ledger_snapshot, clock=FakeClock(100.0))
        assert restored.status("development", "claude").is_eligible
        third = restored.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert third.cooldown_started

    def test_apply_session_capture_durability(self) -> None:
        initial_state = PipelineState(
            phase="development",
            policy_entry_phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"])},
        )

        intent = AgentRetryIntent(
            action="resume",
            session_id="sess-1",
        )
        set_last_captured_retry_intent(intent)

        updated_state = apply_session_capture(initial_state)
        assert updated_state.last_agent_session_id == "sess-1"
        assert updated_state.agent_retry_intent.action == "resume"

        set_last_captured_session_id("sess-success")
        success_state = apply_session_capture(updated_state)
        assert success_state.last_agent_session_id == "sess-success"
        assert success_state.agent_retry_intent.action is None
