from __future__ import annotations

from collections.abc import Callable

import pytest
from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError, InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from ralph.timeout_defaults import AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_wu_c4_stalled_reported_success_uses_default_threshold_and_cooldown": "operator rule: an agent is either on cooldown or not; the consecutive unproductive limit now starts an expiring unproductive_limit cooldown, so the test asserts that reason, the 60 s expiry, and that the agent is selected again once it passes",
}

_PRIMARY = "primary-agent"
_NEXT = "next-agent"
_AGENTS = (_PRIMARY, _NEXT)


def _default_phases() -> tuple[str, ...]:
    return tuple(
        name
        for name, phase in cached_default_policy_bundle().pipeline.phases.items()
        if phase.role != "terminal" and not phase.skip_invocation
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=cached_default_policy_bundle(),
        )
    )


def _state(phase: str) -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={phase: AgentChainState(agents=list(_AGENTS))},
        last_connectivity_state="online",
    )


def _capture_cooldown_lines(run: Callable[[], object]) -> list[str]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        run()
    finally:
        logger.remove(sink)
    return [line.strip() for line in lines if "cooldown_decision" in line]


def _assert_complete_log_line(
    lines: list[str],
    *,
    phase: str,
    reason: UnavailabilityReason,
    duration_ms: int,
    expiry_ms: int,
) -> None:
    assert len(lines) == 1
    line = lines[0]
    assert f"phase={phase}" in line
    assert f"agent={_PRIMARY}" in line
    assert f"reason={reason}" in line
    assert f"duration_ms={duration_ms}" in line
    assert f"expiry_ms={expiry_ms}" in line


def _assert_fallback_only(controller: RecoveryController, phase: str) -> None:
    invoked: list[str] = []
    for _attempt in range(2):
        selection = controller.preferred_agent_index(phase, _AGENTS)
        assert selection.agent is not None
        invoked.append(selection.agent)
    assert invoked == [_NEXT, _NEXT]
    assert _PRIMARY not in invoked


@pytest.mark.parametrize("phase", _default_phases())
def test_wu_c4_stalled_reported_success_uses_default_threshold_and_cooldown(
    phase: str,
) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    state = _state(phase)
    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)

    def exhaust_threshold() -> None:
        for _attempt in range(AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT):
            controller.handle(
                state,
                "missing completion evidence",
                FailureContext(phase=phase, agent=_PRIMARY, facts=facts),
                facts=facts,
                artifact_valid=False,
                workspace_changed=False,
            )

    lines = _capture_cooldown_lines(exhaust_threshold)
    status = controller.agent_eligibility(phase, _PRIMARY)

    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    assert status.until_ms == 60_000
    _assert_complete_log_line(
        lines,
        phase=phase,
        reason=UnavailabilityReason.UNPRODUCTIVE_LIMIT,
        duration_ms=60_000,
        expiry_ms=60_000,
    )
    _assert_fallback_only(controller, phase)
    clock.advance(60.0)
    assert controller.agent_eligibility(phase, _PRIMARY).is_eligible
    assert controller.preferred_agent_index(phase, _AGENTS).agent == _PRIMARY


@pytest.mark.parametrize("phase", _default_phases())
def test_wu_c4_no_activity_uses_shared_reason_duration_and_skips_cooled_agent(
    phase: str,
) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    state = _state(phase)
    reason = UnavailabilityReason.NO_OUTPUT_AT_START
    duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[reason].base_backoff_ms
    error = AgentInactivityTimeoutError(
        _PRIMARY,
        30.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
    )

    lines = _capture_cooldown_lines(
        lambda: controller.handle(
            state,
            error,
            FailureContext(phase=phase, agent=_PRIMARY),
        )
    )
    status = controller.agent_eligibility(phase, _PRIMARY)

    assert isinstance(status, Cooldown)
    assert status.reason == str(reason)
    assert status.until_ms == duration_ms
    _assert_complete_log_line(
        lines,
        phase=phase,
        reason=reason,
        duration_ms=duration_ms,
        expiry_ms=duration_ms,
    )
    _assert_fallback_only(controller, phase)
