from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import AgentMode, FakeAgentRunner, git, paused_text_rebase
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


def _resolve_current_stop(root: Path) -> None:
    (root / "shared.txt").write_text("first stop resolved\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "-c", "core.editor=:", "rebase", "--continue", check=False)


def test_strategy_switch_keeps_partly_resolved_work(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    _resolve_current_stop(tmp_git_repo)
    resolved = (tmp_git_repo / "shared.txt").read_text(encoding="utf-8")

    run = drive_integration(tmp_git_repo, resolver=lambda _root, _target, _stop: False)

    assert run.error is None, f"a strategy switch must not crash: {run.error}"
    assert (tmp_git_repo / "shared.txt").read_text(encoding="utf-8") == resolved, (
        "switching strategy mid-flight must keep already-resolved work"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="a mid-flight strategy switch must still land the feature",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_new_feature_commits_during_rebase_are_absorbed(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    old_tip = git(tmp_git_repo, "rev-parse", "feature")
    tree = git(tmp_git_repo, "rev-parse", f"{old_tip}^{{tree}}")
    new_tip = git(tmp_git_repo, "commit-tree", tree, "-p", old_tip, "-m", "late feature work")
    git(tmp_git_repo, "update-ref", "refs/heads/feature", new_tip, old_tip)

    run = drive_integration(
        tmp_git_repo,
        resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES),
    )

    assert run.error is None, f"new feature work must be absorbed without a crash: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", new_tip, "main", check=False), (
        "commits added to the feature during integration must reach the target"
    )


def test_new_target_commits_during_rebase_are_absorbed(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    old_target = git(tmp_git_repo, "rev-parse", "main")
    tree = git(tmp_git_repo, "rev-parse", f"{old_target}^{{tree}}")
    new_target = git(tmp_git_repo, "commit-tree", tree, "-p", old_target, "-m", "late target work")
    git(tmp_git_repo, "update-ref", "refs/heads/main", new_target, old_target)

    run = drive_integration(
        tmp_git_repo,
        resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES),
    )

    assert run.error is None, f"new target work must be absorbed without a crash: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", new_target, "main", check=False), (
        "a target commit added mid-flight must remain reachable after landing"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="the feature must land after the target moves mid-flight",
        feature=feature_sha,
        source_refs=(new_target,),
    )
