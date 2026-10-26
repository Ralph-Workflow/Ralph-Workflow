"""Restart persistence for agent-failure counts: legacy checkpoint migration.

These tests cover the migration contract of legacy ``progress_guard`` failure
history into the durable agent-failure ledger:

* every legacy phase chain is migrated independently, with the chain's own
  phase and current agent as the ledger identity;
* when both a stored ledger snapshot and legacy history are present, the
  stricter (more demoted) state is preserved;
* legacy data that cannot be read fails closed (the affected agent is spent)
  instead of crashing or silently resetting its failure history;
* resuming mid-streak keeps only the remaining attempts.

Every assertion goes through the public seam: the run's observable behaviour
(which agent is selected next after the resume, how the run ends, what is
logged) and the owner's public answers (``AgentFailureLedger.status`` /
``is_available`` / ``record`` via ``RecoveryController.ledger``). No test reads
a serialised ledger entry, a private counter or an internal field: the
persisted snapshot is only ever handed back to the owner's public restore API
and is never indexed into.
"""

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

#: The demotion reason recorded when legacy failure history cannot be read.
LEGACY_MALFORMED_REASON = "legacy_guard_malformed"

#: The selection evidence a fail-closed agent leaves in the resumed run's log.
FAIL_CLOSED_SKIP_EVIDENCE = "skipped agent_a: unavailable"


def _migrated_state(payload: dict[str, object]) -> PipelineState:
    """Return the state a legacy checkpoint payload migrates into."""
    return PipelineState.model_validate(payload)


def _restored_controller(state: PipelineState, clock: FakeClock) -> RecoveryController:
    """Return the recovery owner a restart restores from the persisted state.

    The state's persisted ledger snapshot is handed back to the owner's public
    restore API. Nothing in this module indexes into that snapshot: the
    owner's own answers are the only facts the assertions below read.
    """
    snapshot = state.ledger_snapshot
    assert snapshot is not None, "the legacy checkpoint must migrate into a ledger snapshot"
    return RecoveryController.from_snapshot({"ledger": snapshot}, clock=clock)


def _stored_snapshot(phase: str, agent: str, failures: int) -> dict[str, object]:
    """Return a stored ledger snapshot carrying the given failure history."""
    ledger = AgentFailureLedger(clock=FakeClock(100.0))
    for _ in range(failures):
        ledger.record(AttemptFact(phase=phase, agent=agent, artifact_valid=False))
    return ledger.snapshot()


def _only_second_agent_succeeds(
    agent: str, _phase: str, _attempt: int, _workspace: object
) -> AgentAttemptResult:
    """Return a script where only the second agent of the chain succeeds."""
    return AgentAttemptResult(produce_artifact=agent == "agent_b")


def test_each_chain_migrates_to_its_own_ledger_entry() -> None:
    """A legacy checkpoint keeps one ledger entry per chain identity.

    The chain's own phase and current agent are the identity, so the review
    chain's exhausted history lands on the review chain's agent and nowhere
    else: the development chain's own agent keeps its own (unexhausted)
    history, and an agent no legacy chain declared carries no history at all.
    """
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

    # The development chain's history landed on that chain's current agent.
    assert ledger.status("development", "codex").is_eligible
    # The review chain's history landed on the review chain's agent ...
    assert ledger.status("review", "claude").is_spent
    # ... and on no other identity.
    assert ledger.status("review", "codex").is_eligible
    assert ledger.status("development", "claude").is_eligible


def test_chain_history_is_not_dropped_without_a_top_level_guard() -> None:
    """A checkpoint with only per-chain history still restores that history."""
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
    """The legacy retry intent carries the failing agent's history."""
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

    # The history belongs to the agent the retry intent names.
    assert ledger.status("development", "claude").is_spent
    assert ledger.status("development", "codex").is_eligible


def test_legacy_cap_is_honoured_when_it_is_stricter() -> None:
    """A legacy guard exhausted below the ledger cap still demotes."""
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
    """Legacy exhaustion is kept even when the stored snapshot is fresh."""
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
    """A spent stored entry survives a legacy payload that looks harmless."""
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
    """Migration adds entries without dropping the ones already stored."""
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

    # The stored identity keeps its own (still eligible) history ...
    assert ledger.status("development", "claude").is_eligible
    # ... and the migrated identity keeps its own (exhausted) history.
    assert ledger.status("review", "claude").is_spent


def test_non_mapping_payload_fails_closed(tmp_path: Path) -> None:
    """A legacy payload that is not a mapping is not a history reset.

    The unreadable payload leaves the affected agent spent, so the resumed
    run never hands it a fresh attempt budget: the chain falls straight over
    to the next agent, which completes the phase.
    """
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
    """A non-numeric streak is unreadable, not a zeroed streak."""
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

    # The unreadable streak is not a fresh budget: the agent is skipped.
    assert outcome.invocations == ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_unidentifiable_agent_preserves_history_for_every_declared_agent(
    tmp_path: Path,
) -> None:
    """A chain whose current agent is unknown keeps the history for all of them.

    The restored history is one failure short of the limit for every declared
    agent, so the resumed run demotes each of them after exactly one further
    attempt instead of the three a fresh agent would get.
    """
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

    # One attempt each: both declared agents carried the restored history.
    assert outcome.invocations == ["claude", "codex"]
    assert any("Agent 'claude' demoted in phase 'development'" in line for line in outcome.logs)
    assert any("Agent 'codex' demoted in phase 'development'" in line for line in outcome.logs)


def test_missing_chain_identity_fails_closed() -> None:
    """A chain without any agent identity still fails closed."""
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
    """An unreadable stored snapshot cannot silently protect nobody."""
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

    # The unreadable snapshot protects nobody and crashes nothing: the run
    # ends with a terminal outcome instead of a fresh attempt budget.
    assert outcome.invocations == ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0


def test_resume_keeps_only_the_remaining_attempts(tmp_path: Path) -> None:
    """A restart keeps the remaining attempts of an unfinished streak.

    The legacy streak left the agent one failure short of the limit, so the
    resumed run attempts the SAME agent again and demotes it after exactly
    the two remaining attempts -- neither resetting the streak nor counting
    it twice.
    """
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
    """An accepted artifact after the resume clears the restored history.

    The agent resumes with a history one failure short of the limit and then
    produces a valid artifact. The artifact is accepted, the run completes,
    and the restored history is observably gone: the agent is eligible again
    and its count is fresh, so it absorbs the whole consecutive limit again
    before it is demoted.
    """
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

    # The accepted artifact cleared the restored history.
    status = controller.ledger.status("development", "agent_a")
    assert status.is_eligible
    assert not status.is_spent

    # The count is fresh: two more failures are tolerated, the third demotes.
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
    """A workspace-changing agent stays bounded by the TOTAL limit after a resume.

    The agent changes the workspace on every attempt, so the consecutive count
    restarts every time and only the total count grows. The restored total
    count is one failure short of the limit, so the resumed run demotes the
    agent after the remaining eight attempts -- at the total limit, not at
    the consecutive one -- and the chain then falls over to the next agent.
    """
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

    # The demotion reason names the limit that was reached.
    assert any("total_unproductive_limit_reached" in line for line in outcome.logs)
    assert not any("consecutive_unproductive_limit_reached" in line for line in outcome.logs)


def test_restart_does_not_replenish_a_spent_agent(tmp_path: Path) -> None:
    """A demoted agent stays demoted across the restart.

    The resumed run never gives the spent agent another attempt: it is
    skipped by the selection, and the chain falls over to the next agent,
    which completes the phase.
    """
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
