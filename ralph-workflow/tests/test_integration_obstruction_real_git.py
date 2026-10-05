"""Real-git proof that nothing in the worktree can strand a branch behind its target.

Regression 2026-10-05: 22 untracked files that ``main`` also tracks made
``git merge main`` refuse to start (exit 2). No merge ran, so no resolver
could act, and planning began 40 commits behind ``main``. Every refusal
shape is driven through real git here: identical leftovers are cleared,
differing local work is preserved in a commit and handed to the resolver,
and the branch ends up containing the target.

Registered in ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` so ``make verify``
runs it even though it crosses the real git boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.git.merge import merge_in_progress, merge_target_into_current

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
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _behind_with_untracked_copy(tmp_git_repo: Path, content: str) -> str:
    """Feature branch behind ``base``, holding an untracked ``incoming.txt``."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature.txt", "feature body\n", "feature work")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "incoming.txt", "mainline body\n", "mainline adds incoming")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "incoming.txt").write_text(content, encoding="utf-8")
    return base


def test_merge_lands_over_untracked_copy_identical_to_target(tmp_git_repo: Path) -> None:
    """Regression 2026-10-05: an identical untracked leftover no longer blocks the merge."""
    base = _behind_with_untracked_copy(tmp_git_repo, "mainline body\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "success"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_merge_refusal_over_differing_untracked_file_keeps_it_and_names_it(
    tmp_git_repo: Path,
) -> None:
    """Local untracked work is never deleted; the refusal names the path."""
    base = _behind_with_untracked_copy(tmp_git_repo, "local work\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "conflict"
    assert result.reason == "merge refused: untracked files would be overwritten: incoming.txt"
    assert (tmp_git_repo / "incoming.txt").read_text(encoding="utf-8") == "local work\n"
    assert merge_in_progress(tmp_git_repo) is False


def test_refused_merge_hands_preserved_local_work_to_the_resolver(tmp_git_repo: Path) -> None:
    """Regression 2026-10-05: a refusal must end in a resolver call, not a stale branch.

    The differing untracked file is committed (never deleted), the merge
    runs again, the add/add overlap is a real conflict, and the resolver --
    the agent in production -- reconciles it. The branch then contains the
    target.
    """
    from ralph.pipeline.auto_integrate_resolve import endpoint_merge_with_resolution

    base = _behind_with_untracked_copy(tmp_git_repo, "local work\n")
    calls: list[str] = []

    def _resolver(root: Path, target: str) -> bool:
        calls.append(target)
        (root / "incoming.txt").write_text("local work\nmainline body\n", encoding="utf-8")
        return True

    result = endpoint_merge_with_resolution(tmp_git_repo, base, _resolver)

    assert result is not None
    assert result.outcome == "success"
    assert calls == [base]
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    assert (tmp_git_repo / "incoming.txt").read_text(encoding="utf-8") == (
        "local work\nmainline body\n"
    )
    log = _run(tmp_git_repo, "log", "--format=%s").stdout
    assert "preserve local work blocking integration" in log


def test_refused_merge_over_uncommitted_tracked_edit_reaches_the_resolver(
    tmp_git_repo: Path,
) -> None:
    """``local changes would be overwritten`` is preserved and resolved the same way."""
    from ralph.pipeline.auto_integrate_resolve import endpoint_merge_with_resolution

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "shared.txt", "original\n", "seed shared")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "shared.txt", "mainline\n", "mainline edits shared")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "shared.txt").write_text("uncommitted local\n", encoding="utf-8")

    def _resolver(root: Path, _target: str) -> bool:
        (root / "shared.txt").write_text("uncommitted local + mainline\n", encoding="utf-8")
        return True

    result = endpoint_merge_with_resolution(tmp_git_repo, base, _resolver)

    assert result is not None
    assert result.outcome == "success"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    assert (tmp_git_repo / "shared.txt").read_text(encoding="utf-8") == (
        "uncommitted local + mainline\n"
    )


def test_preserve_uncommitted_tracked_work_commits_edits_only(tmp_git_repo: Path) -> None:
    """The planning gate's dirty-tree preservation keeps edits and leaves untracked files."""
    from ralph.git.merge_obstructions import preserve_uncommitted_tracked_work

    _commit_file(tmp_git_repo, "tracked.txt", "v1\n", "seed tracked")
    (tmp_git_repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
    (tmp_git_repo / "untracked.txt").write_text("loose\n", encoding="utf-8")

    assert preserve_uncommitted_tracked_work(tmp_git_repo, "main") is True
    assert _run(tmp_git_repo, "show", "HEAD:tracked.txt").stdout == "v2\n"
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == "?? untracked.txt"
    assert preserve_uncommitted_tracked_work(tmp_git_repo, "main") is False


def test_a_hook_rejecting_snapshot_commits_cannot_strand_the_branch(tmp_git_repo: Path) -> None:
    """Unattended runs cannot wait for a human: preservation commits bypass hooks.

    The hook here rejects every non-merge commit. The preservation commit
    only snapshots work that already exists, so it skips hooks; the merge
    commit, which carries the resolver's new content, still runs them.
    """
    from ralph.pipeline.auto_integrate_resolve import endpoint_merge_with_resolution

    base = _behind_with_untracked_copy(tmp_git_repo, "local work\n")
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    hook = git_dir / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        '#!/bin/sh\ntest -f "$(git rev-parse --git-dir)/MERGE_HEAD" && exit 0\nexit 1\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)

    def _resolver(root: Path, _target: str) -> bool:
        (root / "incoming.txt").write_text("reconciled\n", encoding="utf-8")
        return True

    result = endpoint_merge_with_resolution(tmp_git_repo, base, _resolver)

    assert result is not None
    assert result.outcome == "success"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
