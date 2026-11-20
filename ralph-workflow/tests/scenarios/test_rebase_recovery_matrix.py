from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._scenario_harness import (
    SCENARIO_FILES,
    ScenarioResult,
    ScenarioRunner,
    run_declared_decisions_and_gitlink_resolution,
    run_detached_head_probe_quietness,
    run_empty_resolution_stop,
    run_marker_resolution,
    run_marker_resolution_bounded,
    run_spent_chain_fall_over_then_wait,
)

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
