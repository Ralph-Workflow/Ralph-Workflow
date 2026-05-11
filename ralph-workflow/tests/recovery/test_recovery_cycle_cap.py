"""Black-box test: recovery cycle cap prevents infinite loops."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController
from ralph.recovery.cycle_cap import CycleCap


def _minimal_policy_bundle():
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


_CYCLE_CAP = 3
_EXPECTED_CYCLE_COUNT = 2
_MIN_REASON_LEN = 20


def _make_state(agents: list[str], cycle_count: int = 0) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
        recovery_cycle_count=cycle_count,
        recovery_cycle_cap=_CYCLE_CAP,
    )


class _AgentInactivityTimeoutError(Exception):
    pass


_AgentInactivityTimeoutError.__name__ = "AgentInactivityTimeoutError"


def test_env_failure_does_not_increment_cycle_count() -> None:
    """Environmental failures never increment recovery_cycle_count."""
    controller = RecoveryController(cycle_cap=_CYCLE_CAP)
    state = _make_state(["claude"], cycle_count=_EXPECTED_CYCLE_COUNT)

    new_state, effects, _ = controller.handle(
        state,
        ConnectionError("network unreachable"),
        phase="development",
        agent="claude",
    )

    assert new_state.recovery_cycle_count == _EXPECTED_CYCLE_COUNT
    assert effects == []


def test_agent_failure_chain_exhaustion_increments_cycle_count() -> None:
    """Agent chain exhaustion increments recovery_cycle_count."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=1)
    controller = RecoveryController(
        cycle_cap=10, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
    )
    state = _make_state(["claude"])

    new_state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle"),
        phase="development",
        agent="claude",
    )

    assert new_state.recovery_cycle_count == 1


def test_cycle_cap_exceeded_emits_exit_failure_effect() -> None:
    """When recovery_cycle_count >= cap, an ExitFailureEffect is produced."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=1)
    controller = RecoveryController(
        cycle_cap=_CYCLE_CAP, budget_registry=registry, policy_bundle=_minimal_policy_bundle()
    )

    state = _make_state(["claude"], cycle_count=_EXPECTED_CYCLE_COUNT)

    _new_state, effects, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle timeout again"),
        phase="development",
        agent="claude",
    )

    exit_effects = [e for e in effects if isinstance(e, ExitFailureEffect)]
    assert len(exit_effects) == 1
    reason = exit_effects[0].reason
    assert str(_CYCLE_CAP) in reason
    assert "recovery" in reason.lower()


def test_cycle_cap_exit_reason_is_non_sentinel() -> None:
    """Exit reason from cycle cap must not contain forbidden sentinels."""
    cap = CycleCap(cap=_CYCLE_CAP)
    reason = cap.exit_reason(_CYCLE_CAP, "agent", "agent idle timeout")

    assert "None" not in reason
    assert "null" not in reason
    assert "Unknown failure" not in reason
    assert "unknown failure" not in reason
    assert len(reason) > _MIN_REASON_LEN


def test_cycle_cap_exit_failure_effect_is_valid() -> None:
    """ExitFailureEffect built from cycle cap reason must be constructable."""
    cap = CycleCap(cap=5)
    reason = cap.exit_reason(5, "agent", "agent idle timeout after 10s")
    effect = ExitFailureEffect(reason=reason)
    assert effect.reason == reason


def test_cycle_cap_rejects_zero() -> None:
    """CycleCap must reject cap=0."""
    with pytest.raises(ValueError, match=">="):
        CycleCap(cap=0)
