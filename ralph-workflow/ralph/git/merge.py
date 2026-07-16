"""Non-interactive merge, fast-forward, and worktree primitives for Ralph.

These primitives back :mod:`ralph.pipeline.auto_integrate`. They wrap
``ralph.git.subprocess_runner.run_git`` (which already enforces
non-interactive git env: ``GIT_TERMINAL_PROMPT=0``, ``GIT_EDITOR=:"``,
``GIT_PAGER=cat``) so a missing credential or hung pager can never
block the integration step.

The module never calls ``git push`` or ``repo.remote().push``; the
auto-integrate contract is local-only (AC-10).

Public functions (every primitive has a docstring):

* ``branch_exists`` — cheap ``git show-ref --verify`` check.
* ``branch_sha`` — observed HEAD SHA of a branch (the value used as
  the compare-and-swap ``<oldvalue>`` argument later).
* ``is_ancestor`` — wraps ``git merge-base --is-ancestor``.
* ``merge_in_progress`` — checks for ``MERGE_HEAD`` in the git dir.
* ``merge_target_into_current`` — runs ``git merge --no-edit`` against
  the current branch; aborts cleanly on conflict and reports
  ``MergeResult(outcome='conflict', ...)``.
* ``abort_merge`` — guarded ``git merge --abort``.
* ``reset_hard`` — guarded ``git reset --hard``; used by crash
  recovery to restore the feature branch to its pre-integration SHA.
* ``fast_forward_via_worktree`` — ``git -C <wt> merge --ff-only
  <feature_sha>``. ``merge --ff-only`` is itself the atomic guard:
  it refuses and returns non-zero when ``feature_sha`` is not a
  fast-forward of the worktree's checked-out branch, so a
  concurrently-advanced target is never force-moved (AC-08).
* ``compare_and_swap_branch`` — ``git update-ref refs/heads/<target>
  <new> <old>``. Git performs an atomic CAS: it updates the ref ONLY
  if it still equals ``<old>``, returning rc==0; if the target moved
  concurrently the rc is non-zero and the ref is left untouched.
  This REPLACES the previous unconditional ``git branch -f`` and
  closes the AC-08 TOCTOU race.
* ``worktree_for_branch`` — parses ``git worktree list --porcelain``
  for the worktree whose checked-out branch is ``refs/heads/<branch>``.
* ``resolve_origin_head_branch`` — strips
  ``refs/remotes/origin/`` from ``git symbolic-ref --quiet
  refs/remotes/origin/HEAD``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.git.subprocess_runner import GitRunOptions, run_git


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a merge attempt.

    ``outcome`` is one of ``'success'`` (merge completed cleanly;
    may include a merge commit when both sides diverged, or may be
    a fast-forward), ``'noop'`` (target already an ancestor of
    HEAD or working tree unchanged), or ``'conflict'`` (merge
    conflict; the in-progress merge has already been aborted so the
    working tree is clean).
    """

    outcome: str


def branch_exists(repo_root: Path | str, name: str) -> bool:
    """Return True when ``refs/heads/<name>`` resolves in ``repo_root``."""
    repo_root_path = Path(repo_root)
    result = run_git(
        ("show-ref", "--verify", "--quiet", f"refs/heads/{name}"),
        cwd=repo_root_path,
        label="git-branch-exists",
    )
    return result.returncode == 0


def branch_sha(repo_root: Path | str, name: str) -> str | None:
    """Return the SHA of ``refs/heads/<name>`` or ``None`` when absent.

    The returned SHA is the value to pass as the
    ``<oldvalue>`` argument to :func:`compare_and_swap_branch` so a
    concurrent landing cannot be silently force-overwritten (AC-08).
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/heads/{name}"),
        cwd=repo_root_path,
        label="git-branch-sha",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def is_ancestor(repo_root: Path | str, ancestor: str, descendant: str) -> bool:
    """Return True when ``ancestor`` is reachable from ``descendant``.

    Thin wrapper over ``git merge-base --is-ancestor``. Used to gate
    the fast-forward phase (AC-08 guard): ``is_ancestor(target,
    feature_sha)`` must be True before we attempt to move the target
    to ``feature_sha``; if False, the target has diverged and we
    must skip with a recorded reason.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("merge-base", "--is-ancestor", ancestor, descendant),
        cwd=repo_root_path,
        label="git-is-ancestor",
    )
    return result.returncode == 0


def merge_in_progress(repo_root: Path | str) -> bool:
    """Return True when a ``MERGE_HEAD`` exists in the git directory.

    Used by the crash-recovery preamble to detect an owned merge we
    must abort before restoring the feature branch.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("rev-parse", "--git-dir"),
        cwd=repo_root_path,
        label="git-merge-progress:git-dir",
    )
    if result.returncode != 0:
        return False
    git_dir_raw = result.stdout.strip()
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = (repo_root_path / git_dir).resolve()
    return (git_dir / "MERGE_HEAD").exists()


def merge_target_into_current(repo_root: Path | str, target: str) -> MergeResult:
    """Run ``git merge --no-edit <target>`` into the current branch.

    On non-zero return code, run ``git merge --abort`` (guarded by a
    ``merge_in_progress`` precheck) and return a conflict result. On
    success return ``MergeResult(outcome='success')``. The merge is
    never force-resolved; conflict outcome is the only escape hatch
    other than success.

    The target branch name is passed AFTER the ``--`` option
    terminator so a configured value that starts with ``-`` (e.g.
    ``--allow-unrelated-histories``) is treated as a positional
    revision argument and never parsed as a git option. This
    closes the untrusted-config subprocess-argument-boundary
    exposure that the prompt's feedback item flagged.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("merge", "--no-edit", "--", target),
        cwd=repo_root_path,
        label="git-merge",
    )
    if result.returncode == 0:
        return MergeResult(outcome="success")
    # Conflict path. Abort the in-progress merge so the working tree
    # is left clean (the caller can then record the conflict and
    # return; auto_integrate uses this to satisfy AC-07).
    if merge_in_progress(repo_root_path):
        abort_merge(repo_root_path)
    return MergeResult(outcome="conflict")


def abort_merge(repo_root: Path | str) -> None:
    """Abort an in-progress merge; no-op when no merge is in progress.

    The precheck is the same fail-closed pattern used by
    :func:`ralph.git.rebase.rebase.abort_rebase` so a stray operator
    merge is never disturbed by the auto-integrate recovery path.
    """
    repo_root_path = Path(repo_root)
    if not merge_in_progress(repo_root_path):
        return
    run_git(
        ("merge", "--abort"),
        cwd=repo_root_path,
        label="git-merge-abort",
    )


def reset_hard(repo_root: Path | str, sha: str) -> None:
    """Run ``git reset --hard <sha>``; crash-recovery only.

    Used by :func:`ralph.pipeline.auto_integrate.recover_incomplete_integration`
    to restore the feature branch to its ``pre_feature_sha`` when a
    run is interrupted mid-rebase. Do NOT call this from any other
    code path — a hard reset on a feature branch is destructive to
    any uncommitted work.
    """
    repo_root_path = Path(repo_root)
    run_git(
        ("reset", "--hard", sha),
        cwd=repo_root_path,
        label="git-reset-hard",
    )


def fast_forward_via_worktree(worktree_root: Path | str, feature_sha: str) -> bool:
    """Fast-forward the branch checked out in ``worktree_root``.

    Returns True on success; False when ``feature_sha`` is NOT a
    fast-forward of the worktree's current branch. ``git merge
    --ff-only`` is itself the atomic guard — non-zero return when
    the requested SHA is not a descendant of HEAD — so a target that
    advanced concurrently between observation and the call is never
    force-moved.
    """
    worktree_root_path = Path(worktree_root)
    result = run_git(
        ("merge", "--ff-only", feature_sha),
        cwd=worktree_root_path,
        label="git-merge-ff-only",
    )
    return result.returncode == 0


def compare_and_swap_branch(
    repo_root: Path | str,
    target: str,
    expected_old_sha: str,
    new_sha: str,
) -> bool:
    """Move ``refs/heads/<target>`` from ``expected_old_sha`` to ``new_sha`` atomically.

    Uses ``git update-ref`` with the ``<newvalue> <oldvalue>`` form
    so git performs an atomic compare-and-swap: it updates the ref
    ONLY if it still equals ``expected_old_sha``, returning rc==0;
    if the target advanced concurrently the rc is non-zero and the
    ref is left untouched. This REPLACES the previous unconditional
    ``git branch -f`` and closes the AC-08 TOCTOU race.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("update-ref", f"refs/heads/{target}", new_sha, expected_old_sha),
        cwd=repo_root_path,
        label="git-update-ref-cas",
    )
    return result.returncode == 0


def worktree_for_branch(repo_root: Path | str, branch: str) -> Path | None:
    """Return the worktree path that has ``refs/heads/<branch>`` checked out.

    Parses ``git worktree list --porcelain`` (the stable, scriptable
    format). Returns ``None`` when the branch is not checked out in
    any worktree of the shared git common directory — the caller
    then uses :func:`compare_and_swap_branch` to move the ref
    directly.

    When multiple worktrees have the same branch checked out (only
    possible if one of them is the primary repo and another is a
    linked worktree, which is itself illegal by git's own rules),
    the FIRST match wins. The ``branch <ref>`` field is the
    authoritative "what branch is checked out here" indicator; the
    ``HEAD`` field is just a detached SHA / refs/heads/<name>
    pointer and is intentionally NOT used to decide which branch is
    checked out.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("worktree", "list", "--porcelain"),
        cwd=repo_root_path,
        label="git-worktree-list",
    )
    if result.returncode != 0:
        return None
    target_ref = f"refs/heads/{branch}"
    current_path: Path | None = None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            current_path = None
            continue
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
        elif line.startswith("branch ") and line == f"branch {target_ref}" and current_path is not None:
            return current_path
    return None


def resolve_origin_head_branch(repo_root: Path | str) -> str | None:
    """Return the remote default branch name resolved via ``origin/HEAD``.

    Strips the ``refs/remotes/origin/`` prefix. Returns ``None``
    when the remote lacks a symbolic HEAD or no ``origin`` remote is
    configured. Used by :func:`ralph.pipeline.auto_integrate.resolve_integration_target`
    as the first auto-detection candidate.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"),
        cwd=repo_root_path,
        label="git-symbolic-ref-origin-head",
        options=GitRunOptions(),
    )
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    prefix = "refs/remotes/origin/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref or None


__all__ = [
    "MergeResult",
    "abort_merge",
    "branch_exists",
    "branch_sha",
    "compare_and_swap_branch",
    "fast_forward_via_worktree",
    "is_ancestor",
    "merge_in_progress",
    "merge_target_into_current",
    "reset_hard",
    "resolve_origin_head_branch",
    "worktree_for_branch",
]
