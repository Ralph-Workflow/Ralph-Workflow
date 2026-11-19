from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import AgentInvocationError, attempt_facts_for_failure
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_entry_point import AgentEntryPoint, registered_runners
from ralph.pipeline.attempt_finalizer import AttemptFinalization
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests.recovery.test_cooldown_real_invocation_main_phases import (
    _FALLBACK,
    _RUN_ID,
    _MainPhaseHarness,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.effects import InvokeAgentEffect

_PHASE = "development_analysis"
_OVERLOAD = (
    "unexpected status 503 Service Unavailable: Service temporarily unavailable due to "
    "resource pressure. Retry shortly., url: http://127.0.0.1:20128/v1/responses"
)


def _codex_overload_transcript() -> list[str]:
    frames: list[dict[str, object]] = [
        {"type": "thread.started", "thread_id": "01a1f398-c334-7750-beec-4185b9d59262"},
        {"type": "turn.started"},
        *(
            {"type": "error", "message": f"Reconnecting... {attempt}/5 ({_OVERLOAD})"}
            for attempt in range(1, 6)
        ),
        {"type": "error", "message": _OVERLOAD},
        {"type": "turn.failed", "error": {"message": _OVERLOAD}},
    ]
    return [json.dumps(frame) for frame in frames]


def _codex_overload_exit(agent: str) -> AgentInvocationError:
    return AgentInvocationError(
        agent,
        1,
        "2026-11-19T03:17:00Z ERROR codex_rollout::list: state db missing rollout path",
        parsed_output=_codex_overload_transcript(),
    )


def _invoke_overloaded_agent(harness: _MainPhaseHarness, effect: InvokeAgentEffect) -> object:
    def runner(
        target: InvokeAgentEffect,
        *_args: object,
        **kwargs: object,
    ) -> PipelineEvent:
        agent = target.agent_name
        harness.invoked.append(agent)
        failure = _codex_overload_exit(agent)
        finalizer = kwargs.get("attempt_finalizer")
        assert callable(finalizer)
        finalizer(
            AttemptFinalization(
                attempt_ordinal=1,
                attempt_failed=True,
                exc=failure,
                workspace_changed=False,
                typed_signal=attempt_facts_for_failure(failure).typed_signal,
                retryable_reason=None,
            )
        )
        return PipelineEvent.AGENT_FAILURE

    route = registered_runners()[AgentEntryPoint.MAIN_PHASE_LOOP]
    deps = AttemptRunnerDeps(harness.clock, PRODUCTION_INVOKE, None, None, None)
    token = install_agent_effect_dependencies(
        agent_runner=runner,
        recovery_controller=harness.controller,
        pipeline_deps=harness.deps,
    )
    try:
        return route(
            effect=effect,
            config=harness.config,
            controller=harness.controller,
            deps=deps,
            state=harness.state,
            run_id=_RUN_ID,
            pipeline_deps=harness.deps,
            workspace_scope=harness.scope,
            display_context=harness.display_context,
        )
    finally:
        reset_agent_effect_dependencies(token)


def test_a_provider_overload_in_the_transcript_cools_the_agent_and_rotates_the_chain(
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(_PHASE, tmp_git_repo)

    effect = harness.dispatch()
    assert effect.agent_name == harness.primary
    _invoke_overloaded_agent(harness, effect)

    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.PROVIDER_UNAVAILABLE)
    assert harness.invoked == [harness.primary]

    assert harness.dispatch().agent_name != harness.primary

    wait_ms = harness.controller.earliest_eligible_wait_ms(_PHASE, [harness.primary])
    assert wait_ms is not None
    assert wait_ms > 0
    harness.clock.advance(wait_ms / 1000)
    assert isinstance(harness.controller.agent_eligibility(_PHASE, harness.primary), Eligible)


@pytest.mark.parametrize("frame_type", ["error", "turn.failed"])
def test_provider_overload_is_read_from_transport_frames_not_from_agent_prose(
    frame_type: str,
) -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock(start=0.0)))
    controller.register_phase_chain(_PHASE, ["codex", _FALLBACK])
    prose = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": f"The API returned {_OVERLOAD}"},
        }
    )
    frame = (
        {"type": "error", "message": _OVERLOAD}
        if frame_type == "error"
        else {"type": "turn.failed", "error": {"message": _OVERLOAD}}
    )

    prose_only = AgentInvocationError("codex", 1, "exit", parsed_output=[prose])
    controller.finalize_attempt(
        _PHASE,
        "codex",
        facts=attempt_facts_for_failure(prose_only),
        attempt_failed=True,
        exc=prose_only,
    )
    assert isinstance(controller.agent_eligibility(_PHASE, "codex"), Eligible)

    framed = AgentInvocationError("codex", 1, "exit", parsed_output=[prose, json.dumps(frame)])
    controller.finalize_attempt(
        _PHASE,
        "codex",
        facts=attempt_facts_for_failure(framed),
        attempt_failed=True,
        exc=framed,
    )
    status = controller.agent_eligibility(_PHASE, "codex")
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.PROVIDER_UNAVAILABLE)
