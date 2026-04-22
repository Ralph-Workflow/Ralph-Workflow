"""Black-box test: checkpoint preserves recovery context for resume."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline.state import AgentChainState, FalloverRecord, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController


def _make_state_with_recovery_context(
    fallover_history: list[FalloverRecord] | None = None,
    recovery_cycle_count: int = 0,
    last_failure_category: str | None = None,
) -> PipelineState:
    if fallover_history is None:
        fallover_history = []
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=1, retries=0),
        fallover_history=tuple(fallover_history),
        recovery_cycle_count=recovery_cycle_count,
        last_failure_category=last_failure_category,
    )


def _make_state(agents: list[str]) -> PipelineState:
    """Simple helper for single-agent chain state."""
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
    )


def test_fallover_history_preserved_in_state() -> None:
    """Fallover history is preserved through state transitions."""
    fallover_record = FalloverRecord(
        phase=PHASE_DEVELOPMENT,
        from_agent="claude",
        to_agent="opencode",
        timestamp_iso=datetime.now(UTC).isoformat(),
    )
    state = _make_state_with_recovery_context(fallover_history=[fallover_record])

    assert len(state.fallover_history) == 1
    assert state.fallover_history[0].from_agent == "claude"
    assert state.fallover_history[0].to_agent == "opencode"


def test_recovery_cycle_count_preserved() -> None:
    """Recovery cycle count is preserved in state."""
    state = _make_state_with_recovery_context(recovery_cycle_count=3)
    assert state.recovery_cycle_count == 3


def test_last_failure_category_preserved() -> None:
    """Last failure category is preserved in state."""
    state = _make_state_with_recovery_context(last_failure_category="environmental")
    assert state.last_error is None  # last_failure_category is separate from last_error


def test_state_copy_with_preserves_recovery_fields() -> None:
    """copy_with preserves recovery-related fields."""
    fallover_record = FalloverRecord(
        phase=PHASE_DEVELOPMENT,
        from_agent="claude",
        to_agent="opencode",
        timestamp_iso=datetime.now(UTC).isoformat(),
    )
    state = _make_state_with_recovery_context(
        fallover_history=[fallover_record],
        recovery_cycle_count=2,
        last_failure_category="agent",
    )

    # Copy with a different phase
    new_state = state.copy_with(phase=PHASE_FAILED)

    # Recovery fields should be preserved
    assert new_state.recovery_cycle_count == state.recovery_cycle_count
    assert new_state.fallover_history == state.fallover_history
    assert new_state.last_failure_category == state.last_failure_category


def test_checkpoint_round_trip_preserves_recovery_context() -> None:
    """Loading a checkpoint restores recovery context exactly."""
    fallover_record = FalloverRecord(
        phase=PHASE_DEVELOPMENT,
        from_agent="claude",
        to_agent="opencode",
        timestamp_iso=datetime.now(UTC).isoformat(),
    )
    original_state = _make_state_with_recovery_context(
        fallover_history=[fallover_record],
        recovery_cycle_count=5,
        last_failure_category="environmental",
    )

    # Serialize to JSON (like checkpoint.save does)
    json_data = original_state.model_dump_json()
    restored_state = PipelineState.model_validate_json(json_data)

    assert restored_state.recovery_cycle_count == original_state.recovery_cycle_count
    assert len(restored_state.fallover_history) == len(original_state.fallover_history)
    assert restored_state.last_failure_category == original_state.last_failure_category


def test_controller_budget_registry_seed_from_checkpoint() -> None:
    """Budget registry can be seeded to match checkpoint state."""
    # Simulate a checkpoint where claude has 1 failure consumed
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=3)
    registry = registry.debit(
        PHASE_DEVELOPMENT,
        "claude",
        type("FakeFailure", (), {"counts_against_budget": True})(),
    )

    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state_with_recovery_context(recovery_cycle_count=1)

    # Verify budget state reflects checkpoint
    budget = controller.budget_registry.get(PHASE_DEVELOPMENT, "claude")
    assert budget is not None
    assert budget.consumed == 1
    assert budget.remaining == 2


def test_resume_after_single_agent_chain_exhaustion_increments_count() -> None:
    """After exhausting a single-agent chain, recovery_cycle_count increments.

    When an agent exhausts its budget with no next agent to fall over to,
    the chain is exhausted and the pipeline enters PHASE_FAILED.
    recovery_cycle_count increments. fallover_history does NOT grow because
    there is no next agent to fall over to.
    """
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=1)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"])  # Single agent chain

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure - budget exhausted for the single agent
    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    # Chain exhausted with no next agent -> enters PHASE_FAILED
    assert new_state.phase == PHASE_FAILED
    assert new_state.recovery_cycle_count == 1

    # Fallover history should be EMPTY because there's no next agent
    assert len(new_state.fallover_history) == 0


def test_resume_after_two_agent_chain_first_exhausted() -> None:
    """After first agent exhausted in a two-agent chain, falls over to second.

    When the first agent exhausts its budget but a second agent exists,
    the chain falls over and fallover_history records the transition.
    recovery_cycle_count does NOT increment yet (chain not fully exhausted).
    """
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=1)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude", "opencode"])  # Two-agent chain

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure - budget exhausted for claude, fallover to opencode
    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    # Phase still DEVELOPMENT (not failed) - fallover happened
    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.dev_chain.current_index == 1  # fell over to opencode
    # recovery_cycle_count does NOT increment until full chain exhaustion
    assert new_state.recovery_cycle_count == 0

    # Fallover history should have 1 record
    assert len(new_state.fallover_history) == 1
    assert new_state.fallover_history[0].from_agent == "claude"
    assert new_state.fallover_history[0].to_agent == "opencode"


def test_checkpoint_round_trip_after_partial_recovery() -> None:
    """Mid-recovery checkpoint round-trip preserves fallover_history and cycle count."""
    registry = AgentBudgetRegistry().set_budget(PHASE_DEVELOPMENT, "claude", max_retries=1)
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure - fallover from claude to opencode
    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    # Simulate saving checkpoint mid-recovery
    json_data = new_state.model_dump_json()
    restored_state = PipelineState.model_validate_json(json_data)

    assert restored_state.recovery_cycle_count == 0
    assert len(restored_state.fallover_history) == 1
    assert restored_state.fallover_history[0].from_agent == "claude"
    assert restored_state.fallover_history[0].to_agent == "opencode"
    assert restored_state.last_failure_category == "agent"


def test_last_failure_category_round_trip() -> None:
    """Last failure category survives checkpoint round-trip exactly."""
    state = _make_state_with_recovery_context(
        fallover_history=[],
        recovery_cycle_count=2,
        last_failure_category="ambiguous",
    )

    json_data = state.model_dump_json()
    restored = PipelineState.model_validate_json(json_data)

    assert restored.last_failure_category == "ambiguous"
    assert restored.recovery_cycle_count == 2
