from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from tests._conflict_resolution_phase_parity_seams import _policy_bundle

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_PRIMARY = "primary-agent"
_FALLBACK = "fallback-agent"
_AGENTS = (_PRIMARY, _FALLBACK)


def _declared_policy_bundle() -> PolicyBundle:
    return _policy_bundle() or cached_default_policy_bundle()


def _artifact_producing_phases() -> tuple[str, ...]:
    bundle = _declared_policy_bundle()
    main_phases = tuple(
        name
        for name, phase in bundle.pipeline.phases.items()
        if phase.role != "terminal" and not phase.skip_invocation
    )
    sub_pipelines = ("policy_remediation", "policy_remediation_analysis")
    combined: list[str] = []
    for item in (*main_phases, *sub_pipelines):
        if item not in combined:
            combined.append(item)
    return tuple(combined)


def _all_routes() -> tuple[str, ...]:
    return (*_artifact_producing_phases(), PHASE_RESOLUTION)


def _controller(clock: FakeClock, *, limit: int = 2) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_declared_policy_bundle(),
            agent_max_consecutive_unproductive=limit,
        )
    )


def _pipeline_state(phase: str) -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={
            phase: AgentChainState(agents=list(_AGENTS), current_index=0, retries=0),
        },
        last_connectivity_state="online",
    )


@pytest.mark.parametrize("route", _all_routes())
def test_launch_and_hard_failure_cools_agent_across_all_routes(route: str) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    session = ResolutionSession(recovery_controller=controller)
    state = _pipeline_state(route)
    exc = AgentInvocationError(_PRIMARY, 1, "process terminated unexpectedly")

    if route == PHASE_RESOLUTION:
        classify_failed_resolution_attempt(
            session,
            _PRIMARY,
            raw_failure=exc,
            candidates=_AGENTS,
            failed_index=0,
        )
    else:
        controller.handle(
            state,
            exc,
            FailureContext(phase=route, agent=_PRIMARY),
        )

    status = controller.agent_eligibility(route, _PRIMARY)
    selection = controller.preferred_agent_index(route, _AGENTS)
    wait_ms = controller.earliest_available_wait_ms(route, [_PRIMARY])
    chain_status = dict(controller.chain_eligibility(route, _AGENTS))
    expected_duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.PROCESS_EXITED
    ].base_backoff_ms

    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.PROCESS_EXITED)
    assert status.until_ms == expected_duration_ms
    assert wait_ms == expected_duration_ms
    assert selection.agent == _FALLBACK
    assert selection.index == 1
    assert isinstance(chain_status[_PRIMARY], Cooldown)
    assert isinstance(chain_status[_FALLBACK], Eligible)


@pytest.mark.parametrize("route", _all_routes())
def test_no_activity_failure_cools_agent_across_all_routes(route: str) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    session = ResolutionSession(recovery_controller=controller)
    state = _pipeline_state(route)

    if route == PHASE_RESOLUTION:
        classify_failed_resolution_attempt(
            session,
            _PRIMARY,
            candidates=_AGENTS,
            failed_index=0,
            workspace_changed=False,
            outcome=ConflictAttemptOutcome.NO_ACTIVITY,
        )
    else:
        err = AgentInactivityTimeoutError(
            _PRIMARY,
            30.0,
            [],
            InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
        )
        controller.handle(
            state,
            err,
            FailureContext(phase=route, agent=_PRIMARY),
        )

    status = controller.agent_eligibility(route, _PRIMARY)
    selection = controller.preferred_agent_index(route, _AGENTS)
    wait_ms = controller.earliest_available_wait_ms(route, [_PRIMARY])
    expected_duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.NO_OUTPUT_AT_START
    ].base_backoff_ms

    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.NO_OUTPUT_AT_START)
    assert status.until_ms == expected_duration_ms
    assert wait_ms == expected_duration_ms
    assert selection.agent == _FALLBACK
    assert selection.index == 1


@pytest.mark.parametrize("phase", _artifact_producing_phases())
def test_stalled_reported_success_cools_agent_across_all_artifact_routes(phase: str) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock, limit=2)
    state = _pipeline_state(phase)

    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)
    for ordinal in range(2):
        if ordinal > 0:
            status = controller.agent_eligibility(phase, _PRIMARY)
            assert isinstance(status, Eligible)
        controller.handle(
            state,
            "missing completion evidence",
            FailureContext(phase=phase, agent=_PRIMARY, facts=facts),
            facts=facts,
            artifact_valid=False,
            workspace_changed=False,
        )

    status = controller.agent_eligibility(phase, _PRIMARY)
    selection = controller.preferred_agent_index(phase, _AGENTS)
    wait_ms = controller.earliest_available_wait_ms(phase, [_PRIMARY])

    assert isinstance(status, Cooldown)
    assert selection.agent == _FALLBACK
    assert selection.index == 1
    assert wait_ms == status.until_ms
    assert status.until_ms == 60_000

    clock.advance(wait_ms / 1000.0)
    assert isinstance(controller.agent_eligibility(phase, _PRIMARY), Eligible)
    assert controller.preferred_agent_index(phase, _AGENTS).agent == _PRIMARY


@pytest.mark.parametrize("phase", _artifact_producing_phases())
def test_valid_artifact_no_workspace_change_asserts_no_cooldown(phase: str) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock, limit=2)

    for ordinal in range(5):
        facts = AttemptFacts(artifact_valid=True, workspace_changed=False)
        controller.finalize_attempt(
            phase,
            _PRIMARY,
            facts=facts,
            attempt_failed=False,
            attempt_key=f"{phase}:{ordinal}",
        )
        assert isinstance(controller.agent_eligibility(phase, _PRIMARY), Eligible)

    selection = controller.preferred_agent_index(phase, _AGENTS)
    assert selection.agent == _PRIMARY
    assert selection.index == 0
    assert controller.earliest_available_wait_ms(phase, [_PRIMARY]) == 0


def test_stalled_reported_success_cools_agent_in_conflict_resolution() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock, limit=2)
    session = ResolutionSession(recovery_controller=controller)

    classify_failed_resolution_attempt(
        session,
        _PRIMARY,
        candidates=_AGENTS,
        failed_index=0,
        workspace_changed=False,
        outcome=ConflictAttemptOutcome.STALLED_NO_PROGRESS,
        unresolved_count=1,
    )

    status = controller.agent_eligibility(PHASE_RESOLUTION, _PRIMARY)
    selection = controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS)
    wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, [_PRIMARY])

    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY)
    assert selection.agent == _FALLBACK
    assert selection.index == 1
    assert wait_ms == 10_000

    clock.advance(wait_ms / 1000.0)
    assert isinstance(controller.agent_eligibility(PHASE_RESOLUTION, _PRIMARY), Eligible)
    assert controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS).agent == _PRIMARY


def test_mutation_bypassing_recovery_owner_fails_cooldown_contract() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock, limit=2)

    status = controller.agent_eligibility("development", _PRIMARY)
    assert isinstance(status, Eligible)
    selection = controller.preferred_agent_index("development", _AGENTS)
    assert selection.agent == _PRIMARY
