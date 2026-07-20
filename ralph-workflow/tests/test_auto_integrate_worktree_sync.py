"""Real-worktree regression coverage for automatic branch integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _base_branch(repo_root: Path) -> str:
    return _run(repo_root, "symbolic-ref", "--quiet", "HEAD").stdout.strip().removeprefix(
        "refs/heads/"
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _add_worktree(repo_root: Path, path: Path, branch: str) -> None:
    assert _run(repo_root, "worktree", "add", "-b", branch, str(path)).returncode == 0


def test_commit_rebases_feature_and_fast_forwards_main(tmp_git_repo: Path) -> None:
    """AC-01: a feature worktree rebases and advances the shared main ref."""
    main = _base_branch(tmp_git_repo)
    feature = tmp_git_repo.parent / "feature-a"
    _add_worktree(tmp_git_repo, feature, "feature-a")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        _commit(tmp_git_repo, "main.txt", "main\n", "main change")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert outcome.last_action in {"rebased", "merged"}
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(tmp_git_repo, main, feature_head) is True
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_default_config_worktree_agent_advances_shared_main(tmp_git_repo: Path) -> None:
    """AC-01 regression: default target detection lands despite a dirty main checkout."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "tracked.txt", "base\n", "seed tracked file")
    feature = tmp_git_repo.parent / "feature-default"
    _add_worktree(tmp_git_repo, feature, "feature-default")
    try:
        _commit(feature, "feature.txt", "feature\n", "feature change")
        dirty_file = tmp_git_repo / "tracked.txt"
        dirty_file.write_text("operator work\n", encoding="utf-8")

        outcome = auto_integrate_after_commit(
            _default_config(), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
        assert is_ancestor(tmp_git_repo, main, feature_head) is True
        assert dirty_file.read_text(encoding="utf-8") == "operator work\n"
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))


def test_two_independent_worktree_agents_converge_on_main(tmp_git_repo: Path) -> None:
    """Regression: an idle feature must rebase after main advances past its tip.

    The former ``no commits beyond target`` skip treated a feature behind main
    as already integrated, so it never received another agent's landed commit.
    """
    main = _base_branch(tmp_git_repo)
    feature_a = tmp_git_repo.parent / "feature-a"
    feature_b = tmp_git_repo.parent / "feature-b"
    _add_worktree(tmp_git_repo, feature_a, "feature-a")
    _add_worktree(tmp_git_repo, feature_b, "feature-b")
    try:
        _commit(feature_a, "a.txt", "a\n", "feature a")
        first = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature_a), RebaseState()
        )
        feature_a_head = _run(feature_a, "rev-parse", "HEAD").stdout.strip()

        assert first is not None
        assert first.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_a_head

        _commit(feature_b, "b.txt", "b\n", "feature b")
        second = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature_b), RebaseState()
        )
        feature_b_head = _run(feature_b, "rev-parse", "HEAD").stdout.strip()

        assert second is not None
        assert second.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_b_head
        assert (feature_b / "a.txt").exists()

        catch_up = auto_integrate_on_phase_transition(
            _build_config(target=main), WorkspaceScope(feature_a), RebaseState()
        )

        assert catch_up is not None
        assert catch_up.fast_forwarded is True
        assert (feature_a / "b.txt").exists()
        assert _run(feature_a, "rev-parse", "HEAD").stdout.strip() == branch_sha(tmp_git_repo, main)
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature_b))
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature_a))


def test_rebase_conflict_falls_back_to_endpoint_merge_and_fast_forwards(
    tmp_git_repo: Path,
) -> None:
    """AC-01: a replay conflict can land through a clean endpoint merge."""
    main = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "one\ntwo\nthree\n", "seed shared")
    feature = tmp_git_repo.parent / "feature-conflict"
    _add_worktree(tmp_git_repo, feature, "feature-conflict")
    try:
        _commit(feature, "shared.txt", "one\nfeature\nthree\n", "feature change")
        _commit(feature, "shared.txt", "one\ntwo\nthree\n", "feature revert")
        _commit(tmp_git_repo, "shared.txt", "one\nmain\nthree\n", "main change")

        outcome = auto_integrate_after_commit(
            _build_config(target=main), WorkspaceScope(feature), RebaseState()
        )
        feature_head = _run(feature, "rev-parse", "HEAD").stdout.strip()

        assert outcome is not None
        assert outcome.last_action == "merged"
        assert outcome.fast_forwarded is True
        assert branch_sha(tmp_git_repo, main) == feature_head
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(feature))
