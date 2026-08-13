"""Live-detector regression coverage for bounded silent-agent recovery (S-2)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError, check_broken_agent_timer
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import ClassifiedFailure, FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.timeout_defaults import (
    BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
    BROKEN_AGENT_SAME_SHAPE_DEFAULT,
)

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle
    from ralph.recovery._broken_agent_same_shape_error import BrokenAgentSameShapeLimitError


class _ManagedHandle:
    """In-memory live process seam for the grace-window detector."""

    pid = None

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def _state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=agents, current_index=0, retries=0),
        },
    ).copy_with(last_connectivity_state="online")


def _silent_live_detector_failure() -> BrokenAgentExitError:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=30.0), clock)
    watchdog.record_invocation_start()
    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 0.1)

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(_ManagedHandle(), watchdog, "opencode")

    failure = excinfo.value
    assert failure.reason == "no_output"
    assert failure.elapsed_seconds == BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 0.1
    return failure


def test_broken_agent_regression_live_no_output_detector_bounds_sole_agent_fallover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: the live silent-process detector bounds a sole-agent recovery loop."""
    policy_bundle = _minimal_policy_bundle()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            policy_bundle=policy_bundle,
        )
    )
    captured_bounds: list[BrokenAgentSameShapeLimitError] = []
    original_check: Callable[
        [str, str | None, ClassifiedFailure, AgentChainState | None],
        BrokenAgentSameShapeLimitError | None,
    ] = controller._check_broken_agent_same_shape_bound

    def capture_bound(
        phase: str,
        agent: str | None,
        failure: ClassifiedFailure,
        chain: AgentChainState | None,
    ) -> BrokenAgentSameShapeLimitError | None:
        bound = original_check(phase, agent, failure, chain)
        if bound is not None:
            captured_bounds.append(bound)
        return bound

    monkeypatch.setattr(controller, "_check_broken_agent_same_shape_bound", capture_bound)

    first_failure = _silent_live_detector_failure()
    first_state, effects, _ = controller.handle(
        _state(["opencode"]),
        first_failure,
        FailureContext(phase="development", agent="opencode"),
    )

    assert effects == []
    assert first_state.phase == "development"
    assert first_state.is_waiting_state is True

    second_failure = _silent_live_detector_failure()
    second_state, effects, _ = controller.handle(
        first_state,
        second_failure,
        FailureContext(phase="development", agent="opencode"),
    )

    assert effects == []
    assert second_state.phase == policy_bundle.pipeline.recovery.failed_route
    assert "BROKEN_AGENT_NO_FALLOVER" in (second_state.last_error or "")
    assert len(captured_bounds) == 1
    assert captured_bounds[0].consecutive == BROKEN_AGENT_SAME_SHAPE_DEFAULT
    assert captured_bounds[0].limit == BROKEN_AGENT_SAME_SHAPE_DEFAULT
    assert controller._broken_agent_same_shape_state["development"][1] == 1

    multi_agent_controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            policy_bundle=policy_bundle,
        )
    )
    multi_agent_state, effects, _ = multi_agent_controller.handle(
        _state(["opencode", "codex"]),
        _silent_live_detector_failure(),
        FailureContext(phase="development", agent="opencode"),
    )

    multi_agent_chain = multi_agent_state.chain_for_phase("development")
    assert effects == []
    assert multi_agent_chain is not None
    assert multi_agent_chain.current_index == 1
    assert multi_agent_state.phase != policy_bundle.pipeline.recovery.failed_route
