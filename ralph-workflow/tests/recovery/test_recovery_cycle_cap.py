from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.effects import Effect, ExitFailureEffect
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.cycle_cap import CycleCap

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_agent_failure_chain_exhaustion_increments_cycle_count": "operator rule: an agent is either on cooldown or not; a cooled sole agent now waits for its cooldown instead of entering the failed phase, so the cycle count is driven through a chain with no agents, the only route that still reaches the failed phase",
    "test_cooling_sole_agent_waits_without_counting_a_recovery_cycle": "operator rule: an agent is either on cooldown or not; pins that a sole agent reaching the unproductive limit yields the wait state with a finite delay and becomes eligible again after it expires",
    "test_cycle_cap_exceeded_emits_exit_failure_effect": "operator rule: an agent is either on cooldown or not; a cooled sole agent now waits instead of failing, so the cycle cap is driven through a chain with no agents, the only route that still reaches the failed phase",
}


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


_CYCLE_CAP = 3
_EXPECTED_CYCLE_COUNT = 2
_MIN_REASON_LEN = 20
_CONSECUTIVE_UNPRODUCTIVE_LIMIT = 3


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
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=_CYCLE_CAP))
    state = _make_state(["claude"], cycle_count=_EXPECTED_CYCLE_COUNT)

    new_state, effects, _ = controller.handle(
        state,
        ConnectionError("network unreachable"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.recovery_cycle_count == _EXPECTED_CYCLE_COUNT
    assert effects == []


def test_agent_failure_chain_exhaustion_increments_cycle_count() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=_minimal_policy_bundle())
    )
    state = _make_state([])

    new_state, _, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle"),
        FailureContext(phase="development", agent=None),
    )

    assert new_state.recovery_cycle_count == 1


def test_cooling_sole_agent_waits_without_counting_a_recovery_cycle() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10, clock=clock, policy_bundle=_minimal_policy_bundle()
        )
    )
    state = _make_state(["claude"])

    new_state = state
    effects: list[Effect] = []
    for _attempt in range(_CONSECUTIVE_UNPRODUCTIVE_LIMIT):
        new_state, effects, _ = controller.handle(
            new_state,
            _AgentInactivityTimeoutError("idle"),
            FailureContext(phase="development", agent="claude"),
        )

    assert new_state.recovery_cycle_count == 0
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms > 0
    assert effects == []

    clock.advance(new_state.last_retry_delay_ms / 1000.0)

    assert controller.agent_eligibility("development", "claude").is_eligible


def test_cycle_cap_exceeded_emits_exit_failure_effect() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=_CYCLE_CAP, policy_bundle=_minimal_policy_bundle()
        )
    )

    state = _make_state([], cycle_count=_EXPECTED_CYCLE_COUNT)

    _new_state, effects, _ = controller.handle(
        state,
        _AgentInactivityTimeoutError("idle timeout again"),
        FailureContext(phase="development", agent=None),
    )

    exit_effects = [e for e in effects if isinstance(e, ExitFailureEffect)]
    assert len(exit_effects) == 1
    reason = exit_effects[0].reason
    assert str(_CYCLE_CAP) in reason
    assert "recovery" in reason.lower()


def test_cycle_cap_exit_reason_is_non_sentinel() -> None:
    cap = CycleCap(cap=_CYCLE_CAP)
    reason = cap.exit_reason(_CYCLE_CAP, "agent", "agent idle timeout")

    assert "None" not in reason
    assert "null" not in reason
    assert "Unknown failure" not in reason
    assert "unknown failure" not in reason
    assert len(reason) > _MIN_REASON_LEN


def test_cycle_cap_exit_failure_effect_is_valid() -> None:
    cap = CycleCap(cap=5)
    reason = cap.exit_reason(5, "agent", "agent idle timeout after 10s")
    effect = ExitFailureEffect(reason=reason)
    assert effect.reason == reason


def test_cycle_cap_rejects_zero() -> None:
    with pytest.raises(ValueError, match=">="):
        CycleCap(cap=0)
