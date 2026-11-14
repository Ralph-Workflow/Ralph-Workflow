from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents import subprocess_executor
from ralph.agents.invoke import BrokenAgentExitError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import Spent
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.typed_signal import TypedSignal
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

PHASE = "development"
AGENT = "claude"
OTHER_AGENT = "opencode"
DEAD = str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
QuotaExhaustedError = subprocess_executor.QuotaExhaustedError


def _policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _bench_controller(clock: FakeClock) -> RecoveryController:
    def probe_runner(_phase: str, _agent: str) -> ProbeOutcome:
        return ProbeOutcome.FAILED_REPRODUCING_NO_WORK

    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=probe_runner,
        )
    )


def _state(
    *,
    agents: tuple[str, ...] = (AGENT,),
    intent: AgentRetryIntent | None = None,
) -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=list(agents), current_index=0, retries=0)},
        last_connectivity_state="online",
        agent_retry_intent=intent if intent is not None else AgentRetryIntent(),
    )


def _assert_no_spent_anywhere(controller: RecoveryController) -> None:
    snapshot = controller.snapshot()
    assert snapshot["spent_agents"] == {}
    assert not isinstance(controller.ledger.status(PHASE, AGENT), Spent)
    for key in snapshot:
        assert "current_allowance_spent" not in str(key)


def test_benching_a_confirmed_dead_agent_never_marks_it_spent() -> None:
    clock = FakeClock(start=0.0)
    controller = _bench_controller(clock)

    controller.handle(
        _state(),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(phase=PHASE, agent=AGENT, facts=AttemptFacts(artifact_valid=False)),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(AGENT) == DEAD
    _assert_no_spent_anywhere(controller)


def test_escalating_the_bench_still_never_marks_the_agent_spent() -> None:
    clock = FakeClock(start=0.0)
    controller = _bench_controller(clock)
    failure = QuotaExhaustedError(AGENT, "credits exhausted for this account")
    context = FailureContext(phase=PHASE, agent=AGENT, facts=AttemptFacts(artifact_valid=False))

    controller.handle(_state(), failure, context)
    clock.advance(3600.0)
    store = controller.unavailability_store
    if isinstance(store, AgentUnavailabilityTracker):
        store.prune_expired()
    controller.handle(_state(), failure, context)

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    _assert_no_spent_anywhere(controller)


def test_the_bench_routes_all_leave_the_agent_unspent() -> None:
    for failure in (
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        BrokenAgentExitError(AGENT, reason="no_output"),
    ):
        clock = FakeClock(start=0.0)
        controller = _bench_controller(clock)

        controller.handle(
            _state(),
            failure,
            FailureContext(phase=PHASE, agent=AGENT, facts=AttemptFacts(artifact_valid=False)),
        )

        assert controller.unavailability_store.is_available(PHASE, AGENT) is False
        _assert_no_spent_anywhere(controller)


def test_the_skip_same_agent_retries_bench_route_leaves_the_agent_unspent() -> None:
    clock = FakeClock(start=0.0)
    controller = _bench_controller(clock)
    intent = AgentRetryIntent(
        failure_reason="QuotaExhaustedError",
        skip_same_agent_retries=True,
        failed_agent_name=AGENT,
    )
    event = AgentFailureEvent(
        phase=PHASE,
        agent=AGENT,
        artifact_valid=False,
        workspace_changed=False,
        facts=AttemptFacts(
            artifact_valid=False,
            typed_signal=TypedSignal(
                name="QuotaExhaustedError",
                provider_originated=True,
                agent_initiated=True,
            ),
        ),
    )

    reduce(
        _state(agents=(AGENT, OTHER_AGENT), intent=intent),
        event,
        _policy(),
        recovery=controller,
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    _assert_no_spent_anywhere(controller)


def test_the_bench_never_uses_a_per_call_allowance_spent_hint() -> None:
    options = RecoveryControllerOptions(clock=FakeClock(start=0.0))
    assert not hasattr(options, "current_allowance_spent")
