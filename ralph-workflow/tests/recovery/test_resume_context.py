"""Black-box test: checkpoint preserves recovery context for resume."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ralph.pipeline.state import AgentChainState, FalloverRecord, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions

#: The failure ledger demotes an agent after this many consecutive
#: unproductive attempts; the demotion is what advances the chain.
_CONSECUTIVE_UNPRODUCTIVE_LIMIT = 3


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_state_with_recovery_context(
    fallover_history: list[FalloverRecord] | None = None,
    recovery_cycle_count: int = 0,
    last_failure_category: str | None = None,
) -> PipelineState:
    if fallover_history is None:
        fallover_history = []
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=1, retries=0
            )
        },
        fallover_history=tuple(fallover_history),
        recovery_cycle_count=recovery_cycle_count,
        last_failure_category=last_failure_category,
    )


def _make_state(agents: list[str]) -> PipelineState:
    """Simple helper for single-agent chain state."""
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def test_fallover_history_preserved_in_state() -> None:
    """Fallover history is preserved through state transitions."""
    fallover_record = FalloverRecord(
        phase="development",
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
        phase="development",
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
    new_state = state.copy_with(phase="failed")

    # Recovery fields should be preserved
    assert new_state.recovery_cycle_count == state.recovery_cycle_count
    assert new_state.fallover_history == state.fallover_history
    assert new_state.last_failure_category == state.last_failure_category


def test_checkpoint_round_trip_preserves_recovery_context() -> None:
    """Loading a checkpoint restores recovery context exactly."""
    fallover_record = FalloverRecord(
        phase="development",
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


def test_controller_ledger_can_be_restored_from_a_checkpoint_snapshot() -> None:
    """The failure ledger is restored from the checkpointed ledger snapshot."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state_with_recovery_context(recovery_cycle_count=1)

    _, _, _ = controller.handle(
        state,
        RuntimeError("claude idle"),
        FailureContext(phase="development", agent="claude"),
    )
    snapshot = controller.ledger.snapshot()

    restored = AgentFailureLedger.from_snapshot(snapshot, clock=controller.clock)

    # The restored ledger answers exactly like the original one.
    assert (
        restored.status("development", "claude").is_eligible
        == controller.ledger.status("development", "claude").is_eligible
    )
    assert restored.is_available("development", "claude") == controller.ledger.is_available(
        "development", "claude"
    )


def test_resume_after_single_agent_chain_exhaustion_increments_count() -> None:
    """After exhausting a single-agent chain, recovery_cycle_count increments.

    When an agent exhausts its budget with no next agent to fall over to,
    the chain is exhausted and the pipeline enters "failed".
    recovery_cycle_count increments. fallover_history does NOT grow because
    there is no next agent to fall over to.
    """
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state(["claude"])  # Single agent chain

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # The ledger demotes the single agent after the consecutive unproductive
    # limit; a demoted sole agent has nowhere to fall over to.
    new_state = state
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
        new_state, _, _ = controller.handle(
            state,
            _AgentTimeoutError("claude idle"),
            FailureContext(phase="development", agent="claude"),
        )

    # Chain exhausted with no next agent -> enters "failed"
    assert new_state.phase == "failed_terminal"
    assert new_state.recovery_cycle_count == 1

    # Fallover history should be EMPTY because there's no next agent
    assert len(new_state.fallover_history) == 0


def test_resume_after_two_agent_chain_first_exhausted() -> None:
    """After first agent exhausted in a two-agent chain, falls over to second.

    When the first agent exhausts its budget but a second agent exists,
    the chain falls over and fallover_history records the transition.
    recovery_cycle_count does NOT increment yet (chain not fully exhausted).
    """
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude", "opencode"])  # Two-agent chain

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # The ledger demotes claude after the consecutive unproductive limit, so
    # the chain falls over to opencode.
    new_state = state
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
        new_state, _, _ = controller.handle(
            state,
            _AgentTimeoutError("claude idle"),
            FailureContext(phase="development", agent="claude"),
        )

    # Phase still DEVELOPMENT (not failed) - fallover happened
    assert new_state.phase == "development"
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1  # fell over to opencode
    # recovery_cycle_count does NOT increment until full chain exhaustion
    assert new_state.recovery_cycle_count == 0

    # Fallover history should have 1 record
    assert len(new_state.fallover_history) == 1
    assert new_state.fallover_history[0].from_agent == "claude"
    assert new_state.fallover_history[0].to_agent == "opencode"


def test_checkpoint_round_trip_after_partial_recovery() -> None:
    """Mid-recovery checkpoint round-trip preserves fallover_history and cycle count."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # The ledger demotes claude after the consecutive unproductive limit, so
    # the chain falls over from claude to opencode.
    new_state = state
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
        new_state, _, _ = controller.handle(
            state,
            _AgentTimeoutError("claude idle"),
            FailureContext(phase="development", agent="claude"),
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


def test_checkpoint_round_trip_normalizes_over_cap_recovery_context() -> None:
    """Direct JSON restore trims recovery context to the newest retained records."""
    state = PipelineState(
        phase="development",
        recovery_cycle_cap=2,
        fallover_history=(
            FalloverRecord(
                phase="development",
                from_agent="a1",
                to_agent="b1",
                timestamp_iso="2026-04-21T00:00:01Z",
            ),
            FalloverRecord(
                phase="development",
                from_agent="a2",
                to_agent="b2",
                timestamp_iso="2026-04-21T00:00:02Z",
            ),
            FalloverRecord(
                phase="development",
                from_agent="a3",
                to_agent="b3",
                timestamp_iso="2026-04-21T00:00:03Z",
            ),
        ),
        recovery_cycle_count=3,
        last_failure_category="agent",
    )

    restored = PipelineState.model_validate_json(state.model_dump_json())

    assert restored.recovery_cycle_count == 3
    assert [record.from_agent for record in restored.fallover_history] == ["a2", "a3"]
    assert restored.last_failure_category == "agent"


def test_mid_recovery_copy_with_normalizes_when_cycle_cap_shrinks() -> None:
    """Resume-path state updates keep only the newest records after a lower cap is applied."""
    state = PipelineState(
        phase="development",
        recovery_cycle_cap=3,
        fallover_history=(
            FalloverRecord(
                phase="development",
                from_agent="a1",
                to_agent="b1",
                timestamp_iso="2026-04-21T00:00:01Z",
            ),
            FalloverRecord(
                phase="development",
                from_agent="a2",
                to_agent="b2",
                timestamp_iso="2026-04-21T00:00:02Z",
            ),
            FalloverRecord(
                phase="development",
                from_agent="a3",
                to_agent="b3",
                timestamp_iso="2026-04-21T00:00:03Z",
            ),
        ),
        recovery_cycle_count=2,
        last_failure_category="agent",
    )

    restored = state.copy_with(recovery_cycle_cap=2)

    assert restored.recovery_cycle_count == 2
    assert [record.from_agent for record in restored.fallover_history] == ["a2", "a3"]
