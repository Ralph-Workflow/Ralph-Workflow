from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents import subprocess_executor
from ralph.agents.invoke import BrokenAgentExitError, MissingCredentialsError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import Spent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import decide_dead_agent_verdict
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.policy.models import PipelinePolicy
    from ralph.recovery.attribution import AttributionVerdict
    from ralph.recovery.dead_agent_verdict import PersistedVerdict, TypedSignal

PHASE = "development"
AGENT = "claude"
OTHER_AGENT = "opencode"
QuotaExhaustedError = subprocess_executor.QuotaExhaustedError


def _policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _facts(*, termination: TerminationRecord | None = None) -> AttemptFacts:
    return AttemptFacts(artifact_valid=False, termination=termination)


def _probe_controller(clock: FakeClock, probe_outcome: ProbeOutcome) -> RecoveryController:
    def probe_runner(phase: str, agent: str) -> ProbeOutcome:
        del phase
        del agent
        return probe_outcome

    def verdict_runner(
        *,
        facts: AttemptFacts | None,
        attribution: AttributionVerdict | None,
        probe_outcome: ProbeOutcome | None,
        typed_signal: TypedSignal | None,
    ) -> PersistedVerdict | None:
        if facts is None or attribution is None:
            return None
        return decide_dead_agent_verdict(facts, attribution, probe_outcome, typed_signal)

    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=probe_runner,
            dead_agent_verdict_runner=verdict_runner,
        )
    )


def _state(
    *,
    connectivity_state: str = "online",
    agents: tuple[str, ...] = (AGENT,),
    current_index: int = 0,
    intent: AgentRetryIntent | None = None,
) -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={
            PHASE: AgentChainState(agents=list(agents), current_index=current_index, retries=0)
        },
        last_connectivity_state=connectivity_state,
        agent_retry_intent=intent if intent is not None else AgentRetryIntent(),
    )


def _ledger_entry(controller: RecoveryController, agent: str) -> dict[str, object]:
    snapshot = controller.ledger.snapshot()
    entries = snapshot.get("entries")
    if not isinstance(entries, dict):
        return {}
    entry = entries.get(f"{PHASE}:{agent}")
    if not isinstance(entry, dict):
        return {}
    return entry


def _counter(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _bench_reason(controller: RecoveryController) -> str | None:
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons")
    if not isinstance(reasons, dict):
        return None
    reason = reasons.get(AGENT)
    return reason if isinstance(reason, str) else None


@pytest.fixture
def warning_sink() -> Iterator[list[str]]:
    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(str(message)), level="WARNING")
    try:
        yield captured
    finally:
        logger.remove(sink_id)


@pytest.mark.parametrize("connectivity_state", ("online", "offline"))
def test_ac10_a_typed_quota_failure_benches_the_agent_on_the_first_failure(
    connectivity_state: str,
) -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(connectivity_state=connectivity_state),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT]) > 0
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac10_a_second_confirmed_failure_escalates_the_cooldown() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
    failure = QuotaExhaustedError(AGENT, "credits exhausted for this account")
    context = FailureContext(phase=PHASE, agent=AGENT, facts=_facts())

    controller.handle(_state(), failure, context)
    first_wait = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])
    controller.handle(_state(), failure, context)
    second_wait = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])

    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[UnavailabilityReason.DEAD_AGENT_NO_PROGRESS]
    assert first_wait == policy.base_backoff_ms
    assert second_wait > first_wait


def test_ac10_a_missing_credentials_failure_benches_the_agent_on_the_first_failure() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        MissingCredentialsError(AGENT, "ANTHROPIC_API_KEY not set"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac10_a_prompt_echo_broken_agent_benches_the_agent_on_the_first_failure() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="prompt_echo"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac14_the_bench_writes_no_spent_entry_and_no_spent_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert isinstance(controller.ledger.status(PHASE, AGENT), Spent) is False
    assert controller.snapshot()["spent_agents"] == {}


def test_ac5_a_healthy_probe_drops_the_legacy_broken_agent_reason() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.PASSED)

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="no_output"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    assert controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT]) == 0


def test_ac5_an_inconclusive_probe_does_not_bench_the_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.INCONCLUSIVE)

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="no_output"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    assert controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT]) == 0


def test_a_runtime_issued_termination_never_benches_and_leaves_the_ledger_untouched() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
    termination = TerminationRecord.create(
        TerminationIssuer.QUOTA_DETECTOR,
        observed_returncode=0,
        reported_returncode=0,
        liveness_observed=True,
    )

    controller.handle(
        _state(),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(
            phase=PHASE,
            agent=AGENT,
            facts=_facts(termination=termination),
        ),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    entry = _ledger_entry(controller, AGENT)
    assert _counter(entry, "consecutive_unproductive") == 0


def test_the_skip_same_agent_retries_route_records_the_attempt_and_benches_the_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
    intent = AgentRetryIntent(
        failure_reason="QuotaExhaustedError",
        skip_same_agent_retries=True,
        failed_agent_name=AGENT,
    )
    state = _state(agents=(AGENT, OTHER_AGENT), intent=intent)
    event = AgentFailureEvent(
        phase=PHASE,
        agent=AGENT,
        artifact_valid=False,
        workspace_changed=False,
        facts=_facts(),
    )

    reduced_state, _effects = reduce(state, event, _policy(), recovery=controller)

    entry = _ledger_entry(controller, AGENT)
    assert _counter(entry, "consecutive_unproductive") == 1
    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
    chain = reduced_state.chain_for_phase(PHASE)
    assert chain is not None
    assert chain.current_index == 1


def test_the_legacy_broken_agent_classification_is_unchanged_without_a_verdict_runner() -> None:
    failure = FailureClassifier().classify(
        BrokenAgentExitError(AGENT, reason="no_output"),
        phase=PHASE,
        agent=AGENT,
        connectivity_state="offline",
    )

    assert failure.is_unavailable is True
    assert failure.unavailability_reason == UnavailabilityReason.BROKEN_AGENT


def test_the_legacy_broken_agent_route_is_unchanged_without_a_verdict_runner() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="no_output"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.BROKEN_AGENT)


def test_the_bench_warning_carries_the_verdict_evidence(warning_sink: list[str]) -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
    termination = TerminationRecord.create(
        TerminationIssuer.AGENT_EXIT,
        observed_returncode=1,
        reported_returncode=0,
    )

    controller.handle(
        _state(),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(
            phase=PHASE,
            agent=AGENT,
            facts=_facts(termination=termination),
        ),
    )

    bench_logs = [line for line in warning_sink if "Confirmed agent unavailability" in line]
    assert bench_logs, (
        f"the bench must emit a structured warning (observed warnings: {warning_sink!r})"
    )
    log = bench_logs[0]
    assert "facts=" in log
    assert "attribution=" in log
    assert "probe_outcome=" in log
    assert "termination=" in log
    assert "return_code_discrepancy=" in log
    assert str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS) in log
