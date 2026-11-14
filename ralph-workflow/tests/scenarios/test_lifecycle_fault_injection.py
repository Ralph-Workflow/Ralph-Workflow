from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

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

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(30),
]


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
    feature_sha = _large_conflicted_range(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    run = drive_integration(
        tmp_git_repo, config=scenario_config(), resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES)
    )
    assert run.error is None, f"large finite work must not crash: {run.error}"
    assert run.resolver_launches >= 20, (
        "a 50-commit rebase with 20 progressing conflicts must not hit a stop, round, attempt or time cap"
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
    feature_sha = _large_conflicted_range(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    completed_stops: list[str] = []
    calls = 0
    cooldown_observed = False
    wait_or_fallover_observed = False

    def resolver(root: Path, _target: str, stop: RebaseStop) -> bool:
        nonlocal calls, cooldown_observed, wait_or_fallover_observed
        calls += 1
        conflicted = tuple(stop.conflicted_files)
        stop_identity = git(root, "rev-parse", "REBASE_HEAD")
        assert stop_identity not in completed_stops, "a completed stop must never be redone"
        if calls == 11:
            raise ConnectionError("scenario provider failed partway through finite work")
        for relative in conflicted:
            (root / relative).write_text(f"resolved stop {calls}\n", encoding="utf-8")
        git(root, "add", "--", *conflicted)
        completed_stops.append(stop_identity)
        return True

    run = drive_integration(tmp_git_repo, config=scenario_config(), resolver=resolver)
    durable = record_path(tmp_git_repo)
    if durable.exists():
        payload = durable.read_text(encoding="utf-8").lower()
        cooldown_observed = "cooldown" in payload or "retry_not_before" in payload
        wait_or_fallover_observed = "waiting" in payload or "fall" in payload
    assert run.error is None, f"large finite work must survive one resolver failure: {run.error}"
    assert cooldown_observed, "the shared agent cooldown must be durably observable"
    assert wait_or_fallover_observed, "the run must expose fall-over or waiting after failure"
    assert len(completed_stops) >= 20
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
