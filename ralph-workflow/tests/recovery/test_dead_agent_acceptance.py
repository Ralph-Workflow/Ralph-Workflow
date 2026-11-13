from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents import subprocess_executor
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    BrokenAgentExitError,
    MissingCredentialsError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.checkpoint import load, save
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.pipeline.run_loop import build_recovery_controller
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Spent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    _build_policy_bundle,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.policy.models import PipelinePolicy

PHASE = "development"
AGENT = "agent_a"
OTHER = "agent_b"
QuotaExhaustedError = subprocess_executor.QuotaExhaustedError
LIMIT_PHRASE = "You've hit your session limit"
DEAD = str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)

RALPH_TEST_CHANGE_REASONS = {
    "test_ac15_an_unknown_workspace_change_does_not_reset_the_consecutive_streak": (
        "the private streak value is replaced by the public demotion and cooldown status"
    ),
    "test_ac15_an_observed_workspace_change_still_resets_the_consecutive_streak": (
        "the private streak value is replaced by the public eligible and available status"
    ),
}


def _policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _state(
    *,
    agents: tuple[str, ...] = (AGENT,),
    intent: AgentRetryIntent | None = None,
    connectivity: str = "online",
) -> PipelineState:
    return PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=list(agents), current_index=0, retries=0)},
        last_connectivity_state=connectivity,
        agent_retry_intent=intent if intent is not None else AgentRetryIntent(),
    )


def _skip_intent(reason: str = "QuotaExhaustedError") -> AgentRetryIntent:
    return AgentRetryIntent(
        failure_reason=reason, skip_same_agent_retries=True, failed_agent_name=AGENT
    )


def _facts(termination: TerminationRecord | None = None) -> AttemptFacts:
    return AttemptFacts(artifact_valid=False, termination=termination)


def _failure_event(facts: AttemptFacts | None = None) -> AgentFailureEvent:
    return AgentFailureEvent(
        phase=PHASE,
        agent=AGENT,
        artifact_valid=False,
        workspace_changed=False,
        facts=facts if facts is not None else _facts(),
    )


def _controller(clock: FakeClock | None = None) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(clock=clock or FakeClock(start=0.0))
    )


def _reasons(controller: RecoveryController) -> dict[str, object]:
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons")
    return dict(reasons) if isinstance(reasons, dict) else {}


@pytest.fixture
def info_logs() -> Iterator[list[str]]:
    captured: list[str] = []
    sink = logger.add(lambda message: captured.append(str(message)), level="INFO")
    try:
        yield captured
    finally:
        logger.remove(sink)


def _resolve(exc: Exception) -> AgentRetryIntent | None:
    return resolve_retry_intent(
        exc,
        phase=PHASE,
        agent=AGENT,
        session_id=None,
        inactivity_error_type=AgentInactivityTimeoutError,
    )


def test_ac1_tool_output_quoting_a_limit_phrase_does_not_skip_same_agent_retries() -> None:
    exc = AgentInvocationError(
        AGENT, 1, stderr="process crashed", parsed_output=[f"tool result: {LIMIT_PHRASE}"]
    )

    intent = _resolve(exc)

    assert intent is None or intent.skip_same_agent_retries is False


def test_ac1_a_limit_phrase_in_the_failure_message_still_skips_same_agent_retries() -> None:
    exc = AgentInvocationError(AGENT, 1, stderr=LIMIT_PHRASE)

    intent = _resolve(exc)

    assert intent is not None
    assert intent.skip_same_agent_retries is True


def test_ac1_a_typed_quota_error_still_skips_same_agent_retries() -> None:
    intent = _resolve(QuotaExhaustedError(AGENT, "credits exhausted"))

    assert intent is not None
    assert intent.skip_same_agent_retries is True


def test_ac1_tool_output_quoting_a_limit_phrase_does_not_make_a_timeout_non_retryable() -> None:
    exc = AgentInvocationError(
        AGENT,
        1,
        stderr="connection timed out",
        parsed_output=[f"tool result: {LIMIT_PHRASE}"],
    )

    assert retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is not None


def test_ac2_ac14_a_live_session_end_on_the_skip_route_is_never_spent() -> None:
    controller = _controller()
    state = _state(intent=_skip_intent("AgentInvocationError"))

    reduce(state, _failure_event(), _policy(), recovery=controller)

    assert isinstance(controller.ledger.status(PHASE, AGENT), Spent) is False
    assert controller.snapshot()["spent_agents"] == {}


def test_ac2_the_selection_evidence_never_labels_the_agent_spent_after_one_attempt(
    info_logs: list[str],
) -> None:
    controller = _controller()
    state = _state(agents=(AGENT, OTHER), intent=_skip_intent("AgentInvocationError"))

    reduce(state, _failure_event(), _policy(), recovery=controller)

    assert not [line for line in info_logs if f"{AGENT}: spent" in line]


@pytest.mark.parametrize("connectivity", ["online", "offline"])
@pytest.mark.parametrize(
    "exc",
    [
        QuotaExhaustedError(AGENT, "credits exhausted"),
        MissingCredentialsError(AGENT, "API key not set"),
        BrokenAgentExitError(AGENT, reason="prompt_echo"),
    ],
    ids=["quota", "credentials", "prompt_echo"],
)
def test_ac10_typed_provider_signals_bench_on_first_failure_without_a_probe(
    exc: Exception, connectivity: str
) -> None:
    controller = _controller()

    controller.handle(
        _state(connectivity=connectivity), exc, FailureContext(phase=PHASE, agent=AGENT)
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert _reasons(controller)[AGENT] == DEAD


def test_ac10_the_skip_route_benches_a_typed_signal_without_any_probe_runner() -> None:
    controller = _controller()

    reduce(
        _state(agents=(AGENT, OTHER), intent=_skip_intent()),
        _failure_event(),
        _policy(),
        recovery=controller,
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False
    assert controller.ledger.status(PHASE, AGENT).is_spent is False


def test_ac10_the_second_confirmed_failure_escalates_per_the_policy_table() -> None:
    controller = _controller()
    context = FailureContext(phase=PHASE, agent=AGENT)
    failure = QuotaExhaustedError(AGENT, "credits exhausted")
    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[UnavailabilityReason.DEAD_AGENT_NO_PROGRESS]

    controller.handle(_state(), failure, context)
    first = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])
    controller.handle(_state(), failure, context)
    second = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])

    assert first == policy.base_backoff_ms
    assert second == policy.base_backoff_ms * 2


def test_ac8_the_production_controller_benches_typed_signals_on_first_failure() -> None:
    bundle = _build_policy_bundle([AGENT, OTHER])
    controller, _cap = build_recovery_controller(
        _state(agents=(AGENT, OTHER)), bundle, UnifiedConfig.model_validate({})
    )

    controller.handle(
        _state(agents=(AGENT, OTHER)),
        QuotaExhaustedError(AGENT, "credits exhausted"),
        FailureContext(phase=PHASE, agent=AGENT),
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is False


def test_ac8_the_production_controller_never_benches_on_an_unresolvable_probe() -> None:
    bundle = _build_policy_bundle(["no_such_agent_cmd_xyz"])
    state = _state(agents=("no_such_agent_cmd_xyz",))
    controller, _cap = build_recovery_controller(state, bundle, UnifiedConfig.model_validate({}))

    controller.handle(
        state,
        AgentInvocationError("no_such_agent_cmd_xyz", 1, stderr="boom"),
        FailureContext(phase=PHASE, agent="no_such_agent_cmd_xyz", facts=_facts()),
    )

    assert _reasons(controller).get("no_such_agent_cmd_xyz") != DEAD


def test_ac8_an_inconclusive_probe_never_benches() -> None:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            dead_agent_probe_runner=lambda _phase, _agent: ProbeOutcome.INCONCLUSIVE,
        )
    )

    controller.handle(
        _state(),
        AgentInvocationError(AGENT, 1, stderr="boom"),
        FailureContext(phase=PHASE, agent=AGENT, facts=_facts()),
    )

    assert _reasons(controller).get(AGENT) != DEAD


def test_ac7_a_quota_detector_termination_is_an_ordinary_failure_not_a_bench() -> None:
    controller = _controller()
    termination = TerminationRecord.create(
        TerminationIssuer.QUOTA_DETECTOR,
        observed_returncode=0,
        reported_returncode=0,
        liveness_observed=True,
    )

    reduce(
        _state(agents=(AGENT, OTHER), intent=_skip_intent()),
        _failure_event(_facts(termination)),
        _policy(),
        recovery=controller,
    )

    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    assert controller.ledger.status(PHASE, AGENT).is_spent is False
    assert controller.snapshot()["spent_agents"] == {}


def test_ac7_the_runtime_attributed_end_is_labelled_with_its_cause_in_the_persisted_record(
    tmp_path: Path,
) -> None:
    controller = _controller()
    termination = TerminationRecord.create(TerminationIssuer.QUOTA_DETECTOR, liveness_observed=True)

    reduced, _effects = reduce(
        _state(agents=(AGENT, OTHER), intent=_skip_intent()),
        _failure_event(_facts(termination)),
        _policy(),
        recovery=controller,
    )
    path = tmp_path / "checkpoint.json"
    save(reduced, path)
    text = path.read_text(encoding="utf-8")

    assert "quota_detector" in text
    assert "not_agent_fault" in text


def test_ac13_the_verdict_evidence_is_persisted_through_the_checkpoint(tmp_path: Path) -> None:
    controller = _controller()
    termination = TerminationRecord.create(
        TerminationIssuer.AGENT_EXIT, observed_returncode=1, reported_returncode=0
    )

    reduced, _effects = reduce(
        _state(agents=(AGENT, OTHER), intent=_skip_intent()),
        _failure_event(_facts(termination)),
        _policy(),
        recovery=controller,
    )
    path = tmp_path / "checkpoint.json"
    save(reduced, path)
    text = path.read_text(encoding="utf-8")
    loaded = load(path)

    assert loaded is not None
    for key in (
        "agent_dead",
        "attribution",
        "probe_outcome",
        "termination",
        "return_code_discrepancy",
        "facts",
    ):
        assert key in text, f"persisted verdict record lacks {key!r}"
    assert load(path) == loaded


def test_ac12_the_cooldown_reason_and_policy_survive_a_snapshot_restore() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    failure = QuotaExhaustedError(AGENT, "credits exhausted")
    controller.handle(_state(), failure, FailureContext(phase=PHASE, agent=AGENT))
    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[UnavailabilityReason.DEAD_AGENT_NO_PROGRESS]

    restored = RecoveryController.from_snapshot(controller.snapshot(), clock=clock)

    assert _reasons(restored)[AGENT] == DEAD
    assert restored.unavailability_store.is_available(PHASE, AGENT) is False
    assert (
        restored.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])
        == policy.base_backoff_ms
    )
    restored.handle(_state(), failure, FailureContext(phase=PHASE, agent=AGENT))
    assert (
        restored.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT])
        == policy.base_backoff_ms * 2
    )


def test_ac11_a_benched_agent_is_not_reselected_while_the_peer_is_on_cooldown(
    tmp_path: Path,
) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    controller.unavailability_store.mark_unavailable(
        PHASE, OTHER, UnavailabilityReason.OUT_OF_CREDITS
    )

    outcome = run_scenario(
        tmp_path,
        {(AGENT, 1): AgentAttemptResult(raise_error=QuotaExhaustedError(AGENT, "credits"))},
        agents=(AGENT, OTHER),
        clock=clock,
        controller=controller,
        advance_clock_on_wait=False,
        max_iterations=6,
    )

    assert outcome.invocations == [AGENT]
    evidence = [line for line in outcome.logs if AGENT in line and "remaining" in line]
    assert evidence, f"selection evidence must list agent, reason and remaining: {outcome.logs!r}"
    assert any(DEAD in line for line in evidence)


def test_ac15_repeated_live_session_ends_below_the_thresholds_never_reach_spent() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    for _ in range(2):
        outcome = ledger.record(
            AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=None)
        )

    assert isinstance(outcome.status, Spent) is False


def test_ac15_an_unknown_workspace_change_does_not_reset_the_consecutive_streak() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    ledger.record(AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=False))
    ledger.record(AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=False))
    outcome = ledger.record(AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=None))

    assert outcome.demoted is True
    assert ledger.status(PHASE, AGENT).is_cooldown is True
    assert ledger.is_available(PHASE, AGENT) is False


def test_ac15_an_observed_workspace_change_still_resets_the_consecutive_streak() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    ledger.record(AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=False))
    outcome = ledger.record(AttemptFact(PHASE, AGENT, artifact_valid=False, workspace_changed=True))

    assert outcome.demoted is False
    assert ledger.status(PHASE, AGENT).is_eligible is True
    assert ledger.is_available(PHASE, AGENT) is True
