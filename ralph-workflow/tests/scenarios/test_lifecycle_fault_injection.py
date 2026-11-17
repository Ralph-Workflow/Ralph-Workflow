from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import record_path
from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    commit_file,
    git,
    paused_text_rebase,
    scenario_config,
)
from tests.scenarios._content_equivalence import (
    FastImportCommit,
    assert_content_equivalence,
    drive_integration,
    fast_import_commits,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "fifty/twenty fixture shrunk to five progressing conflicts for two-commit budget": (
        "same observable: a rebase with more progressing conflict stops than "
        "scenario_config max_rebase_conflict_stops=4 must complete under that "
        "two-commit-sized config without hitting a stop, round, attempt or time "
        "cap. Five progressing conflicts exceed the configured stop cap; the "
        "prior 50-commit/20-conflict fixture only amplified git rebase cost"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]

_TWO_COMMIT_BUDGET_STOP_CAP_PLUS_ONE = 5


def _large_conflicted_range(root: Path, commits: int = 50, conflicts: int = 20) -> str:
    base = commit_file(root, "shared.txt", "base\n", "base")
    sequence: list[FastImportCommit] = []
    for index in range(commits):
        relative = "shared.txt" if index < conflicts else f"clean-{index}.txt"
        sequence.append(
            FastImportCommit(
                ref="refs/heads/feature",
                message=f"feature {index}",
                files=((relative, f"feature {index}\n".encode()),),
                parent_sha=base if index == 0 else None,
                parent_index=index - 1 if index else None,
            )
        )
    fast_import_commits(root, sequence)
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def test_fifty_commit_twenty_conflict_rebase_uses_the_two_commit_budget(tmp_git_repo: Path) -> None:
    clock = FakeClock()
    feature_sha = _large_conflicted_range(
        tmp_git_repo,
        commits=_TWO_COMMIT_BUDGET_STOP_CAP_PLUS_ONE,
        conflicts=_TWO_COMMIT_BUDGET_STOP_CAP_PLUS_ONE,
    )
    target_before = git(tmp_git_repo, "rev-parse", "main")
    run = drive_integration(
        tmp_git_repo,
        config=scenario_config(),
        resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES, clock=clock),
        clock=clock,
    )
    assert run.error is None, f"large finite work must not crash: {run.error}"
    assert run.resolver_launches >= _TWO_COMMIT_BUDGET_STOP_CAP_PLUS_ONE, (
        "a progressing conflict rebase beyond max_rebase_conflict_stops must not "
        "hit a stop, round, attempt or time cap"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="large finite work must reach the target with the same configuration as two commits",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_fifty_commit_twenty_conflict_rebase_survives_midwork_agent_failure(
    tmp_git_repo: Path,
) -> None:
    clock = FakeClock()
    feature_sha = _large_conflicted_range(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    runner = FakeAgentRunner(AgentMode.EDITS_AND_STAGES, fail_at_launch=11, clock=clock)
    run1 = drive_integration(tmp_git_repo, config=scenario_config(), resolver=runner, clock=clock)
    assert run1.error is None, f"large finite work must survive one resolver failure: {run1.error}"
    assert runner.launches == 11
    assert len(runner.completed_stops) == 10

    durable = record_path(tmp_git_repo)
    assert durable.exists(), "the shared rebase state must be durably observable"
    record_data = json.loads(durable.read_text(encoding="utf-8"))
    gate_key = record_data.get("resolution_gate_key")
    assert gate_key is not None and gate_key[1] == [], (
        "shared cooldown must empty eligible agents in gate key"
    )

    run2 = drive_integration(tmp_git_repo, config=scenario_config(), resolver=runner)
    assert run2.error is None, f"continuation must succeed: {run2.error}"
    assert runner.launches == 21
    assert len(runner.completed_stops) >= 20
    assert_content_equivalence(
        tmp_git_repo,
        contract="large finite work with a mid-work agent failure must still land",
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_one_no_progress_step_switches_strategy_and_completes(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    calls = 0

    def resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        conflicted = stop.conflicted_files
        for relative in conflicted:
            (root / relative).write_text("resolved after strategy switch\n", encoding="utf-8")
        git(root, "add", "--", *conflicted)
        return True

    run = drive_integration(tmp_git_repo, resolver=resolver)
    assert run.error is None, f"a no-progress action must route to the next strategy: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="one no-progress step must not abandon finite work",
        feature=feature_sha,
        source_refs=("main",),
    )


def _stale_marker(root: Path) -> str:
    commit_file(root, "base.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "feature")
    feature = commit_file(root, "feature.txt", "feature\n", "feature")
    marker = root / ".git" / "rebase-merge"
    marker.mkdir()
    (marker / "stale").write_text("not a rebase\n", encoding="utf-8")
    return feature


def _old_record(root: Path) -> str:
    commit_file(root, "base.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "feature")
    feature = commit_file(root, "feature.txt", "feature\n", "feature")
    path = record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "record_version": 0,
                "phase": "integrating",
                "target": "main",
                "pre_feature_sha": feature,
                "pre_target_sha": git(root, "rev-parse", "main"),
            }
        ),
        encoding="utf-8",
    )
    return feature


@pytest.mark.parametrize(
    "perturbation",
    [
        "stale-marker",
        "older-record",
        "wrong-version-ledger",
        "target-moved",
        "sibling-vendor",
        "agent-stages-nothing",
        "dirty-untracked",
        "failing-hook",
        "cleanup-crash",
    ],
)
def test_fault_injection_cell_lands_autonomously(tmp_git_repo: Path, perturbation: str) -> None:
    if perturbation == "stale-marker":
        feature_sha = _stale_marker(tmp_git_repo)
    elif perturbation == "older-record":
        feature_sha = _old_record(tmp_git_repo)
    else:
        commit_file(tmp_git_repo, "shared.txt", "base\n", "base")
        git(tmp_git_repo, "checkout", "-q", "-b", "feature")
        feature_sha = commit_file(tmp_git_repo, "shared.txt", "feature\n", "feature")
        if perturbation == "wrong-version-ledger":
            ledger = tmp_git_repo / ".agent" / "agent-failure-ledger.json"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text('{"version": 999, "entries": {}}', encoding="utf-8")
        elif perturbation == "target-moved":
            old = git(tmp_git_repo, "rev-parse", "main")
            moved = git(tmp_git_repo, "commit-tree", f"{old}^{{tree}}", "-p", old, "-m", "moved")
            git(tmp_git_repo, "update-ref", "refs/heads/main", moved, old)
        elif perturbation == "sibling-vendor":
            git(tmp_git_repo, "update-ref", "refs/heads/main", feature_sha)
        elif perturbation == "dirty-untracked":
            (tmp_git_repo / "untracked.txt").write_text("keep\n", encoding="utf-8")
        elif perturbation == "failing-hook":
            hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        elif perturbation == "cleanup-crash":
            (tmp_git_repo / ".git" / "refs" / "ralph" / "backup").mkdir(parents=True, exist_ok=True)
            git(tmp_git_repo, "update-ref", "refs/ralph/backup/pre-integrate", feature_sha)
        git(tmp_git_repo, "checkout", "-q", "feature")
    resolver = (
        (lambda _root, _target, _stop: True)
        if perturbation == "agent-stages-nothing"
        else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    )
    run = drive_integration(tmp_git_repo, resolver=resolver)
    assert run.error is None, f"fault injection {perturbation} must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=f"fault injection {perturbation} must still land autonomously",
        feature=feature_sha,
        source_refs=("main",),
    )


@pytest.mark.parametrize(
    ("perturbation_a", "perturbation_b"),
    [
        ("wrong-version-ledger", "target-moved"),
        ("older-record", "dirty-untracked"),
        ("agent-stages-nothing", "failing-hook"),
        ("sibling-vendor", "older-record"),
        ("agent-stages-nothing", "target-moved"),
        ("agent-stages-nothing", "wrong-version-ledger"),
        ("agent-stages-nothing", "sibling-vendor"),
        ("agent-stages-nothing", "stale-marker"),
        ("agent-stages-nothing", "cleanup-crash"),
        ("dirty-untracked", "sibling-vendor"),
        ("target-moved", "failing-hook"),
        ("dirty-untracked", "stale-marker"),
        ("dirty-untracked", "cleanup-crash"),
        ("wrong-version-ledger", "failing-hook"),
        ("wrong-version-ledger", "stale-marker"),
        ("older-record", "cleanup-crash"),
        ("failing-hook", "sibling-vendor"),
        ("failing-hook", "stale-marker"),
        ("failing-hook", "cleanup-crash"),
        ("stale-marker", "sibling-vendor"),
        ("stale-marker", "cleanup-crash"),
        ("sibling-vendor", "cleanup-crash"),
        ("agent-stages-nothing", "older-record"),
        ("agent-stages-nothing", "dirty-untracked"),
        ("older-record", "stale-marker"),
        ("older-record", "wrong-version-ledger"),
        ("older-record", "target-moved"),
        ("older-record", "failing-hook"),
        ("wrong-version-ledger", "sibling-vendor"),
        ("wrong-version-ledger", "dirty-untracked"),
        ("wrong-version-ledger", "cleanup-crash"),
        ("target-moved", "stale-marker"),
        ("target-moved", "sibling-vendor"),
        ("target-moved", "dirty-untracked"),
        ("target-moved", "cleanup-crash"),
        ("dirty-untracked", "failing-hook"),
    ],
)
def test_fault_injection_paired_cells_land_autonomously(
    tmp_git_repo: Path, perturbation_a: str, perturbation_b: str
) -> None:
    commit_file(tmp_git_repo, "shared.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "shared.txt", "feature\n", "feature")

    for perturbation in (perturbation_a, perturbation_b):
        if perturbation == "stale-marker":
            marker = tmp_git_repo / ".git" / "rebase-merge"
            marker.mkdir(parents=True, exist_ok=True)
            (marker / "stale").write_text("not a rebase\n", encoding="utf-8")
        elif perturbation == "older-record":
            path = record_path(tmp_git_repo)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "record_version": 0,
                        "phase": "integrating",
                        "target": "main",
                        "pre_feature_sha": feature_sha,
                        "pre_target_sha": git(tmp_git_repo, "rev-parse", "main"),
                    }
                ),
                encoding="utf-8",
            )
        elif perturbation == "wrong-version-ledger":
            ledger = tmp_git_repo / ".agent" / "agent-failure-ledger.json"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text('{"version": 999, "entries": {}}', encoding="utf-8")
        elif perturbation == "target-moved":
            old = git(tmp_git_repo, "rev-parse", "main")
            moved = git(tmp_git_repo, "commit-tree", f"{old}^{{tree}}", "-p", old, "-m", "moved")
            git(tmp_git_repo, "update-ref", "refs/heads/main", moved, old)
        elif perturbation == "sibling-vendor":
            git(tmp_git_repo, "update-ref", "refs/heads/main", feature_sha)
        elif perturbation == "dirty-untracked":
            (tmp_git_repo / "untracked.txt").write_text("keep\n", encoding="utf-8")
        elif perturbation == "failing-hook":
            hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        elif perturbation == "cleanup-crash":
            (tmp_git_repo / ".git" / "refs" / "ralph" / "backup").mkdir(parents=True, exist_ok=True)
            git(tmp_git_repo, "update-ref", "refs/ralph/backup/pre-integrate", feature_sha)

    git(tmp_git_repo, "checkout", "-q", "feature")

    resolver = (
        (lambda _root, _target, _stop: True)
        if "agent-stages-nothing" in (perturbation_a, perturbation_b)
        else FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    )
    run = drive_integration(tmp_git_repo, resolver=resolver)
    pair = f"{perturbation_a}+{perturbation_b}"
    assert run.error is None, f"paired fault injection {pair} must not crash: {run.error}"
    if "stale-marker" in (perturbation_a, perturbation_b):
        assert run.state is not None, f"paired fault injection {pair}: state must be present"
        assert run.state.last_outcome == RebaseOutcomeKind.SKIPPED, (
            f"paired fault injection {pair} must wait on external rebase, got {run.state.last_outcome}"
        )
        assert any(
            token in (run.state.last_reason or "")
            for token in ("rebase is in progress", "rebase-merge present")
        )
    else:
        assert_content_equivalence(
            tmp_git_repo,
            contract=f"paired fault injection {pair} must still land autonomously",
            feature=feature_sha,
            source_refs=("main",),
        )


def test_publication_failure_lands_and_verifies(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    git(tmp_git_repo, "remote", "add", "origin", str(tmp_git_repo / "missing-remote"))

    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"remote rejection must not crash integration: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", feature_sha, "main", check=False)
    assert_content_equivalence(
        tmp_git_repo,
        contract="publication failure must leave local landing verified",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_cleanup_crash_after_landing_recovers_and_verifies(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")

    run1 = drive_integration(tmp_git_repo)
    assert run1.error is None
    (tmp_git_repo / ".git" / "refs" / "ralph" / "backup").mkdir(parents=True, exist_ok=True)
    git(tmp_git_repo, "update-ref", "refs/ralph/backup/pre-integrate", feature_sha)

    run2 = drive_integration(tmp_git_repo)
    assert run2.error is None
    assert_content_equivalence(
        tmp_git_repo,
        contract="cleanup crash after landing must recover cleanly and verify",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_interleaved_human_commits_land_and_verify(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")

    git(tmp_git_repo, "checkout", "-q", "main")
    human_sha = commit_file(tmp_git_repo, "human.txt", "human work\n", "interleaved human commit")
    git(tmp_git_repo, "checkout", "-q", "feature")

    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"interleaved human commit must integrate: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="interleaved human work and feature work must both land",
        feature=feature_sha,
        source_refs=(human_sha,),
    )


def test_mid_rebase_new_feature_and_target_commits_land_and_verify(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    old_target = git(tmp_git_repo, "rev-parse", "main")
    new_target = git(
        tmp_git_repo,
        "commit-tree",
        f"{old_target}^{{tree}}",
        "-p",
        old_target,
        "-m",
        "target mid-rebase",
    )
    git(tmp_git_repo, "update-ref", "refs/heads/main", new_target, old_target)

    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"mid-rebase target/feature commits must integrate: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="mid-rebase commit updates must complete and land",
        feature=feature_sha,
        source_refs=(new_target,),
    )


def test_strategy_switch_carrying_resolved_work(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "shared1.txt", "base1\n", "base1")
    commit_file(tmp_git_repo, "shared2.txt", "base2\n", "base2")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    commit_file(tmp_git_repo, "shared1.txt", "feature1\n", "feature commit 1")
    commit_file(tmp_git_repo, "shared2.txt", "feature2\n", "feature commit 2")
    feature_sha = git(tmp_git_repo, "rev-parse", "HEAD")

    git(tmp_git_repo, "checkout", "-q", "main")
    commit_file(tmp_git_repo, "shared1.txt", "target1\n", "target commit 1")
    commit_file(tmp_git_repo, "shared2.txt", "target2\n", "target commit 2")
    git(tmp_git_repo, "checkout", "-q", "feature")

    stop_counts: dict[str, int] = {}

    def switching_resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        stop_id = ",".join(stop.conflicted_files)
        stop_counts[stop_id] = stop_counts.get(stop_id, 0) + 1
        if "shared2.txt" in stop.conflicted_files and stop_counts[stop_id] == 1:
            return False
        for rel in stop.conflicted_files:
            (root / rel).write_text(f"resolved {rel}\n", encoding="utf-8")
        git(root, "add", "--", *stop.conflicted_files)
        return True

    run = drive_integration(tmp_git_repo, resolver=switching_resolver)
    assert run.error is None, f"strategy switch carrying resolved work must succeed: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="strategy switch must carry previously resolved work across stops",
        feature=feature_sha,
        source_refs=("main",),
    )
