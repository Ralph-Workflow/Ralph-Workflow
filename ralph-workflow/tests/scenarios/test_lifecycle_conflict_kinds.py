from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import (
    AgentMode,
    FakeAgentRunner,
    commit_file,
    git,
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    drive_integration,
    snapshot_uncommitted,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e, pytest.mark.timeout_seconds(12)]


def test_resolved_but_unstaged_file_is_finished_without_an_agent(tmp_git_repo: Path) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    (tmp_git_repo / "shared.txt").write_text("resolved by operator\n", encoding="utf-8")
    run = drive_integration(tmp_git_repo)
    assert run.resolver_launches == 0, "resolved-but-unstaged content must be staged mechanically before launching an agent"
    assert_content_equivalence(tmp_git_repo, contract="resolved-but-unstaged content must land", feature=feature_sha, source_refs=("main",))


def test_gitlink_vendor_conflict_uses_a_policy_pointer_and_lands(tmp_git_repo: Path) -> None:
    paused_text_and_gitlink_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"gitlink policy handling must not crash: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract="a gitlink conflict must land with the policy-selected pointer", feature=feature_sha, source_refs=("main",))


@pytest.mark.parametrize("kind", ["append-only", "delete-modify", "binary"])
def test_conflict_kind_lands_without_corruption(tmp_git_repo: Path, kind: str) -> None:
    commit_file(tmp_git_repo, "shared.dat", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    if kind == "delete-modify":
        git(tmp_git_repo, "rm", "shared.dat")
        git(tmp_git_repo, "commit", "-q", "-m", "feature deletes")
    else:
        payload = "feature append\n" if kind == "append-only" else "\x00feature\x00"
        commit_file(tmp_git_repo, "shared.dat", payload, "feature changes")
    feature_sha = git(tmp_git_repo, "rev-parse", "HEAD")
    git(tmp_git_repo, "checkout", "-q", "main")
    commit_file(tmp_git_repo, "shared.dat", "target change\n", "target changes")
    target_before = git(tmp_git_repo, "rev-parse", "HEAD")
    git(tmp_git_repo, "checkout", "-q", "feature")
    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))
    assert run.error is None, f"{kind} conflict handling must not crash: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract=f"a {kind} conflict must land without losing feature content", feature=feature_sha, source_refs=(target_before,))


def test_empty_after_replay_commit_is_preserved_or_explicitly_excluded(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "shared.txt", "same\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    empty_sha = git(tmp_git_repo, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "empty replay")
    git(tmp_git_repo, "reset", "--hard", empty_sha)
    run = drive_integration(tmp_git_repo)
    reason = "" if run.state is None else (run.state.last_reason or "")
    preserved = bool(git(tmp_git_repo, "branch", "--contains", empty_sha, "main", check=False))
    assert preserved or "empty" in reason.lower() or "excluded" in reason.lower(), (
        "an empty-after-replay commit must be preserved or explicitly excluded"
    )


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_dirty_worktree_content_is_preserved_while_feature_lands(tmp_git_repo: Path, dirty_kind: str) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    relative = "base.txt" if dirty_kind == "tracked" else "untracked.txt"
    (tmp_git_repo / relative).write_text("uncommitted\n", encoding="utf-8")
    preserved = snapshot_uncommitted(tmp_git_repo, (relative,))
    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"dirty {dirty_kind} work must not crash integration: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract=f"dirty {dirty_kind} work must survive while feature content lands", feature=feature_sha, source_refs=("main",), preserved=preserved)


def test_failing_hook_does_not_block_landing(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"a failing hook must route to a defined autonomous path: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract="a failing hook must not prevent committed feature work from landing", feature=feature_sha, source_refs=("main",))
