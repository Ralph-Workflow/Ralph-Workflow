from __future__ import annotations

import json
from typing import TYPE_CHECKING

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.recovery.wait_kind import WaitKind
from tests.recovery._scenario_harness import _build_policy_bundle

if TYPE_CHECKING:
    from collections.abc import Callable

PHASE = "development"
AGENT = "agent_a"
PEER = "agent_b"
CHAIN = (AGENT, PEER)
BEYOND_EVERY_EXPIRY_SECONDS = 7 * 24 * 3600.0

Failure = tuple[str, object]


def _state(agents: tuple[str, ...] = CHAIN) -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=list(agents), current_index=0, retries=0)},
        last_connectivity_state="online",
    )


def _inert_facts() -> AttemptFacts:
    return AttemptFacts(
        artifact_valid=False,
        workspace_changed=False,
        liveness_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT, observed_returncode=0, reported_returncode=0
        ),
    )


def _apply(controller: RecoveryController, failure: Failure, agents: tuple[str, ...]) -> None:
    kind, detail = failure
    if kind == "unproductive":
        controller.record_attempt_fact(
            PHASE, AGENT, artifact_valid=False, workspace_changed=bool(detail)
        )
    elif kind == "handled_unproductive":
        controller.handle(
            _state(agents),
            AgentInvocationError(AGENT, 1, stderr="no artifact"),
            FailureContext(phase=PHASE, agent=AGENT, facts=_inert_facts()),
            workspace_changed=bool(detail),
        )
    elif kind == "connection":
        controller.handle(
            _state(agents),
            ConnectionError("connection reset"),
            FailureContext(phase=PHASE, agent=AGENT),
        )
    elif kind == "typed_unavailability":
        controller.record_confirmed_unavailability(PHASE, AGENT, UnavailabilityReason(str(detail)))
    elif kind == "marked_unavailable":
        controller.mark_agent_unavailable(PHASE, AGENT, reason=UnavailabilityReason(str(detail)))
    elif kind == "candidate_unavailable":
        controller.mark_candidate_unavailable(PHASE, AGENT)
    elif kind == "commit_attempt":
        controller.record_attempt(PHASE, AGENT)
    elif kind == "finalized_provider_overload":
        overload = AgentInvocationError(
            AGENT,
            1,
            "exit",
            parsed_output=[
                json.dumps({"type": "turn.failed", "error": {"message": "status 503 overloaded"}})
            ],
        )
        controller.finalize_attempt(
            PHASE,
            AGENT,
            facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
            attempt_failed=True,
            exc=overload,
        )
    elif kind == "slow_unproductive_attempt":
        controller.finalize_attempt(
            PHASE,
            AGENT,
            facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
            attempt_failed=True,
            attempt_duration_ms=int(str(detail)),
        )
    elif kind == "dead_agent":
        controller.handle(
            _state(agents),
            AgentInvocationError(AGENT, 0, stderr="ended"),
            FailureContext(phase=PHASE, agent=AGENT, facts=_inert_facts()),
        )


FAILURES = st.one_of(
    st.tuples(st.just("unproductive"), st.booleans()),
    st.tuples(st.just("handled_unproductive"), st.booleans()),
    st.tuples(st.just("connection"), st.none()),
    st.tuples(st.just("typed_unavailability"), st.sampled_from(list(UnavailabilityReason))),
    st.tuples(st.just("marked_unavailable"), st.sampled_from(list(UnavailabilityReason))),
    st.tuples(st.just("candidate_unavailable"), st.none()),
    st.tuples(st.just("commit_attempt"), st.none()),
    st.tuples(st.just("dead_agent"), st.none()),
    st.tuples(st.just("finalized_provider_overload"), st.none()),
    st.tuples(
        st.just("slow_unproductive_attempt"),
        st.integers(min_value=0, max_value=6 * 3600 * 1000),
    ),
)

SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _controller(
    clock: FakeClock,
    agents: tuple[str, ...],
    probe: Callable[[str, str], ProbeOutcome | None] | None,
) -> RecoveryController:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_build_policy_bundle(list(agents)),
            dead_agent_probe_runner=probe,
        )
    )
    controller.register_phase_chain(PHASE, agents)
    return controller


def _assert_excluded_only_by_a_finite_cooldown(
    controller: RecoveryController, clock: FakeClock, agents: tuple[str, ...]
) -> None:
    now_ms = int(clock.monotonic() * 1000)
    for agent, status in controller.chain_eligibility(PHASE, agents):
        assert status.is_eligible or isinstance(status, Cooldown), (agent, status)
        if isinstance(status, Cooldown):
            assert status.until_ms > now_ms, (agent, status)
    if not any(
        status.is_eligible for _agent, status in controller.chain_eligibility(PHASE, agents)
    ):
        wait = controller.eligibility_wait(PHASE, agents)
        assert wait.kind is WaitKind.COOLING_DOWN, wait
        assert wait.wait_ms is not None
        assert wait.wait_ms > 0


@SETTINGS
@given(
    failures=st.lists(FAILURES, min_size=1, max_size=40),
    sole_agent=st.booleans(),
    probe_fails=st.booleans(),
)
def test_every_failure_sequence_leaves_the_agent_cooling_down_and_then_selectable(
    failures: list[Failure], sole_agent: bool, probe_fails: bool
) -> None:
    agents = (AGENT,) if sole_agent else CHAIN
    clock = FakeClock(start=0.0)
    probe = (
        (lambda _phase, _agent: ProbeOutcome.FAILED_REPRODUCING_NO_WORK) if probe_fails else None
    )
    controller = _controller(clock, agents, probe)

    for failure in failures:
        _apply(controller, failure, agents)
        _assert_excluded_only_by_a_finite_cooldown(controller, clock, agents)

    clock.advance(BEYOND_EVERY_EXPIRY_SECONDS)

    assert controller.agent_eligibility(PHASE, AGENT).is_eligible
    assert controller.preferred_agent_index(PHASE, agents).agent == AGENT
    assert controller.agents_now_available(PHASE, list(agents)) == list(agents)


@SETTINGS
@given(attempts=st.integers(min_value=2, max_value=30), workspace_changed=st.booleans())
def test_a_sole_agent_past_its_unproductive_limit_waits_for_its_cooldown_instead_of_failing(
    attempts: int, workspace_changed: bool
) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock, (AGENT,), None)
    state = _state((AGENT,))

    for _ in range(attempts):
        controller.record_attempt_fact(
            PHASE, AGENT, artifact_valid=False, workspace_changed=workspace_changed
        )
    state, effects, _event = controller.handle(
        state,
        AgentInvocationError(AGENT, 1, stderr="no artifact"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_inert_facts()),
        workspace_changed=workspace_changed,
    )

    assert state.phase == PHASE
    assert effects == []
    if not controller.agent_eligibility(PHASE, AGENT).is_eligible:
        assert state.is_waiting_state
        assert state.last_retry_delay_ms > 0

    clock.advance(BEYOND_EVERY_EXPIRY_SECONDS)

    assert controller.agent_eligibility(PHASE, AGENT).is_eligible
