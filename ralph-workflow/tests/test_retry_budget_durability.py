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
    Spent,
)


class TestRetryLedgerDurability:
    def test_ledger_progression_and_snapshot(self) -> None:
        clock = FakeClock(start=100.0)
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)
        assert ledger.status("development", "claude").is_eligible

        outcome1 = ledger.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert not outcome1.demoted
        assert outcome1.status.is_eligible

        outcome2 = ledger.record(
            AttemptFact(phase="development", agent="claude", artifact_valid=False)
        )
        assert not outcome2.demoted
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
        assert outcome3.demoted
        assert isinstance(outcome3.status, Cooldown)

    def test_ledger_exhaustion_persists_across_restart(self) -> None:
        clock = FakeClock(start=100.0)
        ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=4)

        for _ in range(4):
            ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))

        st = ledger.status("development", "claude")
        assert isinstance(st, Spent)
        assert st.is_spent

        snap = ledger.snapshot()
        restored = AgentFailureLedger.from_snapshot(snap, clock=clock)
        restored_st = restored.status("development", "claude")
        assert isinstance(restored_st, Spent)
        assert restored_st.is_spent
        assert not restored.is_available("development", "claude")

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
        st = ledger_exhausted.status("development", "claude")
        assert isinstance(st, Spent)
        assert st.is_spent

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
        assert ledger_failed.status("development", "claude").is_spent

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
        assert outcome.demoted

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
        entry = entries.get("development:claude")
        assert entry is not None
        assert entry["is_spent"] is False
        restored = AgentFailureLedger.from_snapshot(state.ledger_snapshot, clock=FakeClock(100.0))
        assert restored.status("development", "claude").is_eligible

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
