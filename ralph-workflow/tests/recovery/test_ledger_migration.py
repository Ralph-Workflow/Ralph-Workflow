from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import (
    LEDGER_SNAPSHOT_MALFORMED_REASON,
    LEGACY_IDENTITY_UNATTRIBUTABLE_REASON,
    AgentFailureLedger,
    AttemptFact,
    Spent,
)
from ralph.recovery.controller import RecoveryController
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    run_scenario,
)

if TYPE_CHECKING:
    from pathlib import Path

LEGACY_MALFORMED_REASON = "legacy_guard_malformed"

FAIL_CLOSED_SKIP_EVIDENCE = "skipped agent_a: unavailable"


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
    assert ledger.status("review", "claude").is_spent
    assert ledger.status("review", "codex").is_eligible
    assert ledger.status("development", "claude").is_eligible


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

    status = ledger.status("development", "claude")
    assert status.is_spent
    assert not ledger.is_available("development", "claude")


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

    assert ledger.status("development", "claude").is_spent
    assert ledger.status("development", "codex").is_eligible


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

    assert ledger.status("development", "claude").is_spent
    assert not ledger.is_available("development", "claude")


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

    assert ledger.status("development", "claude").is_spent
    assert not ledger.is_available("development", "claude")


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
        "ledger_snapshot": _stored_snapshot("development", "claude", 9),
    }

    state = _migrated_state(legacy)
    ledger = _restored_controller(state, FakeClock(100.0)).ledger

    assert ledger.status("development", "claude").is_spent
    assert not ledger.is_available("development", "claude")


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

    assert ledger.status("development", "claude").is_eligible
    assert ledger.status("review", "claude").is_spent


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

    status = controller.ledger.status("development", "agent_a")
    assert isinstance(status, Spent)
    assert status.reason == LEGACY_MALFORMED_REASON

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_b"]
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

    status = controller.ledger.status("development", "agent_a")
    assert isinstance(status, Spent)
    assert status.reason == LEGACY_MALFORMED_REASON

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_b"]
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
    assert any("Agent 'claude' demoted in phase 'development'" in line for line in outcome.logs)
    assert any("Agent 'codex' demoted in phase 'development'" in line for line in outcome.logs)


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

    status = ledger.status("development", "claude")
    assert isinstance(status, Spent)
    assert status.reason == LEGACY_IDENTITY_UNATTRIBUTABLE_REASON
    assert not ledger.is_available("development", "claude")


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

    status = controller.ledger.status("development", "agent_a")
    assert isinstance(status, Spent)
    assert status.reason == LEDGER_SNAPSHOT_MALFORMED_REASON

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_b"]
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
    assert any("Agent 'agent_a' demoted in phase 'development'" in line for line in outcome.logs)


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
    assert not status.is_spent

    for _ in range(2):
        outcome_attempt = controller.ledger.record(
            AttemptFact(phase="development", agent="agent_a", artifact_valid=False)
        )
        assert not outcome_attempt.demoted
    final_attempt = controller.ledger.record(
        AttemptFact(phase="development", agent="agent_a", artifact_valid=False)
    )
    assert final_attempt.demoted


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

    assert any("total_unproductive_limit_reached" in line for line in outcome.logs)
    assert not any("consecutive_unproductive_limit_reached" in line for line in outcome.logs)


def test_restart_does_not_replenish_a_spent_agent(tmp_path: Path) -> None:
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

    assert not controller.ledger.is_available("development", "agent_a")

    outcome = run_scenario(
        tmp_path,
        script=_only_second_agent_succeeds,
        agents=("agent_a", "agent_b"),
        initial_state=state,
        controller=controller,
        clock=clock,
        max_iterations=10,
    )

    assert outcome.invocations == ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0
    assert any(FAIL_CLOSED_SKIP_EVIDENCE in line for line in outcome.logs)
