from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult
from tests.scenarios._paused_rebase_fixtures import (
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._rebase_recovery_harness_helpers import (
    FakeAgentRunner,
    capture_operator_lines,
    corrupted_config,
    scenario_config,
    shared_executor_seam,
    supervisor_restart,
)

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import FakeClock


pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "SCENARIO_FILES",
    "AgentMode",
    "FakeAgentRunner",
    "OperatorLine",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "ScenarioDisplay",
    "ScenarioResult",
    "StopResolutionOutcome",
    "capture_operator_lines",
    "commit_file",
    "corrupted_config",
    "git",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "run_completed_rebase_promotion",
    "run_marker_resolution",
    "run_spent_gate",
    "run_unstaged_text_resolution",
    "scenario_config",
    "supervisor_restart",
]

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_auto_integrate_symptom_matrix.py",)


def spent_ledger_state(target: str = "main") -> tuple[RebaseState, ConflictIdentity]:
    identity = ConflictIdentity(
        feature_sha="feature",
        target_sha="target",
        conflicted_paths=("shared.txt",),
        stage_oids=("one", "two"),
    )
    state = RebaseState(
        last_target=target,
        resolution_exhausted=True,
        last_conflict_feature_sha=identity.feature_sha,
        last_conflict_target_sha=identity.target_sha,
        last_conflict_paths=identity.conflicted_paths,
        last_conflict_stage_oids=identity.stage_oids,
    )
    return state, identity


def run_completed_rebase_promotion(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        state = integrate_before_planning(
            scenario_config(),
            WorkspaceScope(root),
            RebaseState(),
            conflict_resolver=lambda _root, _target: False,
            rebase_stop_resolver=runner,
        )
    head = git(root, "rev-parse", "HEAD")
    target = git(root, "rev-parse", "main")
    terminal = (
        "promoted" if head == target and state is not None and state.fast_forwarded else "orphaned"
    )
    return ScenarioResult(runner.launches, tuple(lines), terminal, {"head": head, "target": target})


def _resolution_succeeded(outcome: object) -> bool:
    resolved = outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
    return isinstance(resolved, ResolvedOutcome)


def run_unstaged_text_resolution(root: Path) -> ScenarioResult:
    paused_text_and_gitlink_rebase(root)
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-unstaged-gitlink",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = _resolution_succeeded(outcome)
    terminal = "resolved" if succeeded and not rebase_in_progress(root) else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_marker_resolution(root: Path) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(runner), capture_operator_lines() as lines:
        outcome = attempt_stop_resolution(
            "scenario-marker",
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target="main",
                resolver=runner,
                conflict_config=scenario_config().conflict_resolution,
                force_retry=True,
            ),
        )
        succeeded = _resolution_succeeded(outcome)
    terminal = "resolved" if succeeded else "retained"
    return ScenarioResult(runner.launches, tuple(lines), terminal)


def run_spent_gate(root: Path, clock: FakeClock, *, advance: float = 0.0) -> ScenarioResult:
    paused_text_rebase(root)
    runner = FakeAgentRunner(AgentMode.PROVIDER_FAILURE, clock=clock)
    if advance:
        clock.advance(advance)

    class _IntegrationClock:
        def now(self) -> float:
            return clock.monotonic()

        def monotonic(self) -> float:
            return clock.monotonic()

        def epoch(self) -> float:
            return clock.monotonic()

        def sleep(self, seconds: float) -> None:
            clock.sleep(seconds)

    token = install_executors(agent_runner=runner, clock=_IntegrationClock())
    try:
        with shared_executor_seam(runner), capture_operator_lines() as lines:
            state = integrate_before_planning(
                scenario_config(),
                WorkspaceScope(root),
                RebaseState(),
                conflict_resolver=lambda _root, _target: False,
                rebase_stop_resolver=runner,
            )
    finally:
        reset_executors(token)
    terminal = "launched" if runner.launches else "waiting"
    facts = {
        "launch_times": ",".join(str(item) for item in runner.launch_times),
        "next_launch_time": str(getattr(state, "retry_not_before", "")),
    }
    return ScenarioResult(runner.launches, tuple(lines), terminal, facts)
