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
    run_declared_decisions_and_gitlink_resolution,
    run_detached_head_probe_quietness,
    run_empty_resolution_stop,
    run_gate_quiescence_ticks,
    run_marker_resolution,
    run_marker_resolution_bounded,
    run_multi_commit_empty_stop_recovery,
    run_sixth_symptom_landing,
    run_spent_chain_fall_over_then_wait,
    run_spent_gate,
    run_unstaged_text_resolution,
)

if TYPE_CHECKING:
    from tests.scenarios._scenario_runner import ScenarioRunner

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
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


def _spent_chain_fall_over_then_wait(root: Path) -> ScenarioResult:
    return run_spent_chain_fall_over_then_wait(root)


def _unchanged_then_tick(root: Path) -> ScenarioResult:
    return run_spent_gate(root, FakeClock(), advance=1.0)


def _multi_commit_empty_stop(root: Path) -> ScenarioResult:
    return run_multi_commit_empty_stop_recovery(root)


def _unstaged_and_gitlink(root: Path) -> ScenarioResult:
    return run_unstaged_text_resolution(root)


def _empty_resolution_stop(root: Path) -> ScenarioResult:
    return run_empty_resolution_stop(root)


def _detached_head_probe_quietness(root: Path) -> ScenarioResult:
    return run_detached_head_probe_quietness(root)


def _marker_leaving_resolver_bounded(root: Path) -> ScenarioResult:
    return run_marker_resolution_bounded(root)


CASES: tuple[tuple[str, ScenarioRunner, Expected], ...] = (
    (
        "corrupted-config-on-target-worktree",
        _corrupted_config,
        Expected(1, 1, "WARNING", "fix", "resolved"),
    ),
    (
        "spent-resolver-chain-ledger",
        _spent_chain,
        Expected(1, 1, "WARNING", "provider", "waiting"),
    ),
    (
        "spent-chain-fall-over-then-wait",
        _spent_chain_fall_over_then_wait,
        Expected(0, 1, "INFO", "waiting", "waiting"),
    ),
    (
        "completed-rebase-is-promoted",
        _completed_rebase,
        Expected(1, 0, None, None, "promoted"),
    ),
    (
        "unchanged-gate-remains-closed-after-one-second",
        _unchanged_then_tick,
        Expected(1, 1, "WARNING", "provider", "waiting"),
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


def test_sixth_symptom_lands_records_policy_and_stays_quiet(tmp_git_repo: Path) -> None:
    result = run_sixth_symptom_landing(tmp_git_repo)

    assert result.terminal_state == "landed"
    assert result.resolver_launches == 1
    stages = result.facts["stage_shas"].split(",")
    assert len(stages) == 3 and all(len(sha) == 40 for sha in stages)
    policy = result.facts["policy_lines"]
    assert all(f"{label}={sha}" in policy for label, sha in zip(("base", "ours", "theirs"), stages, strict=True))
    assert "policy=vendor-immutable" in policy
    assert "alternatives=('unchanged-from-base', 'ours', 'theirs')" in policy
    assert result.facts["integrated_feature_sha"] == result.facts["completed_head"]
    assert result.facts["head"] == result.facts["target"]
    assert result.facts["launches_after_landing"] == "0"
    assert result.facts["lines_after_landing"] == "0"
    assert result.facts["post_landing_selections"] == "0"
    assert result.facts["repeated_pairs"] == ""


def test_gate_active_identity_is_quiet_for_ten_ticks(tmp_git_repo: Path) -> None:
    result = run_gate_quiescence_ticks(tmp_git_repo)

    assert result.terminal_state == "quiescent"
    assert result.resolver_launches == 0
    assert result.facts["gate_count"] == "1"
    assert result.facts["waiting_count"] == "1"
    waiting = result.facts["waiting_lines"]
    assert "conflict identity, eligible agents, and configuration unchanged" in waiting
    assert waiting.endswith("fix configuration and run 'ralph --check-config'.")


def test_mixed_declared_decisions_and_gitlink_are_separated_then_land(
    tmp_git_repo: Path,
) -> None:
    result = run_declared_decisions_and_gitlink_resolution(tmp_git_repo)

    assert result.terminal_state == "landed"
    content = "\n".join(line.content for line in result.operator_lines)
    assert "2 resolvable" in content
    assert "1 needs operator action: vendor/link" in content
    assert "vendor/link: gitlink pointer policy: conflict on vendor/link" in content
    assert "deleted.txt: delete/modify declaration" in content
    assert "docs/decision.md: marker-less declaration" in content
    assert result.facts["prompted_paths"] == "deleted.txt,docs/decision.md"
    assert result.facts["driver_invocations"] == "1"
    assert result.facts["driver_resolved"] == "false"
    assert "conflicts resolved in round" not in content
    stages = result.facts["stage_shas"].split(",")
    policy = result.facts["policy_lines"]
    assert all(f"{label}={sha}" in policy for label, sha in zip(("base", "ours", "theirs"), stages, strict=True))
    assert result.facts["head"] == result.facts["target"]
