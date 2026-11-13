from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import TYPE_CHECKING, get_args, get_origin

import pytest

from ralph.agents.invoke import AgentInvocationError, AttemptEnded
from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_finalizer import AttemptFinalization, AttemptFinalizer
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.attempt_runner import (
    _attempt_facts,
    build_entry_point_recovery_controller,
    register_entry_point,
    registered_entry_points,
    registered_runners,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import (
    AttemptRunnerDeps,
    RawAgentInvoke,
    effect_attempt_invoke,
    raw_attempt_invoke,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.recovery.agent_config_fingerprint import agent_config_fingerprint
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attempt_outcome import AttemptOutcome
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

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


def _effect_adapter_context(
    tmp_path: Path,
) -> tuple[DisplayContext, PipelineDeps, WorkspaceScope]:
    display_context = make_display_context()
    pipeline_deps = PipelineDeps(display_context=display_context)
    workspace_scope = WorkspaceScope(root=tmp_path)
    return display_context, pipeline_deps, workspace_scope


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


def test_a_reraised_already_finalized_failure_charges_the_ledger_once() -> None:
    exc = RuntimeError("attempt failed terminally")

    def invoke(_target: object, **kwargs: object) -> object:
        finalizer = kwargs["attempt_finalizer"]
        assert callable(finalizer)
        finalizer(AttemptFinalization(1, True, exc, None, None, None))
        raise exc

    deps = _deps(invoke)
    controller = _controller(deps)
    with pytest.raises(RuntimeError, match="attempt failed terminally"):
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=_effect(),
            config=_config(),
            controller=controller,
            deps=deps,
            run_id="run-1",
        )
    _assert_exactly_one_ledger_charge(controller)


def test_a_terminal_exception_that_was_never_finalized_still_charges() -> None:
    first = RuntimeError("attempt one failed")
    terminal = RuntimeError("terminal raise without finalization")

    def invoke(_target: object, **kwargs: object) -> object:
        finalizer = kwargs["attempt_finalizer"]
        assert callable(finalizer)
        finalizer(AttemptFinalization(1, True, first, None, None, None))
        raise terminal

    deps = _deps(invoke)
    controller = _controller(deps)
    with pytest.raises(RuntimeError, match="terminal raise without finalization"):
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=_effect(),
            config=_config(),
            controller=controller,
            deps=deps,
            run_id="run-1",
        )
    status = controller.ledger.status(_PHASE, _AGENT)
    assert status.is_spent is True


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


@pytest.mark.parametrize("binding", ["positional", "keyword"])
def test_attempt_runner_regression_effect_adapter_forwards_arguments(
    tmp_path: Path,
    binding: str,
) -> None:
    calls: list[dict[str, object]] = []

    def runner(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        display_context: DisplayContext | None,
        **kwargs: object,
    ) -> PipelineEvent:
        calls.append(
            {
                "effect": effect,
                "config": config,
                "pipeline_deps": pipeline_deps,
                "workspace_scope": workspace_scope,
                "display_context": display_context,
                "options": kwargs,
            }
        )
        return PipelineEvent.AGENT_SUCCESS

    display_context, pipeline_deps, workspace_scope = _effect_adapter_context(tmp_path)
    effect = _effect()
    config = _config()
    finalizer = object()
    state = object()
    invoke = effect_attempt_invoke(runner)

    if binding == "positional":
        result = invoke(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            display_context=display_context,
            attempt_finalizer=finalizer,
            state=state,
        )
    else:
        result = invoke(
            effect,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            display_context=display_context,
            attempt_finalizer=finalizer,
            state=state,
        )

    assert result is PipelineEvent.AGENT_SUCCESS
    assert calls == [
        {
            "effect": effect,
            "config": config,
            "pipeline_deps": pipeline_deps,
            "workspace_scope": workspace_scope,
            "display_context": display_context,
            "options": {"attempt_finalizer": finalizer, "state": state},
        }
    ]


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        ("target", "effect attempts require an InvokeAgentEffect target"),
        ("config", "effect attempts require a UnifiedConfig"),
        ("pipeline_deps", "effect attempts require PipelineDeps"),
        ("workspace_scope", "effect attempts require WorkspaceScope"),
        ("display_context", "effect attempts require DisplayContext or None"),
    ],
)
def test_attempt_runner_regression_effect_adapter_rejects_invalid_arguments(
    tmp_path: Path,
    invalid: str,
    message: str,
) -> None:
    calls: list[object] = []

    def runner(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        display_context: DisplayContext | None,
        **kwargs: object,
    ) -> PipelineEvent:
        calls.append(effect)
        return PipelineEvent.AGENT_SUCCESS

    display_context, pipeline_deps, workspace_scope = _effect_adapter_context(tmp_path)
    effect = _effect()
    config = _config()
    arguments: dict[str, object] = {
        "config": config,
        "pipeline_deps": pipeline_deps,
        "workspace_scope": workspace_scope,
        "display_context": display_context,
    }
    if invalid == "target":
        target = config.agents[_AGENT]
    else:
        target = effect
        arguments[invalid] = object()

    invoke = effect_attempt_invoke(runner)
    with pytest.raises(TypeError, match=message):
        invoke(target, **arguments)

    assert calls == []


def _carried_raw_invoke(facts: AttemptFacts) -> RawAgentInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> object:
        del config, prompt_file, options
        yield "line\n"
        return AttemptEnded(facts=facts)

    return _invoke


def test_raw_attempt_invoke_returns_the_carried_attempt_ended() -> None:
    recorder_facts = AttemptFacts(
        event_kinds=("output_line",),
        termination=TerminationRecord.create(TerminationIssuer.AGENT_EXIT),
    )

    carried = raw_attempt_invoke(_carried_raw_invoke(recorder_facts))(
        _config().agents[_AGENT],
        prompt_file="prompt.md",
    )

    assert isinstance(carried, AttemptEnded)
    assert carried.facts is recorder_facts


def test_raw_attempt_invoke_without_a_carried_value_returns_agent_success() -> None:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: object | None = None,
    ) -> object:
        del config, prompt_file, options
        yield "line\n"

    carried = raw_attempt_invoke(_invoke)(
        _config().agents[_AGENT],
        prompt_file="prompt.md",
    )

    assert carried is PipelineEvent.AGENT_SUCCESS


def test_a_successful_raw_attempt_result_uses_the_carried_recorder_facts() -> None:
    recorder_facts = AttemptFacts(
        event_kinds=("output_line",),
        tool_calls=("read",),
        termination=TerminationRecord.create(TerminationIssuer.AGENT_EXIT),
    )
    deps = _deps(raw_attempt_invoke(_carried_raw_invoke(recorder_facts)))
    controller = _controller(deps)
    result = run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        invocation=_config().agents[_AGENT],
        config=_config(),
        controller=controller,
        deps=deps,
        prompt_file="prompt.md",
    )

    assert result.event is PipelineEvent.AGENT_SUCCESS
    assert result.facts.event_kinds == ("output_line",)
    assert result.facts.tool_calls == ("read",)
    assert result.facts.artifact_valid is True
    assert result.facts.termination is not None
    assert result.facts.termination.issuer is TerminationIssuer.AGENT_EXIT
    assert result.outcome.kind is AttemptOutcomeKind.COMPLETED


def test_attempt_facts_keeps_workspace_changed_override_on_carried_facts() -> None:
    carried = AttemptFacts(event_kinds=("output_line",))
    finalization = AttemptFinalization(1, False, None, True, None, None)

    facts = _attempt_facts(finalization, carried)

    assert facts.event_kinds == ("output_line",)
    assert facts.workspace_changed is True
    assert facts.artifact_valid is True


def test_a_raised_attempt_ended_failure_reads_the_typed_facts() -> None:
    error = AgentInvocationError(_AGENT, 1, "boom")

    def invoke(_target: object, **_kwargs: object) -> object:
        raise error

    deps = _deps(invoke)
    controller = _controller(deps)
    with pytest.raises(AgentInvocationError):
        run_agent_attempt(
            AgentEntryPoint.MAIN_PHASE_LOOP,
            effect=_effect(),
            config=_config(),
            controller=controller,
            deps=deps,
        )

    assert isinstance(error, AttemptEnded)
    assert error.facts.termination is not None
    assert error.facts.termination.issuer is TerminationIssuer.AGENT_EXIT
    _assert_exactly_one_ledger_charge(controller)
