from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git import Repo

from ralph.git.merge import (
    abort_merge,
    branch_exists,
    branch_sha,
    compare_and_swap_branch,
    conflict_stage_entries,
    fast_forward_via_worktree,
    is_ancestor,
    merge_in_progress,
    merge_target_into_current,
    observe_branch_sha,
    paths_with_conflict_markers,
    reset_keep,
    staged_conflict_marker_paths,
    worktree_for_branch,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    head_file = tmp_git_repo / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _add_worktree(repo_root: Path, wt_path: Path, branch: str) -> subprocess.CompletedProcess[str]:
    return _run(repo_root, "worktree", "add", str(wt_path), branch)


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def test_branch_exists_true_and_false(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    assert branch_exists(tmp_git_repo, base) is True
    assert branch_exists(tmp_git_repo, "definitely-not-a-branch-xyz") is False


def test_branch_sha_returns_sha_or_none(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    expected = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert branch_sha(tmp_git_repo, base) == expected
    assert branch_sha(tmp_git_repo, "missing-branch") is None


def test_observe_branch_sha_separates_an_absent_branch_from_a_failed_query(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    base = _base_branch(tmp_git_repo)
    expected = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()

    assert observe_branch_sha(tmp_git_repo, base) == (expected, True)
    assert observe_branch_sha(tmp_git_repo, "missing-branch") == (None, True)
    assert observe_branch_sha(tmp_path, base) == (None, False)

    assert branch_sha(tmp_git_repo, "missing-branch") is None
    assert branch_sha(tmp_path, base) is None


def test_is_ancestor_true_and_false(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    base_sha = branch_sha(tmp_git_repo, base)
    assert base_sha is not None
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feat.txt", "feature\n", "add feat")
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert is_ancestor(tmp_git_repo, base_sha, head_sha) is True
    assert is_ancestor(tmp_git_repo, head_sha, base_sha) is False


def test_merge_target_into_current_clean_returns_success(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature.txt", "feature body\n", "feature work")
    feature_tip = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "mainline.txt", "mainline body\n", "mainline work")
    _run(tmp_git_repo, "checkout", "feature")
    result = merge_target_into_current(tmp_git_repo, base)
    assert result.outcome == "success"
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert (tmp_git_repo / "feature.txt").exists()
    assert (tmp_git_repo / "mainline.txt").exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() != feature_tip


def test_merge_target_into_current_conflict_aborts_cleanly(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "shared.txt", "mainline version\n", "mainline shared")
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    result = merge_target_into_current(tmp_git_repo, base)
    assert result.outcome == "conflict"
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "diff", "--name-only", "--diff-filter=U").stdout.strip() == ""
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_sha


def test_a_resolved_merge_with_an_rst_heading_has_no_staged_markers(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "a.txt", "base\n", "base a")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "a.txt", "feature\n", "feature a")
    _run(tmp_git_repo, "checkout", base)
    (tmp_git_repo / "CHANGES.rst").write_text("Changes\n=======\n\n- new\n", encoding="utf-8")
    _commit_file(tmp_git_repo, "a.txt", "main\n", "main a")
    _run(tmp_git_repo, "add", "CHANGES.rst")
    _run(tmp_git_repo, "commit", "-m", "changelog")
    _run(tmp_git_repo, "checkout", "feature")

    assert merge_target_into_current(tmp_git_repo, base, keep_conflicts=True).outcome == "conflict"
    (tmp_git_repo / "a.txt").write_text("feature+main\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "a.txt")

    assert staged_conflict_marker_paths(tmp_git_repo) == []
    (tmp_git_repo / "a.txt").write_text("<<<<<<< ours\nfeature\n=======\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "a.txt")
    assert staged_conflict_marker_paths(tmp_git_repo) == ["a.txt"]


def test_reset_keep_restores_branch_to_pre_integration_sha(tmp_git_repo: Path) -> None:
    original_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _commit_file(tmp_git_repo, "temp.txt", "temporary\n", "temp commit")
    new_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert new_sha != original_sha
    reset_keep(tmp_git_repo, original_sha)
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == original_sha
    assert not (tmp_git_repo / "temp.txt").exists()
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_fast_forward_via_worktree_advances_checked_out_branch(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    wt_branch = "wt-base-tmp"
    _run(tmp_git_repo, "branch", wt_branch, base)
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        target = wt_path / "main_only.txt"
        target.write_text("mainline\n", encoding="utf-8")
        _run(wt_path, "add", "main_only.txt")
        _run(wt_path, "commit", "-m", "main only")
        new_tip = _run(wt_path, "rev-parse", "HEAD").stdout.strip()
        ok = fast_forward_via_worktree(wt_path, new_tip)
        assert ok is True
        wt_branch_sha = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        assert wt_branch_sha == new_tip
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_fast_forward_via_worktree_refuses_non_fast_forward(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature_div.txt", "divergent\n", "divergent")
    divergent_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "base_forward.txt", "base forward\n", "base forward")
    wt_branch = "wt-base-tmp"
    _run(tmp_git_repo, "branch", wt_branch, base)
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        before = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        ok = fast_forward_via_worktree(wt_path, divergent_sha)
        assert ok is False
        after = _run(wt_path, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
        assert after == before
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_compare_and_swap_branch_succeeds_when_old_sha_matches(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    new_sha = _commit_file(tmp_git_repo, "bumped.txt", "bump\n", "bump")
    observed = branch_sha(tmp_git_repo, base)
    assert observed is not None and observed != new_sha
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == observed
    ok = compare_and_swap_branch(tmp_git_repo, base, observed, new_sha)
    assert ok is True
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == new_sha


def test_compare_and_swap_branch_fails_closed_on_stale_old_sha(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    observed = branch_sha(tmp_git_repo, base)
    assert observed is not None
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    concurrent_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "-f", base, concurrent_sha)
    post_concurrent = branch_sha(tmp_git_repo, base)
    assert post_concurrent is not None and post_concurrent != observed
    stale_target_sha = _run(tmp_git_repo, "rev-parse", "HEAD~1").stdout.strip()
    ok = compare_and_swap_branch(tmp_git_repo, base, observed, stale_target_sha)
    assert ok is False
    assert branch_sha(tmp_git_repo, base) == post_concurrent


def test_worktree_for_branch_returns_correct_path_and_none(
    tmp_git_repo: Path,
) -> None:
    _base_branch(tmp_git_repo)
    wt_branch = "wt-tmp-branch"
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _run(tmp_git_repo, "branch", wt_branch)
    _add_worktree(tmp_git_repo, wt_path, wt_branch)
    try:
        found = worktree_for_branch(tmp_git_repo, wt_branch)
        assert found is not None
        assert Path(found).resolve() == wt_path.resolve()
        assert worktree_for_branch(tmp_git_repo, "definitely-not-checked-out") is None
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))


def test_merge_regression_lone_conflict_marker_is_reported(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "open_only.txt").write_text(
        "<<<<<<< HEAD\nfeature version\nbase version\n", encoding="utf-8"
    )
    (tmp_git_repo / "close_only.txt").write_text(
        "feature version\nbase version\n>>>>>>> main\n", encoding="utf-8"
    )
    (tmp_git_repo / "prose.txt").write_text(
        "Heading\n=======\nordinary reStructuredText prose\n", encoding="utf-8"
    )

    reported = paths_with_conflict_markers(
        tmp_git_repo, ["open_only.txt", "close_only.txt", "prose.txt"]
    )

    assert reported == ["open_only.txt", "close_only.txt"]


def test_paths_with_conflict_markers_ignores_clean_and_unreadable_paths(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "resolved.txt").write_text("merged content\n", encoding="utf-8")

    assert paths_with_conflict_markers(tmp_git_repo, ["resolved.txt", "never_written.txt"]) == []


def test_abort_merge_is_safe_when_no_merge_in_progress(tmp_git_repo: Path) -> None:
    assert merge_in_progress(tmp_git_repo) is False
    abort_merge(tmp_git_repo)
    assert merge_in_progress(tmp_git_repo) is False
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


__all__ = ["Repo"]


def test_conflict_stage_entries_parses_mode_only_and_gitlink(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    from ralph.git import merge as merge_module

    output = (
        "100644 base 1\tspace path\0"
        "100644 blob 2\tspace path\0"
        "100755 blob 3\tspace path\0"
        "160000 old 2\tsub\0"
        "160000 new 3\tsub\0"
    )
    monkeypatch.setattr(
        merge_module,
        "run_git",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": output})(),
    )

    assert conflict_stage_entries(tmp_git_repo, ["space path", "sub"]) == {
        "space path": {1: ("100644", "base"), 2: ("100644", "blob"), 3: ("100755", "blob")},
        "sub": {2: ("160000", "old"), 3: ("160000", "new")},
    }


def test_conflict_stage_entries_returns_empty_when_git_query_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    from ralph.git import merge as merge_module

    monkeypatch.setattr(
        merge_module,
        "run_git",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": ""})(),
    )

    assert conflict_stage_entries(tmp_git_repo, ["conflict"]) == {}
