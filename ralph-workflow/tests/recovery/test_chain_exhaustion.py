"""Black-box test: chain exhaustion produces PHASE_FAILED with fallover history."""

from __future__ import annotations

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEventBus, FalloverEvent

_MIN_ERROR_LEN = 10


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
    )


def _registry_with_one_retry(*agents: str) -> AgentBudgetRegistry:
    reg = AgentBudgetRegistry()
    for agent in agents:
        reg = reg.set_budget(PHASE_DEVELOPMENT, agent, max_retries=1)
    return reg


def test_chain_exhaustion_with_two_agents() -> None:
    """Two agents each exhausted → PHASE_FAILED, recovery_cycle_count==1, two fallover records."""
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None)  # type: ignore[arg-type]

    registry = _registry_with_one_retry("claude", "opencode")
    controller = RecoveryController(cycle_cap=10, budget_registry=registry, event_bus=bus)
    state = _make_state(["claude", "opencode"])

    # First failure on claude → budget exhausted (1/1), should fallover to opencode
    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("claude timed out"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    # Should have fallen over
    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"
    assert state.phase == PHASE_DEVELOPMENT
    assert state.dev_chain.current_index == 1
    assert len(state.fallover_history) == 1

    # Second failure on opencode → budget exhausted (1/1), chain exhausted → PHASE_FAILED
    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("opencode timed out"),
        phase=PHASE_DEVELOPMENT,
        agent="opencode",
    )

    assert state.phase == PHASE_FAILED
    assert state.recovery_cycle_count == 1
    assert state.last_error is not None
    # Two fallover records: claude->opencode is already in fallover_history from first call
    assert len(state.fallover_history) == 1  # still 1 since opencode was last, no next agent


def test_chain_exhaustion_last_error_is_non_sentinel() -> None:
    """Chain exhaustion last_error must be descriptive and not a forbidden sentinel."""
    registry = _registry_with_one_retry("claude")
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"])

    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("agent idle timeout"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert state.phase == PHASE_FAILED
    assert state.last_error is not None
    assert state.last_error not in ("Unknown failure", "unknown failure", "None", "null", "")
    assert len(state.last_error) > _MIN_ERROR_LEN


def test_chain_exhaustion_increments_recovery_cycle_count() -> None:
    """Each full-chain exhaustion must increment recovery_cycle_count by 1."""
    registry = _registry_with_one_retry("claude")
    controller = RecoveryController(cycle_cap=10, budget_registry=registry)
    state = _make_state(["claude"])

    assert state.recovery_cycle_count == 0

    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert state.recovery_cycle_count == 1
