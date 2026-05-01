"""Black-box test: chain exhaustion produces "failed" with fallover history."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEventBus, FalloverEvent


def _minimal_policy_bundle():
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")

_MIN_ERROR_LEN = 10


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def _registry_with_one_retry(*agents: str) -> AgentBudgetRegistry:
    reg = AgentBudgetRegistry()
    for agent in agents:
        reg = reg.set_budget("development", agent, max_retries=1)
    return reg


def test_chain_exhaustion_with_two_agents() -> None:
    """Two agents each exhausted → "failed", recovery_cycle_count==1, two fallover records."""
    fallovers: list[FalloverEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: fallovers.append(evt) if isinstance(evt, FalloverEvent) else None)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    registry = _registry_with_one_retry("claude", "opencode")
    controller = RecoveryController(
        cycle_cap=10, budget_registry=registry, event_bus=bus,
        policy_bundle=_minimal_policy_bundle(),
    )
    state = _make_state(["claude", "opencode"])

    # First failure on claude → budget exhausted (1/1), should fallover to opencode
    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("claude timed out"),
        phase="development",
        agent="claude",
    )

    # Should have fallen over
    assert len(fallovers) == 1
    assert fallovers[0].from_agent == "claude"
    assert fallovers[0].to_agent == "opencode"
    assert state.phase == "development"
    assert state.chain_for_phase("development").current_index == 1
    assert len(state.fallover_history) == 1

    # Second failure on opencode → budget exhausted (1/1), chain exhausted → "failed"
    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("opencode timed out"),
        phase="development",
        agent="opencode",
    )

    assert state.phase == "failed_terminal"
    assert state.recovery_cycle_count == 1
    assert state.last_error is not None
    # Two fallover records: claude->opencode is already in fallover_history from first call
    assert len(state.fallover_history) == 1  # still 1 since opencode was last, no next agent


def test_chain_exhaustion_last_error_is_non_sentinel() -> None:
    """Chain exhaustion last_error must be descriptive and not a forbidden sentinel."""
    registry = _registry_with_one_retry("claude")
    controller = RecoveryController(
        cycle_cap=10, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
    )
    state = _make_state(["claude"])

    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("agent idle timeout"),
        phase="development",
        agent="claude",
    )

    assert state.phase == "failed_terminal"
    assert state.last_error is not None
    assert state.last_error not in ("Unknown failure", "unknown failure", "None", "null", "")
    assert len(state.last_error) > _MIN_ERROR_LEN


def test_chain_exhaustion_increments_recovery_cycle_count() -> None:
    """Each full-chain exhaustion must increment recovery_cycle_count by 1."""
    registry = _registry_with_one_retry("claude")
    controller = RecoveryController(
        cycle_cap=10, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
    )
    state = _make_state(["claude"])

    assert state.recovery_cycle_count == 0

    state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle"),
        phase="development",
        agent="claude",
    )

    assert state.recovery_cycle_count == 1
