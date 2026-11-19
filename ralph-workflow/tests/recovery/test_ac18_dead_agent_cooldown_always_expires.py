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
from ralph.recovery.agent_failure_ledger import Cooldown
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

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_confirmed_dead_agent_cooldown_always_has_an_expiry (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_benching_a_confirmed_dead_agent_never_marks_it_spent with an unchanged body"
    ),
    "test_escalating_the_dead_agent_cooldown_still_keeps_an_expiry (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_escalating_the_bench_still_never_marks_the_agent_spent with an unchanged body"
    ),
    "test_every_dead_agent_cooldown_route_leaves_an_expiring_cooldown (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_bench_routes_all_leave_the_agent_unspent with an unchanged body"
    ),
    "test_the_skip_same_agent_retries_cooldown_route_leaves_an_expiring_cooldown (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_skip_same_agent_retries_bench_route_leaves_the_agent_unspent with an unchanged body"
    ),
    "test_the_dead_agent_cooldown_never_uses_a_per_call_allowance_hint": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_bench_never_uses_a_per_call_allowance_spent_hint with an unchanged body"
    ),
    "test_ac18_dead_agent_cooldown_always_expires module": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; module renamed from tests/recovery/test_ac18_no_spent_on_dead_agent_bench.py with unchanged content"
    ),
    "test_a_confirmed_dead_agent_cooldown_always_has_an_expiry": "operator rule: an agent is either on cooldown or not; the Spent type is gone, so the test asserts the bench is a dead_agent_no_progress cooldown with a finite expiry after which the agent is selectable",
    "test_escalating_the_dead_agent_cooldown_still_keeps_an_expiry": "operator rule: an agent is either on cooldown or not; the Spent type is gone, so the test asserts the escalated bench is a dead_agent_no_progress cooldown with a finite expiry after which the agent is selectable",
    "test_every_dead_agent_cooldown_route_leaves_an_expiring_cooldown": "operator rule: an agent is either on cooldown or not; the Spent type is gone, so the test asserts each bench route leaves a dead_agent_no_progress cooldown with a finite expiry after which the agent is selectable",
    "test_the_skip_same_agent_retries_cooldown_route_leaves_an_expiring_cooldown": "operator rule: an agent is either on cooldown or not; the Spent type is gone, so the test asserts the bench is a dead_agent_no_progress cooldown with a finite expiry after which the agent is selectable",
}


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


def _assert_bench_is_an_expiring_cooldown(controller: RecoveryController, clock: FakeClock) -> None:
    snapshot = controller.snapshot()
    assert "spent_agents" not in snapshot
    for key in snapshot:
        assert "current_allowance_spent" not in str(key)
    status = controller.ledger.status(PHASE, AGENT)
    assert isinstance(status, Cooldown)
    assert status.reason == DEAD
    now_ms = int(clock.monotonic() * 1000)
    assert status.until_ms > now_ms
    clock.advance((status.until_ms - now_ms) / 1000.0 + 0.001)
    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    assert controller.ledger.status(PHASE, AGENT).is_eligible


def test_a_confirmed_dead_agent_cooldown_always_has_an_expiry() -> None:
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
    _assert_bench_is_an_expiring_cooldown(controller, clock)


def test_escalating_the_dead_agent_cooldown_still_keeps_an_expiry() -> None:
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
    _assert_bench_is_an_expiring_cooldown(controller, clock)


def test_every_dead_agent_cooldown_route_leaves_an_expiring_cooldown() -> None:
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
        _assert_bench_is_an_expiring_cooldown(controller, clock)


def test_the_skip_same_agent_retries_cooldown_route_leaves_an_expiring_cooldown() -> None:
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
    _assert_bench_is_an_expiring_cooldown(controller, clock)


def test_the_dead_agent_cooldown_never_uses_a_per_call_allowance_hint() -> None:
    options = RecoveryControllerOptions(clock=FakeClock(start=0.0))
    assert not hasattr(options, "current_allowance_spent")
