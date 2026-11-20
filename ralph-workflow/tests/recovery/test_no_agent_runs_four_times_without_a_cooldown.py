from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from loguru import logger
from watchdog.events import FileClosedNoWriteEvent, FileOpenedEvent

from ralph.agents.invoke import AgentInvocationError, OpenCodeResumableExitError, WorkspaceMonitor
from ralph.agents.registry import AgentRegistry
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline.events import AgentFailureEvent
from ralph.workspace.awareness import release_workspace_awareness
from ralph.workspace.fs import FsWorkspace
from tests.recovery.test_cooldown_real_invocation_main_phases import (
    _MainPhaseHarness,
    _production_phases,
)
from tests.recovery.test_development_analysis_consecutive_limit import _run_step

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "opencode_transcripts"
    / "minimax_token_plan_limit.jsonl"
)
_MAX_STREAK_WITHOUT_COOLDOWN = 3
_INVOCATIONS = 5
_STEP_CEILING = 8
_READ_BY_THE_AGENT = ("README.md",)


def _silent_exit(_agent: str, _observe: Callable[[], bool]) -> Iterator[str]:
    return iter(())


def _quota_frames_then_clean_exit(_agent: str, _observe: Callable[[], bool]) -> Iterator[str]:
    return iter(_FIXTURE.read_text(encoding="utf-8").splitlines())


def _resumable_exit(agent: str, observe: Callable[[], bool]) -> Iterator[str]:
    observe()
    raise OpenCodeResumableExitError(agent, returncode=0)


def _error_exit(agent: str, observe: Callable[[], bool]) -> Iterator[str]:
    observe()
    raise AgentInvocationError(agent, 1, "")


_SHAPES: dict[str, Callable[[str, Callable[[], bool]], Iterator[str]]] = {
    "silent_clean_exit": _silent_exit,
    "token_plan_frames_then_clean_exit": _quota_frames_then_clean_exit,
    "resumable_exit": _resumable_exit,
    "error_exit": _error_exit,
}


def _harness(
    phase: str,
    root: Path,
    shape: Callable[[str, Callable[[], bool]], Iterator[str]],
) -> tuple[_MainPhaseHarness, list[str]]:
    harness = _MainPhaseHarness(phase, root)
    invoked: list[str] = []

    def invoke_agent(
        agent_config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
        **_kwargs: object,
    ) -> Iterator[str]:
        del agent_config, prompt_file
        agent = str(getattr(options, "agent_name", ""))
        probe = getattr(options, "workspace_probe", None)
        sink = getattr(options, "failure_observation_sink", None)
        assert probe is not None
        invoked.append(agent)
        workspace = FsWorkspace(root)
        monitor = WorkspaceMonitor(root)
        monitor.start()

        def observe() -> bool:
            changed = bool(probe.has_changed(phase, workspace))
            if callable(sink):
                sink(
                    AgentFailureEvent(
                        phase=phase,
                        agent=agent,
                        artifact_valid=False,
                        workspace_changed=changed,
                    )
                )
            return changed

        try:
            probe.observe_before(phase, workspace)
            for name in _READ_BY_THE_AGENT:
                read_path = str(root / name)
                monitor.dispatch_event(FileOpenedEvent(read_path))
                monitor.dispatch_event(FileClosedNoWriteEvent(read_path))
            return shape(agent, observe)
        finally:
            monitor.stop()
            release_workspace_awareness(root)

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
    harness.deps = replace(
        harness.deps,
        invoke_agent=invoke_agent,
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )
    return harness, invoked


def _cooldowns_started_by_each_invocation(
    harness: _MainPhaseHarness, invoked: list[str]
) -> list[tuple[str, bool]]:
    timeline: list[tuple[str, bool]] = []
    cooled: set[str] = set()

    def sink(message: object) -> None:
        text = str(message)
        if "cooldown_decision" not in text:
            return
        cooled.update(agent for agent in harness.agents if f"agent={agent} " in text)

    sink_id = logger.add(sink, level="WARNING", format="{message}")
    try:
        for _step in range(_STEP_CEILING):
            if len(invoked) >= _INVOCATIONS:
                break
            before = len(invoked)
            cooled.clear()
            _run_step(harness)
            step_agents = invoked[before:]
            for index, agent in enumerate(step_agents):
                last_of_agent = agent not in step_agents[index + 1 :]
                timeline.append((agent, last_of_agent and agent in cooled))
    finally:
        logger.remove(sink_id)
    return timeline


@pytest.mark.timeout_seconds(3)
@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize("phase", _production_phases())
def test_the_same_agent_is_never_invoked_four_times_in_a_row_without_a_cooldown(
    phase: str, shape: str, tmp_git_repo: Path
) -> None:
    harness, invoked = _harness(phase, Path(tmp_git_repo), _SHAPES[shape])

    timeline = _cooldowns_started_by_each_invocation(harness, invoked)

    assert any(started for _agent, started in timeline), (phase, shape, timeline)
    streak_agent: str | None = None
    streak = 0
    for agent, started_cooldown in timeline:
        if agent != streak_agent:
            streak_agent, streak = agent, 0
        streak += 1
        assert streak <= _MAX_STREAK_WITHOUT_COOLDOWN, (phase, shape, timeline)
        if started_cooldown:
            streak_agent, streak = None, 0
