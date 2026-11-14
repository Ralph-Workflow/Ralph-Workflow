from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from tests.scenarios._rebase_recovery_harness import (
    SCENARIO_FILES,
    ScenarioResult,
    run_completed_rebase_promotion,
    run_corrupted_config,
    run_marker_resolution,
    run_multi_commit_empty_stop_recovery,
    run_spent_gate,
    run_unstaged_text_resolution,
)

if TYPE_CHECKING:
    from tests.scenarios._scenario_runner import ScenarioRunner

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(10),
]

__all__ = ["CASES", "Expected"]


@dataclass(frozen=True)
class Expected:
    launches: int
    line_count: int
    level: str | None
    content: str | None
    terminal: str


def _corrupted_config(root: Path) -> ScenarioResult:
    return run_corrupted_config(root)


def _completed_rebase(root: Path) -> ScenarioResult:
    return run_completed_rebase_promotion(root)


def _spent_chain(root: Path) -> ScenarioResult:
    return run_spent_gate(root, FakeClock())


def _unchanged_then_tick(root: Path) -> ScenarioResult:
    return run_spent_gate(root, FakeClock(), advance=1.0)


def _multi_commit_empty_stop(root: Path) -> ScenarioResult:
    return run_multi_commit_empty_stop_recovery(root)


def _unstaged_and_gitlink(root: Path) -> ScenarioResult:
    return run_unstaged_text_resolution(root)


CASES: tuple[tuple[str, ScenarioRunner, Expected], ...] = (
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
        _multi_commit_empty_stop,
        Expected(1, 0, None, None, "anchor_proven"),
    ),
    (
        "resolved-unstaged-text-plus-gitlink",
        _unstaged_and_gitlink,
        Expected(1, 0, None, None, "resolved"),
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
    assert SCENARIO_FILES == ("tests/scenarios/test_rebase_recovery_matrix.py",)
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
