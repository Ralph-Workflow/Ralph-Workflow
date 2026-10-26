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

Every assertion goes through the public seam (``PipelineState`` fields,
``AgentFailureLedger.status`` / ``record`` / ``snapshot``) — no test reads a
private ledger entry.
"""

from __future__ import annotations

from collections.abc import Mapping

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
    Spent,
)


def _entries(state: PipelineState) -> dict[str, dict[str, object]]:
    """Return the migrated ledger entries of a state, keyed by identity."""
    snapshot = state.ledger_snapshot
    assert snapshot is not None
    raw_entries = snapshot.get("entries")
    assert isinstance(raw_entries, Mapping)
    return {
        str(key): dict(value) for key, value in raw_entries.items() if isinstance(value, Mapping)
    }


def _entry(state: PipelineState, identity: str) -> dict[str, object]:
    """Return one migrated ledger entry, failing loudly when it is missing."""
    entries = _entries(state)
    assert identity in entries, f"missing migrated ledger entry: {identity}"
    return entries[identity]


def _ledger(state: PipelineState) -> AgentFailureLedger:
    """Return a ledger restored from the state's migrated snapshot."""
    snapshot = state.ledger_snapshot
    assert snapshot is not None
    return AgentFailureLedger.from_snapshot(snapshot, clock=FakeClock(100.0))


def _ledger_snapshot(phase: str, agent: str, failures: int) -> dict[str, object]:
    """Return a stored ledger snapshot carrying the given failure history."""
    ledger = AgentFailureLedger(clock=FakeClock(100.0))
    for _ in range(failures):
        ledger.record(AttemptFact(phase=phase, agent=agent, artifact_valid=False))
    return ledger.snapshot()

    """Legacy per-chain failure history reaches the ledger with its identity."""


def test_each_chain_migrates_to_its_own_ledger_entry() -> None:
    """A legacy checkpoint keeps one ledger entry per chain identity."""
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

    state = PipelineState.model_validate(legacy)

    entries = _entries(state)
    assert set(entries) == {"development:codex", "review:claude"}
    assert _entry(state, "development:codex").get("agent") == "codex"
    assert _entry(state, "review:claude").get("phase") == "review"

    ledger = _ledger(state)
    assert ledger.status("development", "codex").is_eligible
    assert isinstance(ledger.status("review", "claude"), Spent)


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

    state = PipelineState.model_validate(legacy)

    assert _entry(state, "development:claude").get("is_spent") is True
    assert isinstance(_ledger(state).status("development", "claude"), Spent)


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

    state = PipelineState.model_validate(legacy)

    assert _entry(state, "development:claude").get("is_spent") is True


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

    state = PipelineState.model_validate(legacy)

    assert _entry(state, "development:claude").get("is_spent") is True

    """A stored ledger snapshot and legacy history never weaken each other."""


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
        "ledger_snapshot": _ledger_snapshot("development", "claude", 0),
    }

    state = PipelineState.model_validate(legacy)

    assert _entry(state, "development:claude").get("is_spent") is True
    assert isinstance(_ledger(state).status("development", "claude"), Spent)


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
        "ledger_snapshot": _ledger_snapshot("development", "claude", 9),
    }

    state = PipelineState.model_validate(legacy)

    assert _entry(state, "development:claude").get("is_spent") is True
    assert isinstance(_ledger(state).status("development", "claude"), Spent)


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
        "ledger_snapshot": _ledger_snapshot("development", "claude", 1),
    }

    state = PipelineState.model_validate(legacy)

    entries = _entries(state)
    assert "development:claude" in entries
    assert "review:claude" in entries
    ledger = _ledger(state)
    assert ledger.status("development", "claude").is_eligible
    assert isinstance(ledger.status("review", "claude"), Spent)

    """Unreadable legacy data never hands back a fresh attempt budget."""


def test_non_mapping_payload_fails_closed() -> None:
    """A legacy payload that is not a mapping is not a history reset."""
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {"development": {"agents": ["claude"], "current_index": 0}},
        "progress_guard": "not-a-mapping",
    }

    state = PipelineState.model_validate(legacy)

    entry = _entry(state, "development:claude")
    assert entry.get("is_spent") is True
    assert entry.get("spent_reason") == "legacy_guard_malformed"
    assert isinstance(_ledger(state).status("development", "claude"), Spent)


def test_unreadable_streak_fails_closed_without_crashing() -> None:
    """A non-numeric streak is unreadable, not a zeroed streak."""
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": "many", "max_identical": 3},
            }
        },
    }

    state = PipelineState.model_validate(legacy)

    entry = _entry(state, "development:claude")
    assert entry.get("is_spent") is True
    assert entry.get("spent_reason") == "legacy_guard_malformed"


def test_unidentifiable_agent_preserves_history_for_every_declared_agent() -> None:
    """A chain whose current agent is unknown keeps the history for all of them."""
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

    state = PipelineState.model_validate(legacy)
    ledger = _ledger(state)

    for agent in ("claude", "codex"):
        assert ledger.status("development", agent).is_eligible
        outcome = ledger.record(AttemptFact(phase="development", agent=agent, artifact_valid=False))
        assert outcome.demoted
        assert isinstance(outcome.status, Cooldown)


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

    state = PipelineState.model_validate(legacy)

    entry = _entry(state, "development:claude")
    assert entry.get("is_spent") is True
    assert entry.get("spent_reason") == "legacy_identity_unattributable"
    assert isinstance(_ledger(state).status("development", "claude"), Spent)


def test_unreadable_stored_snapshot_fails_closed() -> None:
    """An unreadable stored snapshot cannot silently protect nobody."""
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {"development": {"agents": ["claude"], "current_index": 0}},
        "ledger_snapshot": "not-a-mapping",
    }

    state = PipelineState.model_validate(legacy)

    entry = _entry(state, "development:claude")
    assert entry.get("is_spent") is True
    assert entry.get("spent_reason") == "ledger_snapshot_malformed"

    """A restart keeps the remaining attempts of an unfinished streak."""


def test_resume_keeps_only_the_remaining_attempts() -> None:
    """The restored streak is neither reset nor counted twice."""
    legacy = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "progress_guard": {"streak": 1, "max_identical": 3},
            }
        },
    }

    state = PipelineState.model_validate(legacy)
    ledger = _ledger(state)
    assert ledger.status("development", "claude").is_eligible

    first = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert not first.demoted
    second = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert second.demoted
    assert isinstance(second.status, Cooldown)


def test_restart_does_not_replenish_a_spent_agent() -> None:
    """A demoted agent stays demoted across the restart."""
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
    }

    state = PipelineState.model_validate(legacy)
    ledger = _ledger(state)

    assert not ledger.is_available("development", "claude")
    outcome = ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert outcome.demoted
    assert isinstance(ledger.status("development", "claude"), Spent)
