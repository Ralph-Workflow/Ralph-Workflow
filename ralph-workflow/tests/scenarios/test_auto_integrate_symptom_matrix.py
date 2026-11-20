from __future__ import annotations

import functools
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from git import Repo

from ralph.agents.timeout_clock import FakeClock
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope
from tests._scenario_harness import (
    SCENARIO_FILES,
    AgentMode,
    FakeAgentRunner,
    ScenarioResult,
    ScenarioRunner,
    capture_operator_lines,
    corrupted_config,
    git,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
    scenario_config,
    shared_executor_seam,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "in_process_supervisor_restart": (
        "moved-target anchor proof probes integrate_before_planning in-process instead of "
        "a cold python -I subprocess; anchor_proven vs restart head asserts unchanged"
    ),
    "paused_text_rebase_template_reuse": (
        "completed-rebase, spent-gate, and marker shapes hardlink-copytree a once-built "
        "paused rebase instead of reseeding via commit_file each case"
    ),
    "paused_text_gitlink_template_reuse": (
        "unstaged-text-plus-gitlink hardlink-copytrees a once-built mixed rebase "
        "instead of reseeding via paused_text_and_gitlink_rebase each case"
    ),
}

__all__ = ["Expected", "ScenarioRunner"]


@dataclass(frozen=True)
class Expected:
    launches: int
    line_count: int
    level: str | None
    content: str | None
    terminal: str


def _link_git_objects_or_copy(src: str, dst: str) -> None:
    src_path = Path(src)
    parts = src_path.parts
    if ".git" in parts:
        git_index = parts.index(".git")
        rest = parts[git_index + 1 :]
        if rest and rest[0] == "objects" and len(rest) >= 2:
            try:
                os.link(src, dst)
            except OSError:
                pass
            else:
                return
    shutil.copy2(src, dst)


def _seed_identity_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    readme.write_text("seed\n", encoding="utf-8")
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgsign", "false")
    repo.index.add(["README.md"])
    repo.index.commit("seed")
    repo.close()


@functools.lru_cache(maxsize=1)
def _paused_text_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="symptom-paused-text-"))
    _seed_identity_repo(base)
    paused_text_rebase(base)
    return base


@functools.lru_cache(maxsize=1)
def _paused_gitlink_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="symptom-paused-gitlink-"))
    _seed_identity_repo(base)
    paused_text_and_gitlink_rebase(base)
    return base


def _materialize_template(template: Path, root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        template,
        root,
        copy_function=_link_git_objects_or_copy,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )


def _materialize_paused_text(root: Path) -> None:
    _materialize_template(_paused_text_template(), root)


def _materialize_paused_gitlink(root: Path) -> None:
    _materialize_template(_paused_gitlink_template(), root)


def _corrupted_config(root: Path) -> ScenarioResult:
    config_path = corrupted_config(root)
    with (
        capture_operator_lines() as lines,
        pytest.raises(WorkspaceContextError),
        workspace_context(root),
    ):
        pass
    return ScenarioResult(0, tuple(lines), "config_failure", {"config": str(config_path)})


def _promote_paused(root: Path) -> ScenarioResult:
    _materialize_paused_text(root)
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    with shared_executor_seam(None), capture_operator_lines() as lines:
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


def _run_spent_on_paused(root: Path, clock: FakeClock, *, advance: float = 0.0) -> ScenarioResult:
    _materialize_paused_text(root)
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
        with shared_executor_seam(None), capture_operator_lines() as lines:
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


def _spent_chain(root: Path) -> ScenarioResult:
    return _run_spent_on_paused(root, FakeClock())


def _unchanged_then_tick(root: Path) -> ScenarioResult:
    return _run_spent_on_paused(root, FakeClock(), advance=1.0)


def _in_process_supervisor_restart(root: Path) -> dict[str, object]:
    state = integrate_before_planning(
        scenario_config(),
        WorkspaceScope(root),
        RebaseState(),
    )
    repo = Repo(root)
    try:
        head = repo.head.commit.hexsha
        status = repo.git.status("--porcelain")
    finally:
        repo.close()
    return {"head": head, "status": status, "resumed": state is not None}


def _moved_target_after_restart(root: Path) -> ScenarioResult:
    result = _promote_paused(root)
    restarted = _in_process_supervisor_restart(root)
    terminal = "anchor_proven" if restarted["head"] == result.facts["target"] else "anchor_lost"
    return ScenarioResult(
        result.resolver_launches,
        result.operator_lines,
        terminal,
        {**result.facts, "restart_head": str(restarted["head"])},
    )


def _resolution_succeeded(outcome: object) -> bool:
    resolved = outcome.outcome if isinstance(outcome, RebaseResolutionResult) else outcome
    return isinstance(resolved, ResolvedOutcome)


def _unstaged_and_gitlink(root: Path) -> ScenarioResult:
    _materialize_paused_gitlink(root)
    runner = FakeAgentRunner(AgentMode.EDITS_NEVER_STAGES)
    with shared_executor_seam(None), capture_operator_lines() as lines:
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


def _marker_on_paused(root: Path) -> ScenarioResult:
    _materialize_paused_text(root)
    runner = FakeAgentRunner(AgentMode.LEAVES_MARKER)
    with shared_executor_seam(None), capture_operator_lines() as lines:
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


CASES = (
    (
        "corrupted-config-on-target-worktree",
        _corrupted_config,
        Expected(0, 1, "WARNING", "fix", "config_failure"),
    ),
    (
        "spent-resolver-chain-ledger",
        _spent_chain,
        Expected(1, 1, "WARNING", "provider", "launched"),
    ),
    (
        "completed-rebase-is-promoted",
        _promote_paused,
        Expected(1, 2, "WARNING", "reclaim", "promoted"),
    ),
    (
        "unchanged-gate-remains-closed-after-one-second",
        _unchanged_then_tick,
        Expected(1, 1, "WARNING", "provider", "launched"),
    ),
    (
        "multi-commit-empty-stop-moved-target-anchor-proof",
        _moved_target_after_restart,
        Expected(1, 2, "WARNING", "reclaim", "anchor_proven"),
    ),
    (
        "resolved-unstaged-text-plus-gitlink",
        _unstaged_and_gitlink,
        Expected(1, 0, None, None, "resolved"),
    ),
)


@pytest.mark.parametrize(("symptom", "run", "expected"), CASES, ids=[case[0] for case in CASES])
def test_auto_integrate_symptom_matrix(
    symptom: str,
    run: ScenarioRunner,
    expected: Expected,
    tmp_path: Path,
) -> None:
    del symptom
    assert "tests/scenarios/test_auto_integrate_symptom_matrix.py" in SCENARIO_FILES
    root = tmp_path / "repo"
    if run is _corrupted_config:
        _seed_identity_repo(root)
    result = run(root)

    assert result.resolver_launches == expected.launches
    assert len(result.operator_lines) == expected.line_count
    if expected.level is not None:
        assert all(line.level == expected.level for line in result.operator_lines)
    if expected.content is not None:
        assert any(expected.content in line.content.lower() for line in result.operator_lines)
    assert result.terminal_state == expected.terminal


def test_leaves_marker_mode_retains_the_rebase(tmp_path: Path) -> None:
    result = _marker_on_paused(tmp_path / "repo")
    assert result.resolver_launches == 1
    assert result.terminal_state == "retained"
