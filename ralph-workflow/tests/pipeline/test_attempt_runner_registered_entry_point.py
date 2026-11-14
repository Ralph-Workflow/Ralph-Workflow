from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

import pytest

from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.agent_entry_point import AgentEntryPoint, registered_entry_points
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from ralph.recovery.canary_probe import ProbeOutcome

import_module("ralph.pipeline.runner")


@dataclass(frozen=True)
class _UnregisteredEntryPoint:
    value: str = "unregistered_test_route"


def _config() -> tuple[UnifiedConfig, AgentConfig]:
    agent = AgentConfig(cmd="fake-agent", json_parser="generic", transport="generic")
    return UnifiedConfig(agents={"fake": agent}), agent


def _no_probe(_phase: str, _agent: str) -> ProbeOutcome | None:
    return None


def _invoke(
    target: object,
    *args: object,
    **kwargs: object,
) -> object:
    del target, args, kwargs
    return PipelineEvent.AGENT_SUCCESS


def _deps() -> AttemptRunnerDeps:
    return AttemptRunnerDeps(
        SystemClock(),
        _invoke,
        _no_probe,
        None,
        None,
    )


def test_registered_entry_point_regression_rejects_unregistered_route() -> None:
    config, agent = _config()
    deps = _deps()
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=config,
        policy_bundle=None,
        deps=deps,
    )
    unregistered = _UnregisteredEntryPoint()

    with pytest.raises(
        ValueError,
        match="unregistered agent entry point: unregistered_test_route",
    ):
        run_agent_attempt(
            unregistered,
            invocation=agent,
            config=config,
            controller=controller,
            deps=deps,
            phase="test",
            agent="fake",
        )


def test_registered_entry_point_regression_accepts_every_registered_route() -> None:
    entry_points = registered_entry_points()
    assert entry_points

    for entry_point in entry_points:
        config, agent = _config()
        deps = _deps()
        controller = build_entry_point_recovery_controller(
            entry_point,
            config=config,
            policy_bundle=None,
            deps=deps,
        )

        result = run_agent_attempt(
            entry_point,
            invocation=agent,
            config=config,
            controller=controller,
            deps=deps,
            phase="test",
            agent="fake",
        )

        assert result.event is PipelineEvent.AGENT_SUCCESS
