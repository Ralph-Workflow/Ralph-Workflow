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
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import decide_dead_agent_verdict
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.typed_signal import TypedSignal
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.policy.models import PipelinePolicy
    from ralph.recovery.attribution import AttributionVerdict
    from ralph.recovery.dead_agent_verdict import PersistedVerdict

PHASE = "development"
AGENT = "claude"
OTHER_AGENT = "opencode"
QuotaExhaustedError = subprocess_executor.QuotaExhaustedError

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_ac10_a_typed_quota_failure_cools_the_agent_down_on_the_first_failure": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ac10_a_typed_quota_failure_benches_the_agent_on_the_first_failure with an unchanged body"
    ),
    "test_ac10_a_missing_credentials_failure_cools_the_agent_down_on_the_first_failure": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ac10_a_missing_credentials_failure_benches_the_agent_on_the_first_failure with an unchanged body"
    ),
    "test_ac10_a_prompt_echo_broken_agent_cools_the_agent_down_on_the_first_failure": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ac10_a_prompt_echo_broken_agent_benches_the_agent_on_the_first_failure with an unchanged body"
    ),
    "test_ac14_the_dead_agent_cooldown_writes_no_non_expiring_entry (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ac14_the_bench_writes_no_spent_entry_and_no_spent_agent with an unchanged body"
    ),
    "test_ac5_an_inconclusive_probe_does_not_cool_the_agent_down": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_ac5_an_inconclusive_probe_does_not_bench_the_agent with an unchanged body"
    ),
    "test_a_runtime_issued_quota_termination_cools_without_a_dead_agent_cooldown_or_charge (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_a_runtime_issued_quota_termination_cools_without_benching_or_charging with an unchanged body"
    ),
    "test_the_skip_same_agent_retries_route_records_the_attempt_and_cools_the_agent_down": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_skip_same_agent_retries_route_records_the_attempt_and_benches_the_agent with an unchanged body"
    ),
    "test_the_dead_agent_cooldown_warning_carries_the_verdict_evidence": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_bench_warning_carries_the_verdict_evidence with an unchanged body"
    ),
    "test_dead_agent_cooldown_routes module": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; module renamed from tests/recovery/test_dead_agent_bench_routes.py with unchanged content"
    ),
    "test_a_runtime_issued_quota_termination_cools_without_a_dead_agent_cooldown_or_charge": "a provider quota kill of a live session now cools the agent so the next selection falls over instead of re-invoking the rate-limited agent; the ledger-untouched and no-bench checks are kept",
    "test_ac14_the_dead_agent_cooldown_writes_no_non_expiring_entry": "operator rule: an agent is either on cooldown or not; the Spent type and spent_agents key are gone, so the test asserts the bench is a dead_agent_no_progress cooldown that expires and leaves the agent selectable",
    "test_the_injected_verdict_runner_receives_the_probe_reason_and_elapsed_ms": "the injected dead_agent_verdict_runner seam must receive the probe reason and the elapsed time so the persisted verdict record keeps the probe evidence instead of dropping it; the test records the seam kwargs and asserts the persisted record carries the same reason",
}


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
        probe_reason: str = "",
        probe_elapsed_ms: int | None = None,
    ) -> PersistedVerdict | None:
        if facts is None or attribution is None:
            return None
        return decide_dead_agent_verdict(
            facts,
            attribution,
            probe_outcome,
            typed_signal,
            probe_reason=probe_reason,
            probe_elapsed_ms=probe_elapsed_ms,
        )

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
def test_ac10_a_typed_quota_failure_cools_the_agent_down_on_the_first_failure(
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


def test_ac10_a_missing_credentials_failure_cools_the_agent_down_on_the_first_failure() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        MissingCredentialsError(AGENT, "ANTHROPIC_API_KEY not set"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac10_a_prompt_echo_broken_agent_cools_the_agent_down_on_the_first_failure() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="prompt_echo"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _bench_reason(controller) == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_ac14_the_dead_agent_cooldown_writes_no_non_expiring_entry() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    controller.handle(
        _state(),
        QuotaExhaustedError(AGENT, "credits exhausted for this account"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert "spent_agents" not in controller.snapshot()
    status = controller.ledger.status(PHASE, AGENT)
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
    clock.advance((status.until_ms - int(clock.monotonic() * 1000)) / 1000.0 + 0.001)
    assert controller.ledger.status(PHASE, AGENT).is_eligible
    assert controller.unavailability_store.is_available(PHASE, AGENT) is True


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


def test_ac5_an_inconclusive_probe_does_not_cool_the_agent_down() -> None:
    clock = FakeClock(start=0.0)
    controller = _probe_controller(clock, ProbeOutcome.INCONCLUSIVE)

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="no_output"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    assert controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT]) == 0


def test_a_runtime_issued_quota_termination_cools_without_a_dead_agent_cooldown_or_charge() -> None:
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

    assert _bench_reason(controller) == str(UnavailabilityReason.OUT_OF_CREDITS)
    entry = _ledger_entry(controller, AGENT)
    assert _counter(entry, "consecutive_unproductive") == 0


def test_the_skip_same_agent_retries_route_records_the_attempt_and_cools_the_agent_down() -> None:
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
        facts=AttemptFacts(
            artifact_valid=False,
            typed_signal=TypedSignal(
                name="QuotaExhaustedError",
                provider_originated=True,
                agent_initiated=True,
            ),
        ),
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


def test_the_dead_agent_cooldown_warning_carries_the_verdict_evidence(
    warning_sink: list[str],
) -> None:
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


def test_the_injected_verdict_runner_receives_the_probe_reason_and_elapsed_ms() -> None:
    clock = FakeClock(start=0.0)
    recorded: dict[str, object] = {}

    def probe_runner(phase: str, agent: str) -> ProbeResult:
        del phase
        del agent
        return ProbeResult(
            outcome=ProbeOutcome.INCONCLUSIVE,
            detail="idle timeout",
            reason="probe_terminated_by_runtime:supervisor_idle:rc=9",
            elapsed_ms=456,
        )

    def verdict_runner(
        *,
        facts: AttemptFacts | None,
        attribution: AttributionVerdict | None,
        probe_outcome: ProbeOutcome | None,
        typed_signal: TypedSignal | None,
        probe_reason: str = "",
        probe_elapsed_ms: int | None = None,
    ) -> PersistedVerdict | None:
        recorded["probe_reason"] = probe_reason
        recorded["probe_elapsed_ms"] = probe_elapsed_ms
        if facts is None or attribution is None:
            return None
        return decide_dead_agent_verdict(
            facts,
            attribution,
            probe_outcome,
            typed_signal,
            probe_reason=probe_reason,
            probe_elapsed_ms=probe_elapsed_ms,
        )

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            dead_agent_probe_runner=probe_runner,
            dead_agent_verdict_runner=verdict_runner,
        )
    )

    controller.handle(
        _state(),
        BrokenAgentExitError(AGENT, reason="no_output"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert recorded["probe_reason"] == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert recorded["probe_elapsed_ms"] == 456
    record = controller.last_verdict_record
    assert record is not None
    assert record["probe_reason"] == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert record["probe_elapsed_ms"] == 456
