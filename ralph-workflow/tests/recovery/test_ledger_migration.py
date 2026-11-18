from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
)
from ralph.recovery.controller import RecoveryController
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path

UNPRODUCTIVE_LIMIT = str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)

FAIL_CLOSED_SKIP_EVIDENCE = "agent_a: cooldown ("


def _migrated_state(payload: dict[str, object]) -> PipelineState:
    return PipelineState.model_validate(payload)


def _restored_controller(state: PipelineState, clock: FakeClock) -> RecoveryController:
    snapshot = state.ledger_snapshot
    assert snapshot is not None, "the legacy checkpoint must migrate into a ledger snapshot"
    return RecoveryController.from_snapshot({"ledger": snapshot}, clock=clock)


def _stored_snapshot(phase: str, agent: str, failures: int) -> dict[str, object]:
    ledger = AgentFailureLedger(clock=FakeClock(100.0))
    for _ in range(failures):
        ledger.record(AttemptFact(phase=phase, agent=agent, artifact_valid=False))
    return ledger.snapshot()


def _only_second_agent_succeeds(
    agent: str, _phase: str, _attempt: int, _workspace: object
) -> AgentAttemptResult:
    return AgentAttemptResult(produce_artifact=agent == "agent_b")


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_each_chain_migrates_to_its_own_ledger_entry": "operator rule: an agent is either on cooldown or not; an exhausted legacy guard now migrates to counters at the limit instead of a Spent entry, so the test asserts the per-chain counters and that the next unproductive attempt starts an expiring unproductive_limit cooldown",
    "test_chain_history_is_not_dropped_without_a_top_level_guard": "operator rule: an agent is either on cooldown or not; an exhausted legacy chain guard migrates to counters at the limit, so the preserved history is proven by the next unproductive attempt starting a cooldown instead of a Spent status",
    "test_retry_intent_history_reaches_the_ledger": "operator rule: an agent is either on cooldown or not; exhausted retry intent history migrates to counters at the limit, so it is proven by the next unproductive attempt starting a cooldown instead of a Spent status",
    "test_legacy_cap_is_honoured_when_it_is_stricter": "operator rule: an agent is either on cooldown or not; a stricter legacy cap migrates to counters at that cap, so it is proven by the next unproductive attempt starting a cooldown instead of a Spent status",
    "test_legacy_history_wins_when_it_is_stricter": "operator rule: an agent is either on cooldown or not; stricter legacy history migrates to counters at the limit, so it is proven by the next unproductive attempt starting a cooldown instead of a Spent status",
    "test_stored_snapshot_wins_when_it_is_stricter": "operator rule: an agent is either on cooldown or not; a ledger resets its counters when it starts a cooldown, so the stricter stored snapshot is built from two unproductive attempts and proven by the next attempt starting a cooldown instead of a Spent status",
    "test_stored_entries_for_other_identities_are_preserved": "operator rule: an agent is either on cooldown or not; migrated entries hold counters only and the tracker is keyed by agent, so the test asserts each phase keeps its own counters and the at-limit identity cools on its next unproductive attempt",
    "test_non_mapping_payload_fails_closed": "operator rule: an agent is either on cooldown or not; a malformed legacy guard now fails closed to counters at the limit instead of a permanent Spent status, so the agent is tried once, cooled with an expiring cooldown, and the run falls over to the next agent",
    "test_unreadable_streak_fails_closed_without_crashing": "operator rule: an agent is either on cooldown or not; an unreadable legacy streak now fails closed to counters at the limit instead of a permanent Spent status, so the agent is tried once, cooled, and the run falls over to the next agent",
    "test_unidentifiable_agent_preserves_history_for_every_declared_agent": "operator rule: an agent is either on cooldown or not; the demoted log line no longer exists, so the preserved history is proven by the cooldown_decision log for each declared agent",
    "test_missing_chain_identity_fails_closed": "operator rule: an agent is either on cooldown or not; a missing chain identity now fails closed to counters at the limit instead of a Spent status, so the default agent cools on its next unproductive attempt",
    "test_unreadable_stored_snapshot_fails_closed": "operator rule: an agent is either on cooldown or not; an unreadable stored snapshot now fails closed to counters at the limit instead of a permanent Spent status, so the agent is tried once, cooled, and the run falls over to the next agent",
    "test_resume_keeps_only_the_remaining_attempts": "operator rule: an agent is either on cooldown or not; the demoted log line no longer exists, so the limit is proven by the unproductive_limit cooldown_decision log",
    "test_accepted_artifact_after_resume_clears_the_history": "operator rule: an agent is either on cooldown or not; AttemptOutcome.demoted and is_spent no longer exist, so the cleared history is asserted through cooldown_started",
    "test_workspace_changing_agent_after_resume_is_bounded_by_the_total_limit": "operator rule: an agent is either on cooldown or not; the total and consecutive reason strings collapsed into unproductive_limit, so the total-limit trigger is proven by the cooldown_decision counts consecutive=0 total=9",
    "test_restart_does_not_replenish_an_exhausted_agent_budget": "operator rule: an agent is either on cooldown or not; an exhausted legacy guard is no longer a permanent Spent state; restart keeps the counters at the limit so a single unproductive attempt cools the agent with an expiring cooldown and the run falls over",
}


def _next_unproductive_attempt_cools(ledger: AgentFailureLedger, phase: str, agent: str) -> None:
    outcome = ledger.record(AttemptFact(phase=phase, agent=agent, artifact_valid=False))
    assert isinstance(outcome.status, Cooldown)
    assert outcome.status.reason == UNPRODUCTIVE_LIMIT
    status = ledger.status(phase, agent)
    assert isinstance(status, Cooldown)
    assert status.until_ms > int(ledger.clock.monotonic() * 1000)
    ledger.clock.advance((status.until_ms - int(ledger.clock.monotonic() * 1000)) / 1000.0)
    assert ledger.status(phase, agent).is_eligible


def _entries(ledger: AgentFailureLedger) -> dict[str, object]:
    entries = ledger.snapshot()["entries"]
    assert isinstance(entries, dict)
    return entries


def _attempts_until_cooldown(ledger: AgentFailureLedger, phase: str, agent: str) -> int:
    clone = AgentFailureLedger.from_snapshot(ledger.snapshot(), clock=FakeClock(0.0))
    for attempt in range(1, clone.total_limit + 1):
        fact = AttemptFact(phase=phase, agent=agent, artifact_valid=False)
        if clone.record(fact).cooldown_started:
            return attempt
    return clone.total_limit + 1


def test_each_chain_migrates_to_its_own_ledger_entry() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude", "codex"],
                "current_index": 1,
                "retries": 2,
                "progress_guard": {"streak": 2, "max_identical": 3},
            },
            "review": {
                "agents": ["claude"],
                "current_index": 0,
                "retries": 1,
                "progress_guard": {"streak": 3, "max_identical": 3},
            },
        },
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert ledger.status("development", "codex").is_eligible
    assert ledger.status("review", "claude").is_eligible
    assert _attempts_until_cooldown(ledger, "development", "codex") == 1
    assert _attempts_until_cooldown(ledger, "review", "claude") == 1
    assert "review:codex" not in _entries(ledger)
    assert "development:claude" not in _entries(ledger)
    _next_unproductive_attempt_cools(ledger, "review", "claude")


def test_chain_history_is_not_dropped_without_a_top_level_guard() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "retries": 2,
                "progress_guard": {"streak": 3, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert _attempts_until_cooldown(ledger, "development", "claude") == 1
    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_retry_intent_history_reaches_the_ledger() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {"development": {"agents": ["claude", "codex"], "current_index": 1}},
        "agent_retry_intent": {
            "action": "fresh",
            "failure_reason": "boom",
            "failed_agent_name": "claude",
            "progress_guard": {"streak": 3, "max_identical": 3},
        },
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert ledger.status("development", "codex").is_eligible
    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_legacy_cap_is_honoured_when_it_is_stricter() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": 2, "max_identical": 2},
            }
        },
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_legacy_history_wins_when_it_is_stricter() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": 3, "max_identical": 3},
            }
        },
        "ledger_snapshot": _stored_snapshot("development", "claude", 0),
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_stored_snapshot_wins_when_it_is_stricter() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": 0, "max_identical": 3},
            }
        },
        "ledger_snapshot": _stored_snapshot("development", "claude", 2),
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_stored_entries_for_other_identities_are_preserved() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "review": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": 3, "max_identical": 3},
            }
        },
        "ledger_snapshot": _stored_snapshot("development", "claude", 1),
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert _attempts_until_cooldown(ledger, "development", "claude") == 2
    assert _attempts_until_cooldown(ledger, "review", "claude") == 1
    development = ledger.record(
        AttemptFact(phase="development", agent="claude", artifact_valid=False)
    )
    assert development.status.is_eligible
    assert _attempts_until_cooldown(ledger, "development", "claude") == 1
    _next_unproductive_attempt_cools(ledger, "review", "claude")


def test_non_mapping_payload_fails_closed(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {"development": {"agents": ["agent_a", "agent_b"], "current_index": 0}},
        "progress_guard": "not-a-mapping",
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    assert controller.ledger.status("development", "agent_a").is_eligible
    assert _attempts_until_cooldown(controller.ledger, "development", "agent_a") == 1

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0
    assert any(FAIL_CLOSED_SKIP_EVIDENCE in line for line in outcome.logs)


def test_unreadable_streak_fails_closed_without_crashing(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["agent_a", "agent_b"],
                "current_index": 0,
                "progress_guard": {"streak": "many", "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    assert controller.ledger.status("development", "agent_a").is_eligible
    assert _attempts_until_cooldown(controller.ledger, "development", "agent_a") == 1

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_unidentifiable_agent_preserves_history_for_every_declared_agent(
    tmp_path: Path,
) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude", "codex"],
                "current_index": 7,
                "progress_guard": {"streak": 2, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    assert controller.ledger.status("development", "claude").is_eligible
    assert controller.ledger.status("development", "codex").is_eligible

    outcome = run_scenario(
        tmp_path,
        script=lambda agent, _phase, _attempt, _workspace: AgentAttemptResult(
            produce_artifact=False
        ),
        agents=("claude", "codex"),
        initial_state=state,
        controller=controller,
        clock=clock,
        stop_after_invocations=2,
        max_iterations=10,
    )

    assert outcome.invocations == ["claude", "codex"]
    for agent in ("claude", "codex"):
        assert any(
            f"cooldown_decision phase=development agent={agent} reason=unproductive_limit" in line
            for line in outcome.logs
        )


def test_missing_chain_identity_fails_closed() -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "current_index": 0,
                "progress_guard": {"streak": 2, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert ledger.status("development", "claude").is_eligible
    assert _attempts_until_cooldown(ledger, "development", "claude") == 1
    _next_unproductive_attempt_cools(ledger, "development", "claude")


def test_unreadable_stored_snapshot_fails_closed(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {"development": {"agents": ["agent_a", "agent_b"], "current_index": 0}},
        "ledger_snapshot": "not-a-mapping",
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    assert controller.ledger.status("development", "agent_a").is_eligible
    assert _attempts_until_cooldown(controller.ledger, "development", "agent_a") == 1

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_resume_keeps_only_the_remaining_attempts(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["agent_a", "agent_b"],
                "current_index": 0,
                "retries": 0,
                "progress_guard": {"streak": 1, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0
    assert any(
        "cooldown_decision phase=development agent=agent_a reason=unproductive_limit" in line
        and "consecutive=3 total=3" in line
        for line in outcome.logs
    )


def test_accepted_artifact_after_resume_clears_the_history(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["agent_a"],
                "current_index": 0,
                "retries": 0,
                "progress_guard": {"streak": 2, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    outcome = run_scenario(
        tmp_path,
        script=lambda _agent, _phase, _attempt, _workspace: AgentAttemptResult(
            produce_artifact=True
        ),
        agents=("agent_a",),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=5,
    )

    assert outcome.invocations == ["agent_a"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    status = controller.ledger.status("development", "agent_a")
    assert status.is_eligible

    for _ in range(2):
        outcome_attempt = controller.ledger.record(
            AttemptFact(phase="development", agent="agent_a", artifact_valid=False)
        )
        assert not outcome_attempt.cooldown_started
    final_attempt = controller.ledger.record(
        AttemptFact(phase="development", agent="agent_a", artifact_valid=False)
    )
    assert final_attempt.cooldown_started


def test_workspace_changing_agent_after_resume_is_bounded_by_the_total_limit(
    tmp_path: Path,
) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["agent_a", "agent_b"],
                "current_index": 0,
                "retries": 0,
                "progress_guard": {"streak": 1, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "agent_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(produce_artifact=False, modify_workspace=True)

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=20,
    )

    assert outcome.invocations == ["agent_a"] * 8 + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    cooldown_lines = [line for line in outcome.logs if "cooldown_decision" in line]
    assert any(
        "agent=agent_a reason=unproductive_limit" in line and "consecutive=0 total=9" in line
        for line in cooldown_lines
    )


def test_restart_does_not_replenish_an_exhausted_agent_budget(tmp_path: Path) -> None:
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["agent_a", "agent_b"],
                "current_index": 0,
                "retries": 0,
                "progress_guard": {"streak": 3, "max_identical": 3},
            }
        },
    }

    state = _migrated_state(legacy)
    clock = FakeClock(start=0.0)
    controller = _restored_controller(state, clock)

    assert _attempts_until_cooldown(controller.ledger, "development", "agent_a") == 1

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_a", "agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0
    assert any(FAIL_CLOSED_SKIP_EVIDENCE in line for line in outcome.logs)
