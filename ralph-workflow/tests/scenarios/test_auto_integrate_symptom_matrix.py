from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.workspace.context import WorkspaceContextError, workspace_context
from tests._scenario_harness import (
    SCENARIO_FILES,
    ScenarioResult,
    capture_operator_lines,
    corrupted_config,
    run_completed_rebase_promotion,
    run_marker_resolution,
    run_spent_gate,
    run_unstaged_text_resolution,
    supervisor_restart,
)
from tests.scenarios._scenario_runner import ScenarioRunner

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]

__all__ = ["Expected", "ScenarioRunner"]


@dataclass(frozen=True)
class Expected:
    launches: int
    line_count: int
    level: str | None
    content: str | None
    terminal: str


def _corrupted_config(root: Path) -> ScenarioResult:
    config_path = corrupted_config(root)
    with (
        capture_operator_lines() as lines,
        pytest.raises(WorkspaceContextError),
        workspace_context(root),
    ):
        pass
    return ScenarioResult(0, tuple(lines), "config_failure", {"config": str(config_path)})


def _completed_rebase(root: Path) -> ScenarioResult:
    return run_completed_rebase_promotion(root)


def _spent_chain(root: Path) -> ScenarioResult:
    return run_spent_gate(root, FakeClock())


def _unchanged_then_tick(root: Path) -> ScenarioResult:
    return run_spent_gate(root, FakeClock(), advance=1.0)


def _moved_target_after_restart(root: Path) -> ScenarioResult:
    result = run_completed_rebase_promotion(root)
    restarted = supervisor_restart(root)
    terminal = "anchor_proven" if restarted["head"] == result.facts["target"] else "anchor_lost"
    return ScenarioResult(
        result.resolver_launches,
        result.operator_lines,
        terminal,
        {**result.facts, "restart_head": str(restarted["head"])},
    )


def _unstaged_and_gitlink(root: Path) -> ScenarioResult:
    return run_unstaged_text_resolution(root)


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
        _completed_rebase,
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
    tmp_git_repo: Path,
) -> None:
    del symptom
    assert SCENARIO_FILES == ("tests/scenarios/test_auto_integrate_symptom_matrix.py",)
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
