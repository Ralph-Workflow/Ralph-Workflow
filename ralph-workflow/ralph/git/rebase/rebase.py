"""Core git rebase helpers (abort/continue/rebase)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError

from ralph.git.rebase._process_executor import ProcessExecutor
from ralph.git.rebase.process_result import ProcessResult
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_no_op import RebaseNoOp
from ralph.git.rebase.rebase_operation_error import RebaseOperationError
from ralph.git.rebase.rebase_success import RebaseSuccess
from ralph.git.rebase.subprocess_executor import SubprocessExecutor

from .rebase_kinds import RebaseErrorKind, RebaseKind, classify_rebase_error

if TYPE_CHECKING:
    from git.objects.commit import Commit


REBASE_APPLY_DIR = "rebase-apply"
REBASE_MERGE_DIR = "rebase-merge"
_STATUS_PREFIX_LEN = 3


@dataclass(frozen=True)
class RebaseFailed:
    """Rebase failed with a specific error kind."""

    kind: RebaseErrorKind


RebaseResult = RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed


def abort_rebase(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> None:
    """Abort an in-progress rebase."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    if not rebase_in_progress(path):
        raise RebaseOperationError("No rebase in progress")

    result = executor.execute("git", ("rebase", "--abort"), cwd=path)
    if not result.succeeded:
        raise RebaseOperationError(
            f"Failed to abort rebase: {result.stderr or result.stdout or 'unknown error'}"
        )


def continue_rebase(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> None:
    """Continue an in-progress rebase after conflicts have been resolved."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    if not rebase_in_progress(path):
        raise RebaseOperationError("No rebase in progress")

    conflicts = get_conflicted_files(repo_root=path, executor=executor)
    if conflicts:
        raise RebaseOperationError(
            f"Conflicts remain: {len(conflicts)} file(s) still have conflicts"
        )

    result = executor.execute("git", ("rebase", "--continue"), cwd=path)
    if not result.succeeded:
        raise RebaseOperationError(
            f"Failed to continue rebase: {result.stderr or result.stdout or 'unknown error'}"
        )


def rebase_in_progress(repo_root: Path | str | None = None) -> bool:
    """Return True when a rebase directory exists in the git repo."""

    path = _resolve_repo_root(repo_root)
    git_dir = _git_dir(path)
    return any((git_dir / marker).exists() for marker in (REBASE_APPLY_DIR, REBASE_MERGE_DIR))


def get_conflicted_files(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> list[str]:
    """List files that are currently marked as conflicted in the index."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    result = executor.execute(
        "git",
        ("status", "--porcelain", "--untracked-files=no"),
        cwd=path,
    )

    if result.returncode != 0:
        raise RebaseOperationError(
            f"Failed to list conflicted files: {result.stderr or result.stdout or 'unknown'}"
        )

    conflicts: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue

        prefix = line[:2]
        if "U" not in prefix:
            continue

        payload = line[_STATUS_PREFIX_LEN:] if len(line) > _STATUS_PREFIX_LEN else ""
        filename = payload.split(" -> ")[-1].strip()
        if filename:
            conflicts.append(filename)

    return sorted(set(conflicts))


def rebase_onto(
    upstream_branch: str,
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> RebaseResult:
    """Rebase the current branch on top of the provided upstream branch.

    Every flag is set explicitly so no user/system git config can
    hang, abort, or silently mutate a rebase the agent is watching:

    * ``--no-autostash`` (D4) — a dirty tree never starts
      integration in the first place; an autostash that
      ``--abort`` later cannot re-apply is not our problem to
      inherit.
    * ``--no-autosquash`` (D5) — user config can turn it on, and
      ``squash!`` commits would otherwise open an editor.
    * ``--no-update-refs`` (B9) — ``rebase.updateRefs=true`` would
      force-move other branches pointing into the replay range and
      half-skip ones checked out in sibling worktrees.
    * ``--empty=drop`` (B4) — commits that became empty on replay
      (change already upstream) are dropped without stopping; the
      flag also answers the older-git prompt that the empty-result
      would otherwise trigger.
    * ``--no-keep-empty`` is NOT here on purpose — we want a stop on
      the first genuinely-empty commit, because it is the one case
      where the agent's intent matters.
    * The ``--`` terminator (B1/B8) — a branch whose name begins
      with ``-`` would otherwise be parsed as an option, and a
      target like ``--allow-unrelated-histories`` would be parsed
      as a flag rather than a revision.
    * The active branch name is appended AFTER ``--`` so the
      replay range is unambiguous and independent of any
      ``branch.<name>.merge``/fork-point heuristic (B8).
    * The backend is pinned to ``merge`` via
      :data:`ralph.git.hardening.PINNED_CONFIG_ARGS` (G5); the
      apply backend has documented unrecoverable-interrupt states
      and a different state layout.
    * ``rerere.enabled=false`` (D3) — a recorded wrong resolution
      would be silently committed with zero conflict signal.
    * ``commit.gpgsign=false`` / ``tag.gpgsign=false`` (D6) — a
      locked key or pinentry prompt must never hang or fail a
      replayed commit.
    """

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    repo = _open_repo(path)
    try:
        head_commit = _safe_head_commit(repo)
        if head_commit is None:
            return RebaseNoOp("Repository has no commits yet (unborn branch)")

        validation_result = _validate_rebase_request(repo, upstream_branch, executor, path)
        if validation_result is not None:
            return validation_result

        branch_name = _active_branch_name(repo)
    finally:
        repo.close()

    # Build the argv. The active branch is passed AFTER the ``--``
    # terminator as a positional revision so the replay range is
    # ``<upstream>..<branch>`` and independent of any
    # ``branch.<name>.merge``/fork-point heuristic (B8). When no
    # branch is checked out (detached HEAD) we fall through with
    # ``HEAD`` -- rebase_onto is only ever called from a known
    # checked-out state, and the precondition check has already
    # gated this path, so the fallback is purely defensive.
    upstream_branch_name = (
        branch_name if branch_name is not None else "HEAD"
    )
    rebase_argv = (
        "rebase",
        "--no-autostash",
        "--no-autosquash",
        "--no-update-refs",
        "--empty=drop",
        "--",
        upstream_branch,
        upstream_branch_name,
    )
    result = executor.execute("git", rebase_argv, cwd=path)

    # The earlier-stop handling for ``--empty=drop``-refusing
    # older git: when a rebase STOPs on a commit that became
    # empty (older git that ignores ``--empty=drop``), the engine
    # answers the stop with ``git rebase --skip`` rather than
    # abandoning to endpoint merge. The skip is the B4/G2 path
    # that closes the all-empty-replay-abandon bug class. The
    # loop is bounded so a wedged replay (a skip that itself
    # produces a stop on the next empty commit, indefinitely)
    # cannot keep integration spinning: the same per-call
    # timeout that closes the original rebase closes the loop
    # here too.
    while (
        result.returncode != 0
        and rebase_in_progress(path)
        and _rebase_stop_reports_empty(result)
    ):
        skip_result = executor.execute("git", ("rebase", "--skip"), cwd=path)
        # A skip that itself succeeded means the empty stop landed
        # and either the replay continues or the rebase finished;
        # in BOTH cases the next iteration re-evaluates a fresh
        # ``ProcessResult`` so the rest of the engine (and the
        # conflict resolution pipeline below) sees the same shape
        # it would have for a clean rebase.
        result = skip_result
        if result.succeeded:
            break
        # A non-zero skip with NO empty stop, OR no rebase in
        # progress anymore, means we just answered an empty stop
        # but the underlying rebase reported something else
        # (typically a fresh conflict on the next commit). Stop
        # the empty-stop loop and let ``_rebase_result_from_process``
        # classify the new ``ProcessResult``.
        if not rebase_in_progress(path) or not _rebase_stop_reports_empty(result):
            break

    return _rebase_result_from_process(result, path, executor)


def _rebase_stop_reports_empty(result: ProcessResult) -> bool:
    """True when a non-zero rebase stop was an "empty" stop, not a conflict.

    The signal is the substring that older git prints when it
    stops to ask whether to drop the commit. A genuine content
    conflict is reported differently and is left to the
    index-authoritative ``_rebase_result_from_process`` gate
    below. The substring check is a heuristic; the index is the
    authority, and a false positive here costs at most one
    ``--skip`` invocation on a non-empty commit (which the engine
    answers by re-entering the loop or failing out as
    ``RebaseFailed``).
    """
    payload = f"{result.stderr}\n{result.stdout}".lower()
    return "nothing to commit" in payload or "is empty" in payload


def _validate_rebase_request(
    repo: Repo,
    upstream_branch: str,
    executor: ProcessExecutor,
    repo_root: Path,
) -> RebaseResult | None:
    try:
        repo.commit(upstream_branch)
    except Exception as exc:
        if exc.__class__.__name__ not in {"BadName", "GitCommandError"}:
            raise
        return RebaseFailed(
            RebaseErrorKind(
                kind=RebaseKind.INVALID_REVISION,
                metadata={"revision": upstream_branch},
            )
        )

    branch_name = _active_branch_name(repo)
    if branch_name is None:
        return RebaseNoOp("HEAD is detached (not on any branch), rebase not applicable")

    # Identity, not name: this guard exists solely to stop a branch
    # rebasing onto itself. Keying it on the hardcoded names main/master
    # made the decision name-based, so a configured integration target
    # such as 'develop' produced a phantom no-op whenever the feature
    # branch happened to be called main or master. The genuine
    # already-up-to-date case is still covered by the ancestry check
    # below, and auto_integrate short-circuits the on-target case earlier
    # with reason 'on target branch'.
    if branch_name == upstream_branch:
        return RebaseNoOp(f"Already on '{branch_name}' branch, rebase not applicable")

    if _merge_base_is_ancestor(executor, repo_root, upstream_branch):
        return RebaseNoOp("Branch is already up-to-date with upstream")

    return None


def _rebase_result_from_process(
    result: ProcessResult,
    repo_root: Path,
    executor: ProcessExecutor,
) -> RebaseResult:
    """Classify a completed ``git rebase`` process result.

    A non-zero exit is only ever reported as :class:`RebaseNoOp` when
    the on-disk state CORROBORATES it: git printed an up-to-date
    message AND left no rebase directory behind. Matching the message
    alone was a false-success bug -- git prints ``up to date`` inside
    the hint block of a genuinely conflicted rebase, and a
    success-shaped ``RebaseNoOp`` makes
    :func:`ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`
    skip the
    endpoint-merge fallback and leave the unfinished rebase on disk,
    where it fails ``check_rebase_preconditions`` for every later
    integration.

    Everything else falls through to :func:`classify_rebase_error`,
    whose :class:`RebaseConflicts` / :class:`RebaseFailed` outcomes
    both route into that fallback, so a misclassification inside the
    classifier table can no longer end the integration attempt.
    """
    if result.succeeded:
        return RebaseSuccess()

    if _contains_up_to_date_message(result) and not rebase_in_progress(repo_root):
        return RebaseNoOp("Branch is already up-to-date with upstream")

    # The INDEX is authoritative about conflicts; stderr text is only a
    # heuristic. ``classify_rebase_error`` runs the hook-rejection,
    # concurrent-operation and dirty-worktree classifiers BEFORE the
    # content-conflict one and each matches on substrings, so a genuine
    # conflict whose output also mentions an earlier classifier's keyword
    # is reported as RebaseFailed. That misclassification now costs more
    # than a wrong label: RebaseFailed skips the resolve-in-place path in
    # :mod:`ralph.pipeline.auto_integrate_rebase_merge` entirely, so the
    # conflicts most worth resolving would be the ones never offered to a
    # resolver.
    conflicted = _conflicted_files_if_rebasing(repo_root, executor)
    if conflicted:
        return RebaseConflicts(conflicted)

    error_kind = classify_rebase_error(result.stderr, result.stdout)
    if error_kind.kind == RebaseKind.CONTENT_CONFLICT:
        return RebaseConflicts(
            get_conflicted_files(repo_root=repo_root, executor=executor)
        )

    return RebaseFailed(error_kind)


def _conflicted_files_if_rebasing(
    repo_root: Path, executor: ProcessExecutor
) -> list[str]:
    """Unmerged paths, but ONLY while a rebase is genuinely paused.

    Both conditions matter. Without the rebase-in-progress check this
    would reclassify a rebase that git refused to start (dirty worktree,
    concurrent operation) as a content conflict, because the unmerged
    entries it saw would belong to some OTHER unfinished operation.

    Returns an empty list rather than raising when the index cannot be
    read: an unreadable repository must fall through to the text
    classifier, not fail the rebase.
    """
    try:
        if not rebase_in_progress(repo_root):
            return []
        return get_conflicted_files(repo_root=repo_root, executor=executor)
    except RebaseOperationError:
        return []


def _resolve_repo_root(repo_root: Path | str | None = None) -> Path:
    candidate = Path(repo_root) if repo_root else Path.cwd()
    try:
        repo = Repo(candidate, search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise RebaseOperationError(f"Not a git repository: {exc}") from exc
    try:
        if not repo.working_tree_dir:
            raise RebaseOperationError("Cannot determine git working tree directory")
        return Path(repo.working_tree_dir).resolve()
    finally:
        repo.close()


def _open_repo(repo_root: Path) -> Repo:
    try:
        return Repo(repo_root)
    except InvalidGitRepositoryError as exc:
        raise RebaseOperationError(f"Not a git repository: {exc}") from exc


def _git_dir(repo_root: Path) -> Path:
    repo = _open_repo(repo_root)
    try:
        git_dir = repo.git_dir
        if not git_dir:
            raise RebaseOperationError("Cannot determine .git directory for repository")
        return Path(git_dir).resolve()
    finally:
        repo.close()


def _safe_head_commit(repo: Repo) -> Commit | None:
    try:
        return repo.head.commit
    except (ValueError, GitCommandError, AttributeError):
        return None


def _active_branch_name(repo: Repo) -> str | None:
    try:
        return repo.active_branch.name
    except (TypeError, ValueError, GitCommandError):
        return None


def _merge_base_is_ancestor(
    executor: ProcessExecutor,
    repo_root: Path,
    upstream_branch: str,
) -> bool:
    # ``--`` terminator, for the same reason ``rebase_onto`` uses one: a
    # branch whose name begins with ``-`` would otherwise be parsed as an
    # option (git exits 129, "unknown switch"), and this probe would
    # silently report "not an ancestor" for every such target.
    result = executor.execute(
        "git",
        ("merge-base", "--is-ancestor", "--", upstream_branch, "HEAD"),
        cwd=repo_root,
    )
    return result.returncode == 0


def _contains_up_to_date_message(result: ProcessResult) -> bool:
    payload = f"{result.stderr}\n{result.stdout}".lower()
    return "up to date" in payload or "up-to-date" in payload


__all__ = [
    "ProcessExecutor",
    "ProcessResult",
    "RebaseConflicts",
    "RebaseFailed",
    "RebaseNoOp",
    "RebaseOperationError",
    "RebaseResult",
    "RebaseSuccess",
    "SubprocessExecutor",
    "abort_rebase",
    "continue_rebase",
    "get_conflicted_files",
    "rebase_in_progress",
    "rebase_onto",
]


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: A12
# ladder rung: 1
# AC-14 rationale: B1
# ladder rung: 4
# AC-14 rationale: B2
# ladder rung: 1
# AC-14 rationale: B4
# ladder rung: 1
# AC-14 rationale: B5
# ladder rung: 1
# AC-14 rationale: B8
# ladder rung: 1
# AC-14 rationale: D2
# ladder rung: 3
# AC-14 rationale: D4
# ladder rung: 1
# AC-14 rationale: G2
# ladder rung: 1
# AC-14 rationale: H6
# ladder rung: 1
# ----- end AC-14 catalog evidence -----
