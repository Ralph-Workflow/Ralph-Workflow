from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import TYPE_CHECKING, get_args, get_origin

import pytest

from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_finalizer import AttemptFinalization, AttemptFinalizer
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    register_entry_point,
    registered_entry_points,
    registered_runners,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.agent_config_fingerprint import agent_config_fingerprint
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import ProbeOutcome

if TYPE_CHECKING:
    from ralph.recovery.controller import RecoveryController

_PHASE = "development"
_AGENT = "fake"
_FINGERPRINT = agent_config_fingerprint(
    _AGENT,
    cmd="/opt/bin/fake-agent --secret-not-hashed",
    model="model-a",
    model_flag="--model",
    json_parser="generic",
    transport="generic",
)


def _config() -> UnifiedConfig:
    return UnifiedConfig(
        agents={
            _AGENT: AgentConfig(
                cmd="/opt/bin/fake-agent --secret-not-hashed",
                model="model-a",
                model_flag="--model",
                json_parser="generic",
                transport="generic",
            )
        }
    )


def _effect() -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name=_AGENT, phase=_PHASE, prompt_file="prompt.md")


def _no_probe(_phase: str, _agent: str) -> ProbeOutcome | None:
    return None


def _dead_probe(_phase: str, _agent: str) -> ProbeOutcome | None:
    return ProbeOutcome.FAILED_REPRODUCING_NO_WORK


def _deps(
    invoke: Callable[..., object],
    probe_runner: Callable[[str, str], ProbeOutcome | None] = _no_probe,
) -> AttemptRunnerDeps:
    return AttemptRunnerDeps(SystemClock(), invoke, probe_runner, None, None)


def _controller(deps: AttemptRunnerDeps) -> RecoveryController:
    return build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=_config(),
        policy_bundle=None,
        deps=deps,
        agent_max_consecutive_unproductive=1,
        agent_max_total_unproductive=2,
    )


def _assert_exactly_one_ledger_charge(controller: RecoveryController) -> None:
    status = controller.ledger.status(_PHASE, _AGENT)
    assert status.is_cooldown is True
    assert status.is_spent is False


def _entry_point_runner(*_args: object, **_kwargs: object) -> AttemptResult:
    outcome = AttemptOutcome(AttemptOutcomeKind.COMPLETED, None, None, "completed")
    return AttemptResult(None, AttemptFacts(artifact_valid=True), outcome, ((1, outcome),))


def _fail_then_succeed_invoke(_target: object, **kwargs: object) -> object:
    finalizer = kwargs["attempt_finalizer"]
    assert callable(finalizer)
    finalizer(AttemptFinalization(1, True, RuntimeError(), None, None, None))
    return PipelineEvent.AGENT_SUCCESS


def test_agent_entry_point_contains_every_agent_invoking_route() -> None:
    assert tuple(AgentEntryPoint) == (
        AgentEntryPoint.MAIN_PHASE_LOOP,
        AgentEntryPoint.PARALLEL_WORKER,
        AgentEntryPoint.INTEGRATION_CONTINUATION,
        AgentEntryPoint.CONFLICT_RESOLUTION,
        AgentEntryPoint.COMMIT_GENERATION,
        AgentEntryPoint.SMOKE,
        AgentEntryPoint.PROJECT_POLICY,
        AgentEntryPoint.SESSION_RUNTIME,
        AgentEntryPoint.CANARY_PROBE,
    )


def test_attempt_finalizer_publishes_frozen_per_attempt_contract() -> None:
    assert tuple(field.name for field in fields(AttemptFinalization)) == (
        "attempt_ordinal",
        "attempt_failed",
        "exc",
        "workspace_changed",
        "typed_signal",
        "retryable_reason",
    )
    assert get_origin(AttemptFinalizer) is Callable
    assert get_args(AttemptFinalizer)[0] == [AttemptFinalization]
    frozen = AttemptFinalization(1, False, None, None, None, None)
    with pytest.raises(AttributeError):
        frozen.attempt_ordinal = 2


def test_entry_point_registry_reflects_each_registration() -> None:
    entry_point = AgentEntryPoint.SMOKE
    if entry_point not in registered_entry_points():
        register_entry_point(entry_point, _entry_point_runner)
    assert entry_point in registered_entry_points()
    assert entry_point in registered_runners()


def test_entry_point_registry_rejects_duplicate_registration() -> None:
    entry_point = AgentEntryPoint.COMMIT_GENERATION
    if entry_point not in registered_entry_points():
        register_entry_point(entry_point, _entry_point_runner)
    with pytest.raises(ValueError, match=entry_point.value):
        register_entry_point(entry_point, _entry_point_runner)
    assert entry_point in registered_entry_points()


@pytest.mark.parametrize("entry_point", tuple(AgentEntryPoint))
def test_construction_seam_returns_a_working_controller_per_entry_point(
    entry_point: AgentEntryPoint,
) -> None:
    deps = _deps(lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS, _dead_probe)
    controller = build_entry_point_recovery_controller(
        entry_point,
        config=_config(),
        policy_bundle=None,
        deps=deps,
    )
    assert controller.clock is deps.clock
    assert controller.cycle_cap == 200
    outcome = controller.finalize_attempt(
        _PHASE,
        _AGENT,
        facts=AttemptFacts(artifact_valid=True),
        attempt_failed=False,
    )
    assert outcome.kind is AttemptOutcomeKind.COMPLETED


def test_two_attempts_are_judged_once_each_and_the_failure_is_charged_once() -> None:
    deps = _deps(_fail_then_succeed_invoke)
    controller = _controller(deps)
    result = run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
        run_id="run-1",
        session_id="session-1",
    )

    assert tuple(ordinal for ordinal, _outcome in result.attempt_outcomes) == (1, 2)
    assert result.outcome.kind is AttemptOutcomeKind.COMPLETED
    assert result.attempt_outcomes[0][1].kind is AttemptOutcomeKind.UNKNOWN
    _assert_exactly_one_ledger_charge(controller)


def test_the_attempt_key_follows_the_published_attempt_finalization_format() -> None:
    deps = _deps(_fail_then_succeed_invoke)
    controller = _controller(deps)
    run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
    )

    memoized = controller.finalize_attempt(
        _PHASE,
        _AGENT,
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
        attempt_key="main_phase_loop:development:fake:1",
    )
    assert memoized.kind is AttemptOutcomeKind.UNKNOWN
    _assert_exactly_one_ledger_charge(controller)


def test_a_reducer_style_finalization_claims_the_terminal_outcome_without_a_second_charge() -> None:
    deps = _deps(_fail_then_succeed_invoke)
    controller = _controller(deps)
    run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
    )

    claimed = controller.finalize_attempt(
        _PHASE,
        _AGENT,
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    assert claimed.kind is AttemptOutcomeKind.COMPLETED
    _assert_exactly_one_ledger_charge(controller)


def test_a_judged_failure_benches_the_agent_and_a_later_success_clears_the_bench() -> None:
    deps = _deps(_fail_then_succeed_invoke, _dead_probe)
    controller = _controller(deps)
    result = run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
        run_id="run-1",
        session_id="session-1",
    )

    assert result.attempt_outcomes[0][1].kind is AttemptOutcomeKind.AGENT_DEAD
    assert result.outcome.kind is AttemptOutcomeKind.COMPLETED
    assert controller.unavailability_store.is_available(_PHASE, _AGENT) is True
    assert controller.false_bench_feedback.count == 1


def test_a_bench_feedback_event_carries_the_agent_configuration_fingerprint() -> None:
    deps = _deps(_fail_then_succeed_invoke, _dead_probe)
    controller = _controller(deps)
    run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
        run_id="run-1",
        session_id="session-1",
    )

    events = controller.false_bench_feedback.events
    assert len(events) == 1
    assert events[0]["config_fingerprint"] == _FINGERPRINT
    assert events[0]["session_ref"] == {
        "run_id": "run-1",
        "phase": _PHASE,
        "agent": _AGENT,
        "entry_point": "main_phase_loop",
        "session_id": "session-1",
    }


def test_the_canary_probe_entry_point_never_benches_and_never_charges_the_ledger() -> None:
    def failing_invoke(_target: object, **kwargs: object) -> object:
        finalizer = kwargs["attempt_finalizer"]
        assert callable(finalizer)
        finalizer(AttemptFinalization(1, True, None, None, None, None))
        return PipelineEvent.AGENT_FAILURE

    deps = _deps(failing_invoke, _dead_probe)
    controller = _controller(deps)
    result = run_agent_attempt(
        AgentEntryPoint.CANARY_PROBE,
        effect=_effect(),
        config=_config(),
        controller=controller,
        deps=deps,
    )

    assert result.outcome.benches_agent is False
    assert controller.unavailability_store.is_available(_PHASE, _AGENT) is True
    assert controller.false_bench_feedback.count == 0
    assert controller.ledger.status(_PHASE, _AGENT).is_eligible is True


def test_a_raised_attempt_is_finalized_as_a_failure() -> None:
    def invoke(_target: object, **_kwargs: object) -> object:
        raise RuntimeError("agent crashed")

    deps = _deps(invoke)
    controller = _controller(deps)
    with pytest.raises(RuntimeError):
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=_effect(),
            config=_config(),
            controller=controller,
            deps=deps,
        )
    _assert_exactly_one_ledger_charge(controller)


def test_attempt_result_preserves_ordered_attempt_outcomes() -> None:
    first = AttemptOutcome(AttemptOutcomeKind.UNKNOWN, None, None, "first")
    second = AttemptOutcome(AttemptOutcomeKind.COMPLETED, None, None, "second")
    result = AttemptResult(
        event="success",
        facts=AttemptFacts(artifact_valid=True),
        outcome=second,
        attempt_outcomes=((1, first), (2, second)),
    )
    assert result.attempt_outcomes == ((1, first), (2, second))
