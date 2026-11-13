from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.recovery.agent_failure_ledger import Eligible
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

PHASE = "development"
AGENT = "agent_a"
PEER = "agent_b"


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            PHASE: PhaseDefinition(
                drain=PHASE,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain=PHASE,
                role="terminal",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain=PHASE,
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase=PHASE,
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _state(*, skip_same_agent_retries: bool = False) -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=[AGENT, PEER], current_index=0, retries=0)},
        agent_retry_intent=AgentRetryIntent(
            failure_reason="QuotaExhaustedError" if skip_same_agent_retries else "",
            skip_same_agent_retries=skip_same_agent_retries,
        ),
    )


def _controller() -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            agent_max_consecutive_unproductive=3,
        )
    )


def _event(facts: AttemptFacts) -> AgentFailureEvent:
    return AgentFailureEvent(
        phase=PHASE,
        agent=AGENT,
        artifact_valid=facts.artifact_valid,
        workspace_changed=facts.workspace_changed,
        facts=facts,
    )


def _ordinary_failure_facts() -> AttemptFacts:
    return AttemptFacts(
        workspace_changed=False,
        exit_status="exit_1",
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
        ),
    )


def test_both_reducer_routes_finalize_identical_facts_identically() -> None:
    facts = _ordinary_failure_facts()
    ordinary = _controller()
    skip = _controller()

    reduce(_state(), _event(facts), _policy(), recovery=ordinary)
    skip_state = _state(skip_same_agent_retries=True).copy_with(
        agent_retry_intent=AgentRetryIntent(skip_same_agent_retries=True)
    )
    reduce(skip_state, _event(facts), _policy(), recovery=skip)

    assert ordinary.last_attempt_outcome == skip.last_attempt_outcome
    assert ordinary.ledger.snapshot() == skip.ledger.snapshot()
    assert ordinary.unavailability_store.snapshot() == skip.unavailability_store.snapshot()


def test_typed_skip_route_benches_first_failure_and_persists_verdict_and_feedback() -> None:
    controller = _controller()

    reduced, effects = reduce(
        _state(skip_same_agent_retries=True),
        _event(_ordinary_failure_facts()),
        _policy(),
        recovery=controller,
    )

    assert effects == []
    assert controller.last_attempt_outcome is not None
    assert controller.last_attempt_outcome.kind is AttemptOutcomeKind.AGENT_DEAD
    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert reduced.dead_agent_verdict_record is not None
    assert reduced.dead_agent_verdict_record["verdict"] == "agent_dead"
    feedback = reduced.false_bench_feedback
    assert feedback == controller.false_bench_feedback.snapshot()
    assert feedback is not None
    assert feedback["candidates"]


@pytest.mark.parametrize("skip_same_agent_retries", [False, True])
def test_runtime_ended_attempt_is_not_charged_on_either_route(
    skip_same_agent_retries: bool,
) -> None:
    controller = _controller()
    facts = AttemptFacts(
        workspace_changed=False,
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.POST_EXIT_WATCHDOG,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )

    reduce(
        _state(skip_same_agent_retries=skip_same_agent_retries),
        _event(facts),
        _policy(),
        recovery=controller,
    )

    assert controller.last_attempt_outcome is not None
    assert controller.last_attempt_outcome.kind is AttemptOutcomeKind.RUNTIME_ENDED
    assert isinstance(controller.ledger.status(PHASE, AGENT), Eligible)
    assert controller.ledger.snapshot()["entries"] == {}


def test_keyed_repetition_and_reducer_claim_charge_once() -> None:
    controller = _controller()
    facts = _ordinary_failure_facts()
    key = "main_phase_loop:development:agent_a:1"

    first = controller.finalize_attempt(
        PHASE,
        AGENT,
        facts=facts,
        attempt_failed=True,
        attempt_key=key,
    )
    charged = controller.ledger.snapshot()
    repeated = controller.finalize_attempt(
        PHASE,
        AGENT,
        facts=facts,
        attempt_failed=True,
        attempt_key=key,
    )
    reduce(_state(), _event(facts), _policy(), recovery=controller)

    assert repeated is first
    assert controller.last_attempt_outcome is first
    assert controller.ledger.snapshot() == charged
