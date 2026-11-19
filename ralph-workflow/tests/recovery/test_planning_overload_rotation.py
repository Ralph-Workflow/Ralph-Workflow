from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_entry_point import AgentEntryPoint, registered_runners
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.timeout_defaults import AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
from tests.recovery.test_cooldown_real_invocation_main_phases import _RUN_ID, _MainPhaseHarness

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.pipeline.effects import InvokeAgentEffect

_PHASE = "planning"
_OPENCODE_OVERLOAD = "Service temporarily unavailable due to resource pressure. Retry shortly."
_SLOW_ATTEMPT_SECONDS = 300.0


def _opencode_overload_transcript() -> list[str]:
    frames: list[dict[str, object]] = [
        {"type": "step_start", "sessionID": "ses_e0bedcf49ffeaex0EYWqkhHWQa"},
        {
            "type": "error",
            "sessionID": "ses_e0bedcf49ffeaex0EYWqkhHWQa",
            "error": {
                "name": "APIError",
                "data": {
                    "message": _OPENCODE_OVERLOAD,
                    "statusCode": 503,
                    "isRetryable": True,
                },
            },
        },
    ]
    return [json.dumps(frame) for frame in frames]


def _opencode_harness(tmp_git_repo: Path) -> tuple[_MainPhaseHarness, list[str]]:
    harness = _MainPhaseHarness(_PHASE, tmp_git_repo)
    launched: list[str] = []

    def invoke_agent(
        agent_config: AgentConfig, prompt_file: str, *, options: object = None, **_kwargs: object
    ) -> Iterator[str]:
        del agent_config, prompt_file, options
        launched.append(harness.primary)
        return iter(_opencode_overload_transcript())

    harness.config = UnifiedConfig(
        agents={
            name: AgentConfig(
                cmd="opencode run",
                transport="opencode",
                json_parser="opencode",
                output_flag="--format json",
            )
            for name in harness.agents
        }
    )
    harness.registry = AgentRegistry.from_config(harness.config)
    harness.deps = replace(harness.deps, invoke_agent=invoke_agent)
    return harness, launched


def _route(harness: _MainPhaseHarness, effect: InvokeAgentEffect) -> object:
    prompt = harness.scope.root / effect.prompt_file
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("plan the work\n", encoding="utf-8")
    route = registered_runners()[AgentEntryPoint.MAIN_PHASE_LOOP]
    deps = AttemptRunnerDeps(harness.clock, PRODUCTION_INVOKE, None, None, None)
    token = install_agent_effect_dependencies(
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


def test_an_opencode_resource_pressure_error_cools_the_agent_and_rotates_the_chain(
    tmp_git_repo: Path,
) -> None:
    harness, launched = _opencode_harness(tmp_git_repo)

    effect = harness.dispatch()
    assert effect.agent_name == harness.primary
    _route(harness, effect)

    assert launched == [harness.primary]
    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.PROVIDER_UNAVAILABLE)
    assert harness.dispatch().agent_name != harness.primary


def _exits_cleanly_without_a_plan(
    harness: _MainPhaseHarness, *, seconds: float = 0.0
) -> Callable[..., PipelineEvent]:
    def runner(effect: InvokeAgentEffect, *_args: object, **_kwargs: object) -> PipelineEvent:
        harness.invoked.append(effect.agent_name)
        harness.clock.advance(seconds)
        return PipelineEvent.AGENT_SUCCESS

    return runner


def _run_steps(harness: _MainPhaseHarness, steps: int, *, seconds: float = 0.0) -> None:
    for _step in range(steps):
        token = install_agent_effect_dependencies(
            agent_runner=_exits_cleanly_without_a_plan(harness, seconds=seconds),
            recovery_controller=harness.controller,
            pipeline_deps=harness.deps,
        )
        try:
            harness.state = runner_module.run_pipeline_step(
                state=harness.state,
                policy_bundle=harness.bundle,
                workspace_scope=harness.scope,
                config=harness.config,
                display=harness.display,
                display_context=harness.display_context,
                verbosity=Verbosity.QUIET,
                registry=harness.registry,
                pipeline_subscriber=None,
                recovery_controller=harness.controller,
                pipeline_deps=harness.deps,
            )
        finally:
            reset_agent_effect_dependencies(token)


def test_three_clean_exits_without_the_plan_start_an_unproductive_cooldown_and_rotate(
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(_PHASE, tmp_git_repo)
    limit = AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT

    _run_steps(harness, limit - 1)
    assert isinstance(harness.controller.agent_eligibility(_PHASE, harness.primary), Eligible)

    _run_steps(harness, 1)
    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    assert harness.invoked == [harness.primary] * limit

    _run_steps(harness, 1)
    assert harness.invoked[-1] != harness.primary


def test_an_unproductive_cooldown_outlasts_the_failing_attempt(tmp_git_repo: Path) -> None:
    harness = _MainPhaseHarness(_PHASE, tmp_git_repo)

    _run_steps(harness, AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT, seconds=_SLOW_ATTEMPT_SECONDS)

    wait_ms = harness.controller.earliest_eligible_wait_ms(_PHASE, [harness.primary])
    assert wait_ms is not None
    assert wait_ms >= int(_SLOW_ATTEMPT_SECONDS * 1000)
