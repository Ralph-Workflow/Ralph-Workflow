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
* ``merge_state`` — three-valued merge check: in progress / none /
  unknown (the git query itself failed). The third verdict is what
  keeps ``abort_merge`` from silently doing nothing on a repository
  it cannot read.
* ``merge_in_progress`` — boolean projection of ``merge_state``.
* ``merge_target_into_current`` — runs ``git merge --no-edit`` against
  the current branch; aborts cleanly on conflict and reports
  ``MergeResult(outcome='conflict', ...)``; ``keep_conflicts=True``
  leaves a conflicted merge in progress for resolution.
* ``unmerged_paths`` — lists paths still carrying conflict markers.
* ``stage_paths`` — ``git add -- <paths>``; Ralph stages a resolved
  conflict itself so resolution never depends on the resolving
  agent's git access.
* ``paths_with_conflict_markers`` — textual scan proving a resolution
  is real; ``git add`` on a marker-bearing file silently clears its
  unmerged state, so ``unmerged_paths`` alone is not proof.
* ``commit_merge_in_progress`` — deterministically commits a fully
  resolved in-progress merge (``git commit --no-edit``).
* ``abort_merge`` — guarded ``git merge --abort``; returns whether the
  abort actually ran and succeeded.
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
* ``worktree_lookup`` — parses ``git worktree list --porcelain`` for
  the worktree whose checked-out branch is ``refs/heads/<branch>``,
  reporting ``found`` / ``not_checked_out`` / ``query_failed`` so a
  failed query is never mistaken for "nobody holds it".
* ``worktree_for_branch`` — Optional-returning wrapper over
  ``worktree_lookup`` for callers that need no such distinction.
* ``resolve_origin_head_branch`` — strips
  ``refs/remotes/origin/`` from ``git symbolic-ref --quiet
  refs/remotes/origin/HEAD``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Canonical git conflict-marker line prefixes. Matched as line
#: PREFIXES, never substrings, so ordinary prose containing an
#: ``=======`` markdown rule is not mistaken for a conflict.
_CONFLICT_MARKER_PREFIXES: tuple[str, ...] = ("<<<<<<< ", "=======", ">>>>>>> ")

#: :func:`merge_state` verdicts. ``MERGE_STATE_UNKNOWN`` is the
#: fail-closed answer: git could not be asked, so "there is no merge
#: to abort" must NOT be inferred.
MERGE_STATE_NONE = "none"
MERGE_STATE_IN_PROGRESS = "in_progress"
MERGE_STATE_UNKNOWN = "unknown"

#: :func:`worktree_lookup` verdicts. ``WORKTREE_QUERY_FAILED`` is the
#: fail-closed answer, kept distinct from ``WORKTREE_NOT_CHECKED_OUT``
#: so a caller never treats "I could not look" as "nobody holds it".
WORKTREE_FOUND = "found"
WORKTREE_NOT_CHECKED_OUT = "not_checked_out"
WORKTREE_QUERY_FAILED = "query_failed"


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


def merge_state(repo_root: Path | str) -> str:
    """Classify the merge state of ``repo_root`` without guessing.

    Returns :data:`MERGE_STATE_IN_PROGRESS` when ``MERGE_HEAD`` exists,
    :data:`MERGE_STATE_NONE` when the git directory was read and holds
    no ``MERGE_HEAD``, and :data:`MERGE_STATE_UNKNOWN` when ``git
    rev-parse --git-dir`` itself failed.

    The third verdict is the point of this function. Collapsing it into
    ``False`` made :func:`abort_merge` a silent no-op on a repository it
    could not read, which could strand a ``MERGE_HEAD`` and block every
    later integration.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("rev-parse", "--git-dir"),
        cwd=repo_root_path,
        label="git-merge-progress:git-dir",
    )
    if result.returncode != 0:
        return MERGE_STATE_UNKNOWN
    git_dir_raw = result.stdout.strip()
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = (repo_root_path / git_dir).resolve()
    if (git_dir / "MERGE_HEAD").exists():
        return MERGE_STATE_IN_PROGRESS
    return MERGE_STATE_NONE


def merge_in_progress(repo_root: Path | str) -> bool:
    """Return True when a ``MERGE_HEAD`` exists in the git directory.

    Used by the crash-recovery preamble to detect an owned merge we
    must abort before restoring the feature branch. This is the
    positive-verdict projection of :func:`merge_state`; callers that
    must distinguish "no merge" from "could not tell" call
    :func:`merge_state` directly.
    """
    return merge_state(repo_root) == MERGE_STATE_IN_PROGRESS


def merge_target_into_current(
    repo_root: Path | str, target: str, *, keep_conflicts: bool = False
) -> MergeResult:
    """Run ``git merge --no-edit <target>`` into the current branch.

    On non-zero return code, run ``git merge --abort`` (guarded by a
    ``merge_in_progress`` precheck) and return a conflict result. On
    success return ``MergeResult(outcome='success')``. The merge is
    never force-resolved; conflict outcome is the only escape hatch
    other than success.

    With ``keep_conflicts=True`` a conflicted merge is left in
    progress (``MERGE_HEAD`` retained, conflict markers in the
    working tree) so a conflict-resolution step can repair and
    complete it; the caller owns the eventual
    :func:`commit_merge_in_progress` or :func:`abort_merge`. A merge
    that failed without starting (no ``MERGE_HEAD``) still returns
    ``'conflict'`` with nothing to keep.

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
    # Conflict path. Unless the caller asked to keep the conflicted
    # merge for resolution, abort so the working tree is left clean
    # (the caller can then record the conflict and return;
    # auto_integrate uses this to satisfy AC-07).
    if not keep_conflicts:
        abort_merge(repo_root_path)
    return MergeResult(outcome="conflict")


def unmerged_paths(repo_root: Path | str) -> list[str]:
    """Return the paths still carrying merge conflicts.

    Wraps ``git diff --name-only --diff-filter=U``. An empty list
    means every conflict has been resolved and staged; the in-progress
    merge is then safe to commit via :func:`commit_merge_in_progress`.
    A failed git invocation reports a sentinel non-empty list so a
    broken repository is never mistaken for "fully resolved".
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("diff", "--name-only", "--diff-filter=U"),
        cwd=repo_root_path,
        label="git-unmerged-paths",
    )
    if result.returncode != 0:
        return ["<unmerged-path-query-failed>"]
    return [line for line in result.stdout.splitlines() if line.strip()]


def stage_paths(repo_root: Path | str, paths: Sequence[str]) -> bool:
    """Stage the given paths with ``git add -- <paths>``.

    Returns True when every path staged cleanly. The paths are passed
    AFTER the ``--`` terminator so a filename beginning with ``-`` is
    never parsed as a git option. An empty ``paths`` sequence is a
    no-op that returns True.

    Ralph stages the resolution itself rather than requiring the
    resolving agent to run ``git add``: an agent running under Ralph's
    own MCP exec policy is denied every git invocation, so a
    resolver-side staging step could never succeed.
    """
    if not paths:
        return True
    result = run_git(
        ("add", "--", *paths),
        cwd=Path(repo_root),
        label="git-add-resolved",
    )
    return result.returncode == 0


def paths_with_conflict_markers(
    repo_root: Path | str, paths: Sequence[str]
) -> list[str]:
    """Return the subset of ``paths`` whose content still holds markers.

    ``git add`` on a file that still contains ``<<<<<<<`` markers
    silently clears its unmerged state, so an empty
    :func:`unmerged_paths` result is NOT proof that a conflict was
    really resolved. This scan closes that hole.

    A path is reported only when it holds BOTH an opening
    ``<<<<<<< `` line and a closing ``>>>>>>> `` line, so a lone
    ``=======`` separator in ordinary prose never trips it. A path
    that cannot be read as text (binary or deleted) is skipped rather
    than reported.
    """
    repo_root_path = Path(repo_root)
    reported: list[str] = []
    for path in paths:
        try:
            content = (repo_root_path / path).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        seen = {
            prefix
            for line in content.splitlines()
            for prefix in _CONFLICT_MARKER_PREFIXES
            if line.startswith(prefix)
        }
        if "<<<<<<< " in seen and ">>>>>>> " in seen:
            reported.append(path)
    return reported


def commit_merge_in_progress(repo_root: Path | str) -> bool:
    """Commit an in-progress merge with the default merge message.

    Returns True when the merge commit was created (``git commit
    --no-edit`` exited 0 and ``MERGE_HEAD`` is gone). The auto-
    integrate conflict-resolution path calls this AFTER verifying
    :func:`unmerged_paths` is empty, so the commit is deterministic —
    the resolving agent only ever stages content, never commits.
    """
    repo_root_path = Path(repo_root)
    if not merge_in_progress(repo_root_path):
        return False
    result = run_git(
        ("commit", "--no-edit"),
        cwd=repo_root_path,
        label="git-merge-commit",
    )
    return result.returncode == 0 and not merge_in_progress(repo_root_path)


def abort_merge(repo_root: Path | str) -> bool:
    """Abort an in-progress merge; report whether the abort actually ran.

    Returns True only when ``git merge --abort`` was invoked and exited
    zero. A repository with no merge in progress returns False without
    running anything — there was nothing to abort.

    The precheck is deliberately fail-closed: a
    :data:`MERGE_STATE_UNKNOWN` verdict still attempts the abort,
    because "git could not be asked" is not evidence that the working
    tree is clean. A stray operator merge is still protected, since the
    only state that skips the abort is the one where git positively
    reported no ``MERGE_HEAD``.
    """
    repo_root_path = Path(repo_root)
    state = merge_state(repo_root_path)
    if state == MERGE_STATE_NONE:
        return False
    result = run_git(
        ("merge", "--abort"),
        cwd=repo_root_path,
        label="git-merge-abort",
    )
    if result.returncode != 0:
        logger.warning(
            "git merge --abort failed in {} (merge state {}): {}",
            repo_root_path,
            state,
            result.stderr.strip() or result.stdout.strip() or "unknown error",
        )
        return False
    return True


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


def worktree_lookup(repo_root: Path | str, branch: str) -> tuple[str, Path | None]:
    """Locate the worktree holding ``refs/heads/<branch>``, fail-closed.

    Returns ``(verdict, path)`` where ``verdict`` is one of
    :data:`WORKTREE_FOUND` (``path`` is the holding worktree),
    :data:`WORKTREE_NOT_CHECKED_OUT` (git answered and no worktree holds
    the branch) or :data:`WORKTREE_QUERY_FAILED` (``git worktree list``
    itself failed). ``path`` is ``None`` for the latter two.

    Keeping the third verdict distinct is what stops the fast-forward
    from compare-and-swapping the shared mainline ref while a live
    checkout may hold it: in a linked-worktree topology the mainline
    genuinely IS checked out in a sibling worktree, and a CAS there
    advances the ref while leaving that checkout's index and working
    tree behind.

    Parses ``git worktree list --porcelain`` (the stable, scriptable
    format). When multiple worktrees have the same branch checked out
    (only possible if one of them is the primary repo and another is a
    linked worktree, which is itself illegal by git's own rules), the
    FIRST match wins. The ``branch <ref>`` field is the authoritative
    "what branch is checked out here" indicator; the ``HEAD`` field is
    just a detached SHA / refs/heads/<name> pointer and is
    intentionally NOT used to decide which branch is checked out.
    """
    repo_root_path = Path(repo_root)
    result = run_git(
        ("worktree", "list", "--porcelain"),
        cwd=repo_root_path,
        label="git-worktree-list",
    )
    if result.returncode != 0:
        logger.warning(
            "git worktree list failed in {}: {}",
            repo_root_path,
            result.stderr.strip() or result.stdout.strip() or "unknown error",
        )
        return WORKTREE_QUERY_FAILED, None
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
            return WORKTREE_FOUND, current_path
    return WORKTREE_NOT_CHECKED_OUT, None


def worktree_for_branch(repo_root: Path | str, branch: str) -> Path | None:
    """Return the worktree path that has ``refs/heads/<branch>`` checked out.

    Optional-returning wrapper over :func:`worktree_lookup`, kept for
    callers that do not need to distinguish a failed query from a
    branch that is checked out nowhere. Callers that DO need that
    distinction — the auto-integrate fast-forward and refresh paths —
    call :func:`worktree_lookup` directly.
    """
    _verdict, path = worktree_lookup(repo_root, branch)
    return path


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
    "MERGE_STATE_IN_PROGRESS",
    "MERGE_STATE_NONE",
    "MERGE_STATE_UNKNOWN",
    "WORKTREE_FOUND",
    "WORKTREE_NOT_CHECKED_OUT",
    "WORKTREE_QUERY_FAILED",
    "MergeResult",
    "abort_merge",
    "branch_exists",
    "branch_sha",
    "compare_and_swap_branch",
    "fast_forward_via_worktree",
    "is_ancestor",
    "merge_in_progress",
    "merge_state",
    "merge_target_into_current",
    "paths_with_conflict_markers",
    "reset_hard",
    "resolve_origin_head_branch",
    "stage_paths",
    "worktree_for_branch",
    "worktree_lookup",
]
