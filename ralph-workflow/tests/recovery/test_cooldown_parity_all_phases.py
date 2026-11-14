from __future__ import annotations

from dataclasses import dataclass

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_pipeline_policy
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from ralph.workspace.memory import MemoryWorkspace
from tests._conflict_resolution_phase_parity_seams import _policy_bundle
from tests.integration._mock_agent_invoker import MockAgentInvoker

_PRIMARY = "parity-primary"
_FALLBACK = "parity-fallback"
_AGENTS = (_PRIMARY, _FALLBACK)


@dataclass(frozen=True)
class _Case:
    phase: str
    outcome: ConflictAttemptOutcome

    @property
    def reason(self) -> UnavailabilityReason:
        if self.outcome is ConflictAttemptOutcome.NO_ACTIVITY:
            return UnavailabilityReason.NO_OUTPUT_AT_START
        return UnavailabilityReason.DEAD_AGENT_NO_PROGRESS

    @property
    def reports_success(self) -> bool:
        return self.outcome is not ConflictAttemptOutcome.NO_ACTIVITY


class _FailingAgentStub(MockAgentInvoker):
    def __init__(self, outcome: ConflictAttemptOutcome) -> None:
        super().__init__(MemoryWorkspace())
        self.outcome = outcome

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        return super().invoke(agent_name, phase)


class _StalledReportedSuccessStub(_FailingAgentStub):
    def __init__(self) -> None:
        super().__init__(ConflictAttemptOutcome.STALLED)


class _NoActivityStub(_FailingAgentStub):
    def __init__(self) -> None:
        super().__init__(ConflictAttemptOutcome.NO_ACTIVITY)


def _agent_invoking_phases() -> tuple[str, ...]:
    policy = cached_default_pipeline_policy()
    return tuple(
        name
        for name, phase in policy.phases.items()
        if phase.role != "terminal" and not phase.skip_invocation
    )


def _cases() -> tuple[_Case, ...]:
    phases = (*_agent_invoking_phases(), PHASE_RESOLUTION)
    return tuple(
        _Case(phase, outcome)
        for phase in phases
        for outcome in (ConflictAttemptOutcome.STALLED, ConflictAttemptOutcome.NO_ACTIVITY)
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_policy_bundle(),
        )
    )


def _state(phase: str) -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={
            phase: AgentChainState(agents=list(_AGENTS), current_index=0, retries=0),
        },
        last_connectivity_state="online",
    )


def _stub(case: _Case) -> _FailingAgentStub:
    if case.outcome is ConflictAttemptOutcome.NO_ACTIVITY:
        return _NoActivityStub()
    return _StalledReportedSuccessStub()


def _report_success(
    controller: RecoveryController,
    case: _Case,
    ordinal: int,
) -> None:
    controller.finalize_attempt(
        case.phase,
        _PRIMARY,
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
        attempt_key=f"{case.phase}:{case.outcome.value}:{ordinal}",
    )


def _drive_default_phase(
    controller: RecoveryController,
    stub: _FailingAgentStub,
    case: _Case,
    ordinal: int,
) -> None:
    assert isinstance(stub.invoke(_PRIMARY, case.phase), PipelineEvent)
    if case.reports_success:
        _report_success(controller, case, ordinal)
    classified = controller.classify_conflict_attempt(
        None,
        agent=_PRIMARY,
        phase=case.phase,
        outcome=case.outcome,
        unresolved_count=1,
    )
    controller.handle(
        _state(case.phase),
        "",
        FailureContext(
            phase=case.phase,
            agent=_PRIMARY,
            classified_failure=classified,
        ),
    )


def _drive_conflict_phase(
    controller: RecoveryController,
    session: ResolutionSession,
    stub: _FailingAgentStub,
    case: _Case,
    ordinal: int,
) -> None:
    assert stub.invoke(_PRIMARY, case.phase) is PipelineEvent.AGENT_SUCCESS
    if case.reports_success:
        _report_success(controller, case, ordinal)
    classify_failed_resolution_attempt(
        session,
        _PRIMARY,
        None,
        candidates=_AGENTS,
        failed_index=0,
        outcome=case.outcome,
        unresolved_count=1,
    )


def _advance_past_cooldown(
    controller: RecoveryController,
    clock: FakeClock,
    phase: str,
) -> None:
    status = controller.agent_eligibility(phase, _PRIMARY)
    assert isinstance(status, Cooldown)
    now_ms = int(clock.monotonic() * 1000)
    clock.advance((status.until_ms - now_ms) / 1000)


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda case: f"{case.phase}-{case.outcome.value}",
)
def test_every_agent_invoking_phase_uses_the_owner_cooldown_contract(case: _Case) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    stub = _stub(case)
    limit = controller.ledger.consecutive_limit
    session = ResolutionSession(recovery_controller=controller)

    for ordinal in range(limit):
        if ordinal:
            _advance_past_cooldown(controller, clock, case.phase)
        if case.phase == PHASE_RESOLUTION:
            _drive_conflict_phase(controller, session, stub, case, ordinal)
        else:
            _drive_default_phase(controller, stub, case, ordinal)

    status = controller.agent_eligibility(case.phase, _PRIMARY)
    selection = controller.preferred_agent_index(case.phase, _AGENTS)
    snapshot = controller.unavailability_store.snapshot()
    now_ms = int(clock.monotonic() * 1000)

    assert stub.count_for(case.phase) == limit
    assert isinstance(status, Cooldown)
    assert selection.agent == _FALLBACK
    assert dict(selection.skipped_reasons)[_PRIMARY].startswith("cooldown (")
    assert controller.earliest_available_wait_ms(case.phase, [_PRIMARY]) == status.until_ms - now_ms
    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[case.reason]
    benches = 1 if case.reports_success else limit
    expected_duration_ms = min(
        policy.base_backoff_ms << (benches - 1),
        policy.max_backoff_ms,
    )
    assert status.reason == str(case.reason)
    assert status.until_ms - now_ms == expected_duration_ms
    assert snapshot["unavailability_reasons"][_PRIMARY] == str(case.reason)
    timeout_ms = snapshot["unavailable_timeouts"][_PRIMARY]
    assert isinstance(timeout_ms, int)
    assert timeout_ms - now_ms == expected_duration_ms
