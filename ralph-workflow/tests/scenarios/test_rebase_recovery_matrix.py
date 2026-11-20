from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.merge import unmerged_paths
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline import attempt_stop_resolution
from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
from tests._scenario_harness import (
    SCENARIO_FILES,
    ScenarioResult,
    ScenarioRunner,
    commit_file,
    git,
    run_declared_decisions_and_gitlink_resolution,
    run_detached_head_probe_quietness,
    run_empty_resolution_stop,
    run_marker_resolution,
    run_marker_resolution_bounded,
    run_spent_chain_fall_over_then_wait,
    scenario_config,
    shared_executor_seam,
)
from tests.scenarios._counting_resolver import CountingResolver

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

__all__ = ["CASES", "Expected"]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_rebase_recovery_matrix": (
        "2f980b011 stages policy gitlinks before the resolution chain runs, so vendor/link "
        "is picked per policy instead of escalating as out of reach; the driver announces that "
        "staging even when other conflicts remain, then resolves the stop with five operator "
        "lines, the landing pass launches no further resolver, and the rebase still lands "
        "without content loss"
    ),
}


@dataclass(frozen=True)
class Expected:
    launches: int
    line_count: int
    level: str | None
    content: str | None
    terminal: str


def _spent_chain_fall_over_then_wait(root: Path) -> ScenarioResult:
    return run_spent_chain_fall_over_then_wait(root)


def _empty_resolution_stop(root: Path) -> ScenarioResult:
    return run_empty_resolution_stop(root)


def _detached_head_probe_quietness(root: Path) -> ScenarioResult:
    return run_detached_head_probe_quietness(root)


def _partial_out_of_reach_and_declared_decision(root: Path) -> ScenarioResult:
    return run_declared_decisions_and_gitlink_resolution(root)


def _marker_leaving_resolver_bounded(root: Path) -> ScenarioResult:
    return run_marker_resolution_bounded(root)


CASES: tuple[tuple[str, ScenarioRunner, Expected], ...] = (
    (
        "spent-chain-fall-over-then-wait",
        _spent_chain_fall_over_then_wait,
        Expected(0, 1, "INFO", "waiting", "waiting"),
    ),
    (
        "empty-resolution-stop",
        _empty_resolution_stop,
        Expected(1, 0, None, None, "resolved"),
    ),
    (
        "detached-head-probe-quietness",
        _detached_head_probe_quietness,
        Expected(0, 0, None, None, "quiet"),
    ),
    (
        "partial-out-of-reach-and-declared-decision",
        _partial_out_of_reach_and_declared_decision,
        Expected(0, 5, "WARNING", "delete/modify declaration", "landed"),
    ),
    (
        "marker-leaving-resolver-bounded",
        _marker_leaving_resolver_bounded,
        Expected(2, 2, "WARNING", "unmerged", "retained"),
    ),
)


@pytest.mark.parametrize(("symptom", "run", "expected"), CASES, ids=[case[0] for case in CASES])
def test_rebase_recovery_matrix(
    symptom: str,
    run: ScenarioRunner,
    expected: Expected,
    tmp_git_repo: Path,
) -> None:
    del symptom
    assert "tests/scenarios/test_rebase_recovery_matrix.py" in SCENARIO_FILES
    result = run(tmp_git_repo)

    assert result.resolver_launches == expected.launches
    assert len(result.operator_lines) == expected.line_count
    if expected.level is not None:
        assert all(line.level == expected.level for line in result.operator_lines)
    if expected.content is not None:
        assert any(expected.content in line.content.lower() for line in result.operator_lines)
    assert result.terminal_state == expected.terminal


def test_leaves_marker_mode_retains_the_rebase(tmp_git_repo: Path) -> None:
    result = run_marker_resolution(tmp_git_repo)

    assert result.resolver_launches == 1
    assert result.terminal_state == "retained"


_THREE_CONFLICTED_FILES: tuple[str, ...] = ("alpha.txt", "beta.txt", "gamma.txt")
_MARKER_FILE = "gamma.txt"


def _paused_three_text_conflicts(root: Path) -> None:
    for relative in _THREE_CONFLICTED_FILES:
        commit_file(root, relative, "base\n", f"base {relative}")
    git(root, "checkout", "-q", "-b", "feature")
    for relative in _THREE_CONFLICTED_FILES:
        (root / relative).write_text("feature\n", encoding="utf-8")
    git(root, "add", "--", *_THREE_CONFLICTED_FILES)
    git(root, "commit", "-q", "-m", "feature change")
    git(root, "checkout", "-q", "main")
    for relative in _THREE_CONFLICTED_FILES:
        (root / relative).write_text("target\n", encoding="utf-8")
    git(root, "add", "--", *_THREE_CONFLICTED_FILES)
    git(root, "commit", "-q", "-m", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("scenario fixture did not pause in a conflicted rebase")


class _MarkerLeavingResolver:
    def __init__(self, marker_path: str) -> None:
        self.marker_path = marker_path

    def __call__(self, root: Path, _target: str, stop: RebaseStop) -> bool:
        for relative in stop.conflicted_files:
            if relative == self.marker_path:
                continue
            path = root / relative
            if path.is_file():
                path.write_text("resolved by scenario agent\n", encoding="utf-8")
        return True


def test_marker_leaving_resolver_is_re_dispatched_for_that_file_only(
    tmp_git_repo: Path,
) -> None:
    _paused_three_text_conflicts(tmp_git_repo)
    resolver = CountingResolver(_MarkerLeavingResolver(_MARKER_FILE))
    conflict_config = scenario_config().conflict_resolution

    with shared_executor_seam(None):
        for _ in range(2):
            attempt_stop_resolution(
                "scenario-marker-scoped",
                StopResolutionTrigger(
                    name="rebase_stop",
                    root=tmp_git_repo,
                    target="main",
                    resolver=resolver,
                    conflict_config=conflict_config,
                    force_retry=True,
                ),
            )

    assert len(resolver.stops) == 2
    assert resolver.stops[0].conflicted_files == _THREE_CONFLICTED_FILES
    assert resolver.stops[0].sha == resolver.stops[1].sha
    assert resolver.stops[1].conflicted_files == (_MARKER_FILE,)
    assert unmerged_paths(tmp_git_repo) == [_MARKER_FILE]
    assert rebase_in_progress(tmp_git_repo)
