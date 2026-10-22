"""Git operations for ralph pipeline via GitPython.

This module provides a high-level interface for git operations,
wrapping GitPython to provide the functionality needed by the pipeline.

Serialized git queue:

Every git interaction this module performs -- the ``run_git`` subprocess
path AND every ``repo.git.*`` dispatch (the ``git status`` fallback in
:func:`list_changed_paths`, ``git add`` in :func:`stage_all` /
:func:`stage_files`, ``git push`` in :func:`push`) -- is dispatched
through :mod:`ralph.git.git_command_queue`. GitPython takes no
process-wide repository lock, so an unserialized ``git add`` can race a
sibling ``git commit`` on ``.git/index``; routing both through the same
per-common-dir queue makes them mutually exclusive and gives them one
shared health/backoff signal. :func:`_queued_git_call` is the single
GitPython dispatch point; :func:`_queued_remote_push` covers
``Remote.push``, which drives git through GitPython's own dispatch rather
than through the router.

Stale Git Lock Recovery:
Operations that mutate git state run through
``_run_git_operation_with_stale_lock_recovery``. When encountering git lock
contention (such as ``index.lock``, ``HEAD.lock``, or ``packed-refs.lock``),
recovery uses an age-and-liveness gate:

* Conservative 10s age threshold (``_STALE_GIT_LOCK_AGE_SECONDS``):
  Git lock files do NOT record the PID of the holding process (correcting
  a false claim that Git writes holding PIDs into lock files). Because lock
  files cannot identify their holder by content, locks younger than 10s
  cannot be safely unlinked and are refused removal.
* Negative liveness proof via OR predicate: verified across multiple
  independent process signals (proc fd table, proc cwd in worktree/gitdirs,
  proc environ GIT_DIR, lsof, and fuser). If any signal indicates a live
  process or liveness cannot be verified ("unknown"), removal is refused
  fail-closed.
* Bounded 0.5s ThreadPoolExecutor probe: liveness checks run inside a worker
  thread with a strict 0.5s timeout (``_PROBE_TIMEOUT_S``) to prevent
  unbounded hangs.
* Bounded backoff: when a lock cannot be unlinked due to contention, retries
  with bounded backoff (0.2s, 0.5s, 1.0s; total ≤ 1.7s) before re-raising.
* Reducer integration: persistent failures trigger in-place reducer retry
  when policy defines no recovery phase, preserving the validated commit message.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.git._lock_recovery import LivenessProbeFn

from git import Actor, InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git._lock_recovery import (
    _LOCK_BACKOFF_S as _BASE_LOCK_BACKOFF_S,
)
from ralph.git._lock_recovery import (
    _PROBE_TIMEOUT_S,
)
from ralph.git._lock_recovery import (
    _STALE_GIT_LOCK_AGE_SECONDS as _BASE_STALE_GIT_LOCK_AGE_SECONDS,
)
from ralph.git._lock_recovery import (
    _find_lock_holder as _base_find_lock_holder,
)
from ralph.git._lock_recovery import (
    _lock_has_no_live_holder as _base_lock_has_no_live_holder,
)
from ralph.git._lock_recovery import (
    _probe_lsof_fuser as _base_probe_lsof_fuser,
)
from ralph.git._lock_recovery import (
    _recover_stale_git_lock as _base_recover_stale_git_lock,
)
from ralph.git._lock_recovery import (
    _run_git_operation_with_stale_lock_recovery as _base_run_git_operation_with_stale_lock_recovery,
)
from ralph.git._lock_recovery import (
    _sleep_fn as _base_sleep_fn,
)
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
from ralph.git.errors import GitOperationError
from ralph.git.git_command_queue import (
    _classify_exception as _queue_outcome_kind,
)
from ralph.git.git_command_queue import (
    get_queue_for_repo,
    route_git_method,
)
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import GitRunOptions, run_git

_PORCELAIN_STATUS_PREFIX_LEN = 3
_REFLOG_DEST_PARTS = 2
_RALPH_WORKFLOW_COAUTHOR_TRAILER = "Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


@dataclass(frozen=True)
class _RepoQueueView:
    """Structural ``Repo`` stand-in carrying the queue-routing seam attributes.

    :func:`~ralph.git.git_command_queue.route_git_method` reads exactly two
    attributes off the object it is handed: ``git`` (the dynamic
    ``repo.git.<method>`` dispatch handle) and ``git_dir`` (the
    per-repository queue key). A live :class:`~git.Repo` carries both and is
    passed through untouched. A duck-typed ``Repo`` double -- the shape the
    operations tests use to stay off the git binary -- carries only ``git``,
    so this view supplies the key from the repo root the caller already
    holds. Both shapes normalize to the SAME common-dir, so the double
    serializes against the live repository exactly as the real object does.
    """

    git: object
    git_dir: str


def _is_queue_view(candidate: object) -> bool:
    """Return whether ``candidate`` is a queue-routing view wrapper."""
    return isinstance(candidate, _RepoQueueView)


def _queue_routing_target(repo_root: Path | str, repo: Repo) -> Repo:
    """Return the object whose git-dir identifies this repository's queue.

    Live :class:`~git.Repo` objects are returned unchanged (their ``git_dir``
    is already the canonical worktree git-dir). Anything else is wrapped in
    :class:`_RepoQueueView` keyed on ``repo_root``, so the queue key never
    depends on a double exposing ``git_dir``.
    """
    if not _is_queue_view(repo) and not hasattr(repo, "git_dir"):
        view = _RepoQueueView(git=repo.git, git_dir=str(repo_root))
        return cast(
            "Repo", view
        )  # cast-policy: seam: structural boundary (queue-routing view exposes exactly the two attributes route_git_method reads)
    return repo


def _queued_git_call(
    repo_root: Path | str,
    repo: Repo,
    method_name: str,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> object:
    """Run ``repo.git.<method_name>(...)`` inside the shared git queue.

    Routing every GitPython dispatch in this module through
    :func:`~ralph.git.git_command_queue.route_git_method` is what makes a
    ``git add`` / ``git status`` issued here serialize against the
    ``run_git`` subprocess path: both resolve the same per-common-dir
    :class:`~ralph.git.git_command_queue.GitCommandQueue` and therefore share
    one lock and one health/backoff signal. Return value and exception
    behaviour are exactly the underlying dispatch's.
    """
    return route_git_method(_queue_routing_target(repo_root, repo), method_name, args, kwargs)


def _queued_remote_push(repo_root: Path | str, repo: Repo, remote: str, branch: str) -> None:
    """Push ``branch`` to ``remote`` while holding the shared git queue lock.

    :meth:`git.Remote.push` drives ``git push`` through GitPython's own
    dispatch rather than through :func:`route_git_method`, so the lock -- and
    the outcome this repository's shared health signal records -- is taken
    here. The queue is keyed on ``repo_root`` for the same reason
    :func:`_queue_routing_target` supplies a key: the caller may hold a
    duck-typed ``Repo`` double.
    """
    queue = get_queue_for_repo(Path(repo_root))
    with queue.execute_lock:
        try:
            repo.remote(remote).push(branch)
        except BaseException as exc:
            queue.record_outcome(_queue_outcome_kind(exc))
            raise
        queue.record_outcome("success")


# Test seams overrideable via monkeypatch
_LOCK_BACKOFF_S: tuple[float, ...] = _BASE_LOCK_BACKOFF_S
_STALE_GIT_LOCK_AGE_SECONDS: float = _BASE_STALE_GIT_LOCK_AGE_SECONDS
_sleep_fn: Callable[[float], None] = _base_sleep_fn
_probe_lsof_fuser: Callable[[Path, float], bool] = _base_probe_lsof_fuser
_find_lock_holder: Callable[[Path], str] = _base_find_lock_holder


def _lock_has_no_live_holder(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None = None,
    *,
    probe_timeout_s: float = _PROBE_TIMEOUT_S,
    _proc_root: Path = Path("/proc"),
) -> Literal["live", "dead", "unknown"]:
    return _base_lock_has_no_live_holder(
        lock_path,
        gitdir,
        gitdir_common,
        probe_timeout_s=probe_timeout_s,
        _proc_root=_proc_root,
        probe_lsof_fuser_fn=_probe_lsof_fuser,
    )


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    liveness_fn: LivenessProbeFn | None = None,
) -> bool:
    return _base_recover_stale_git_lock(
        operation,
        error,
        liveness_fn=liveness_fn if liveness_fn is not None else _lock_has_no_live_holder,
    )


def _run_git_operation_with_stale_lock_recovery[T](
    operation: str,
    action: Callable[[], T],
) -> T:
    return _base_run_git_operation_with_stale_lock_recovery(
        operation,
        action,
        recover_fn=_recover_stale_git_lock,
        sleep_fn=_sleep_fn,
        holder_fn=_find_lock_holder,
    )


def _append_ralph_workflow_coauthor_trailer(message: str) -> str:
    stripped_message = message.rstrip()
    if not stripped_message:
        return message
    lowered_lines = {line.strip().lower() for line in stripped_message.splitlines()}
    if _RALPH_WORKFLOW_COAUTHOR_TRAILER.lower() in lowered_lines:
        return stripped_message
    return f"{stripped_message}\n\n{_RALPH_WORKFLOW_COAUTHOR_TRAILER}"


def _resolve_commit_identity(
    repo: Repo, author_name: str | None, author_email: str | None
) -> tuple[str, str]:
    if author_name and author_email:
        return author_name, author_email
    try:
        config = repo.config_reader()
        return (
            author_name or str(config.get_value("user", "name", "Ralph")),
            author_email or str(config.get_value("user", "email", "ralph@ai")),
        )
    except Exception:
        return author_name or "Ralph", author_email or "ralph@ai"


def _run_commit_hooks(
    repo_root: Path,
    hooks_dir: Path,
    message_path: Path,
    hook_env: dict[str, str],
) -> None:
    hook_specs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("pre-commit", ()),
        ("prepare-commit-msg", (str(message_path), "message")),
        ("commit-msg", (str(message_path),)),
    )
    for hook_name, hook_args in hook_specs:
        if not (hooks_dir / hook_name).exists():
            continue
        hook_command: tuple[str, ...] = ("hook", "run", hook_name)
        if hook_args:
            hook_command += ("--", *hook_args)
        hook_result = run_git(
            hook_command,
            cwd=repo_root,
            label=f"git-hook-{hook_name}",
            options=GitRunOptions(env=hook_env),
        )
        if hook_result.returncode != 0:
            raise GitOperationError("create_commit", hook_result.stderr.strip())


def find_repo_root(start: Path | str = Path()) -> Path:
    """Locate git repo root from start path.

    Args:
        start: Starting path for the search.

    Returns:
        Path to the repository root.

    Raises:
        GitOperationError: If not inside a git repository.
    """
    repo: Repo | None = None
    try:
        repo = Repo(start, search_parent_directories=True)
        if repo.working_tree_dir:
            return Path(repo.working_tree_dir).resolve()
        return Path(repo.working_dir).resolve()
    except InvalidGitRepositoryError as exc:
        raise GitOperationError("find_repo_root", "Not inside a git repository") from exc
    finally:
        _close_repo(repo)


def find_main_worktree_root(start: Path | str = Path()) -> Path:
    """Find the primary worktree root for the current repository.

    For linked worktrees, this resolves to the main checkout that owns the
    shared git common directory. For ordinary repositories, it matches the
    active repository root.

    This helper detects linked git worktrees only as a workspace-root resolver
    and is NEVER used by the same-workspace parallel worker path. Parallel v1
    workers always share the canonical repo_root; this function MUST NOT be
    invoked by ``ralph.pipeline.parallel.*`` modules. Callers in that package
    violate the same-workspace isolation contract.
    """
    repo: Repo | None = None
    try:
        repo = Repo(start, search_parent_directories=True)
        common_dir = Path(repo.common_dir).resolve()
        return common_dir.parent.resolve()
    except InvalidGitRepositoryError as exc:
        raise GitOperationError("find_main_worktree_root", "Not inside a git repository") from exc
    finally:
        _close_repo(repo)


def is_repo_clean(repo_root: Path | str) -> bool:
    """Check if repository has uncommitted changes.

    Args:
        repo_root: Path to the repository root.

    Returns:
        True if repository is clean (no uncommitted changes).
    """
    repo_root_path = Path(repo_root)
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
            cwd=repo_root_path,
            label="git-status",
        )
        if result.returncode == 0:
            return not bool(result.stdout.splitlines())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return not repo.is_dirty()
    finally:
        _close_repo(repo)


def has_uncommitted_changes(repo_root: Path | str) -> bool:
    """Return True when the working tree has uncommitted work.

    Includes staged diff, unstaged diff, and untracked files. This is the
    authoritative skip check for commit phases: if this returns False, there
    is literally nothing for a commit agent to package up.
    """
    repo_root_path = Path(repo_root)
    try:
        result = run_git(("status", "--porcelain"), cwd=repo_root_path, label="git-status")
        if result.returncode == 0:
            return bool(result.stdout.splitlines())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return repo.is_dirty(untracked_files=True)
    finally:
        _close_repo(repo)


def has_commits_since(repo_root: Path | str, baseline_sha: str | None) -> bool:
    """Return True when HEAD is ahead of ``baseline_sha``.

    When ``baseline_sha`` is None the caller has no prior baseline (first run),
    so we conservatively return True to allow the caller to proceed.
    """
    if baseline_sha is None:
        return True
    repo_root_path = Path(repo_root)
    try:
        result = run_git(
            ("rev-list", "--max-count=1", f"{baseline_sha}..HEAD"),
            cwd=repo_root_path,
            label="git-rev-list",
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return any(True for _ in repo.iter_commits(f"{baseline_sha}..HEAD"))
    except Exception:
        return True
    finally:
        _close_repo(repo)


def has_staged_changes(repo_root: Path | str) -> bool:
    """Check if repository has staged changes.

    Args:
        repo_root: Path to the repository root.

    Returns:
        True if there are staged changes. Untracked-only worktrees
        return False -- this helper is the staged-only contract used by
        ``commit`` to decide whether a commit can proceed.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            return bool(repo.index.diff("HEAD"))
        finally:
            _close_repo(repo)
    return any(_line_is_staged(line) for line in status_lines)


def list_changed_paths(repo_root: Path | str) -> list[str]:
    """Return unique dirty paths from ``git status --porcelain`` in output order.

    Commit-message scopes are resolved before their paths are staged, so this
    deliberately includes both staged and unstaged changes (including
    untracked files). ``get_staged_files`` remains the staged-only API.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            status_output = cast(
                "str", _queued_git_call(repo_root, repo, "status", ("--porcelain",))
            )  # cast-policy: seam: structural boundary (git stdout probe; CLI returns plain text)
            status_lines = status_output.splitlines()
        finally:
            _close_repo(repo)

    changed_paths: list[str] = []
    for line in status_lines:
        if len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    return changed_paths


def get_staged_files(repo_root: Path | str) -> list[str]:
    """Get list of staged files.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of staged file paths. Untracked-only worktrees return ``[]``.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            staged = repo.index.diff("HEAD")
            return [item.a_path for item in staged if item.a_path] if staged else []
        finally:
            _close_repo(repo)

    staged_paths: list[str] = []
    for line in status_lines:
        if not _line_is_staged(line) or len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in staged_paths:
            staged_paths.append(path)
    return staged_paths


def _line_is_staged(line: str) -> bool:
    """Return True iff ``line`` (from ``git status --porcelain``) represents a staged change.

    Untracked files (``??``) are explicitly excluded -- the staged-only
    contract does NOT include them. ``has_uncommitted_changes`` is the
    API for the broader dirty-state check.
    """
    if not line:
        return False
    if line.startswith("??"):
        return False
    return line[0] not in {" ", "?"}


def _git_status_porcelain_lines(repo_root: Path) -> list[str]:
    """Run ``git status --porcelain`` and return the split output lines.

    Raises:
        GitOperationError: when ``git status --porcelain`` exits with a
            non-zero return code. Earlier versions silently collapsed a
            non-zero return into ``[]`` which made downstream helpers
            (``has_staged_changes`` / ``list_changed_paths`` /
            ``get_staged_files``) report a false-clean repo. The
            failure now surfaces so callers can route through the
            GitPython fallback path or fail loud.
    """
    result = run_git(("status", "--porcelain"), cwd=repo_root, label="git-status")
    if result.returncode != 0:
        raise GitOperationError(
            "git-status",
            f"git status --porcelain failed (rc={result.returncode}): "
            f"{result.stderr.strip() or '<no stderr>'}",
        )
    return result.stdout.splitlines()


def stage_all(repo_root: Path | str) -> None:
    """Stage all changes (git add -A).

    Args:
        repo_root: Path to the repository root.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_repo = repo

        def _stage() -> None:
            # Index mutation: ``git add -A`` rewrites the SHARED index, so it
            # runs inside the per-repository queue lock. Two producers staging
            # the same repository -- or a ``git status`` racing the stage --
            # are serialized instead of interleaving on ``.git/index``.
            _ = _queued_git_call(repo_root, active_repo, "add", kwargs={"A": True})

        _run_git_operation_with_stale_lock_recovery("stage_all", _stage)
        logger.debug("Staged all changes in {}", repo_root)
    except Exception as exc:
        raise GitOperationError("stage_all", str(exc)) from exc
    finally:
        _close_repo(repo)


def stage_files(repo_root: Path | str, files: list[str]) -> None:
    """Stage only the provided repository-relative paths.

    Uses ``git add --all -- <paths>`` so modified, untracked, and deleted files
    are all handled consistently for the selected scope.
    """
    if not files:
        logger.debug("No files requested for selective staging in {}", repo_root)
        return
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_repo = repo

        def _stage() -> None:
            # As with ``stage_all``: the selective add shares the index, so it
            # runs inside the per-repository queue lock and cannot interleave
            # with a sibling stage or a concurrent ``git status``/``git commit``.
            _ = _queued_git_call(repo_root, active_repo, "add", ("--all", "--", *files))

        _run_git_operation_with_stale_lock_recovery("stage_files", _stage)
        logger.debug("Staged {} selected paths in {}", len(files), repo_root)
    except Exception as exc:
        raise GitOperationError("stage_files", str(exc)) from exc
    finally:
        _close_repo(repo)


def _current_head_sha(repo_root: Path) -> str | None:
    """Return the SHA at HEAD or ``None`` if HEAD cannot be resolved."""
    result = run_git(
        ("rev-parse", "--verify", "HEAD"),
        cwd=repo_root,
        label="git-rev-parse-head",
    )
    if result.returncode != 0:
        return None
    head_sha = result.stdout.strip()
    return head_sha or None


def _resolve_detached_head_reattach_branch(repo_root: Path) -> str | None:
    """Find a branch name to safely reattach a detached HEAD to.

    Resolves the same way the commit reattach fallback does (reflog,
    worktree metadata, branches whose tip equals HEAD). The
    authoritative resolver lives in
    :mod:`ralph.pipeline.commit_executor`; this helper provides the
    operations-layer fallback so :func:`create_commit` can reattach
    a detached HEAD discovered inside the seam itself, without taking
    a dependency on the pipeline module.

    Search order:

    1. ``git for-each-ref --points-at=HEAD refs/heads/`` -- returns any
       branch whose tip equals HEAD. This is the exact-tip match that
       ``git symbolic-ref HEAD refs/heads/<branch>`` cannot clobber.
    2. The raw ``.git/HEAD`` file when it still contains
       ``ref: refs/heads/<branch>`` (the residual written back by
       ``git checkout <branch>`` prior to the detach).
    3. The reflog's most recent ``checkout: moving from ...`` /
       ``switch branch to ...`` destination.

    Returns ``None`` when no safe branch can be resolved.
    """
    head_sha = _current_head_sha(repo_root)
    if not head_sha:
        return None

    points_at_result = run_git(
        (
            "for-each-ref",
            f"--points-at={head_sha}",
            "refs/heads/",
            "--format=%(refname:short)",
        ),
        cwd=repo_root,
        label="git-for-each-ref-points-at-head",
    )
    if points_at_result.returncode == 0:
        for line in points_at_result.stdout.splitlines():
            branch = line.strip()
            if branch:
                return branch

    head_file = Path(repo_root) / ".git" / "HEAD"
    if head_file.is_file():
        try:
            content = head_file.read_text(encoding="utf-8").strip()
        except OSError:
            content = ""
        if content.startswith("ref: refs/heads/"):
            candidate = content.removeprefix("ref: refs/heads/").strip()
            if candidate:
                return candidate

    reflog_result = run_git(
        ("reflog", "-n", "50", "--format=%gs"),
        cwd=repo_root,
        label="git-reflog-detached-head-reattach",
    )
    if reflog_result.returncode != 0:
        return None
    for line in reflog_result.stdout.splitlines():
        for marker in ("switch branch to ", "checkout: moving from "):
            idx = line.find(marker)
            if idx == -1:
                continue
            rest = line[idx + len(marker) :]
            parts = rest.split(" to ", 1)
            if len(parts) == _REFLOG_DEST_PARTS:
                dest = parts[1].strip()
                if dest:
                    return dest
    return None


def _head_tree_sha(repo_root: Path, head_sha: str) -> str | None:
    """Return the tree SHA at ``head_sha``, or ``None`` if not resolvable."""
    result = run_git(
        ("rev-parse", "--verify", f"{head_sha}^{{tree}}"),
        cwd=repo_root,
        label="git-rev-parse-head-tree",
    )
    if result.returncode != 0:
        return None
    tree_sha = result.stdout.strip()
    return tree_sha or None


def _tree_matches_head(repo_root: Path, current_head_sha: str, candidate_tree: str) -> bool:
    """True iff HEAD's tree already equals the candidate tree (content committed)."""
    if not current_head_sha:
        return False
    head_tree = _head_tree_sha(repo_root, current_head_sha)
    return head_tree is not None and head_tree == candidate_tree


def _reattach_or_resolve_ref_name(repo_root: Path) -> str:
    """Resolve the ref name to commit onto, reattaching a detached HEAD first.

    Detached HEAD at the seam (AC8) is repaired here -- not failed: the
    feature branch is resolved from branches pointing at HEAD, the
    ``.git/HEAD`` residual, or the reflog, then ``HEAD`` is reattached
    atomically so the commit lands on the feature branch instead of
    orphaning the work.
    """
    ref_result = run_git(
        ("symbolic-ref", "--quiet", "HEAD"),
        cwd=repo_root,
        label="git-symbolic-ref-head",
    )
    if ref_result.returncode == 0 and ref_result.stdout.strip():
        return ref_result.stdout.strip()
    reattached_ref = _resolve_detached_head_reattach_branch(repo_root)
    if not reattached_ref:
        raise GitOperationError(
            "create_commit",
            "HEAD is detached and no feature branch to commit onto",
        )
    atomic_ref = run_git(
        ("update-ref", "-m", "ralph reattach detached HEAD", "HEAD", reattached_ref),
        cwd=repo_root,
        label="git-update-ref-head-reattach",
        options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph create_commit"}),
    )
    if atomic_ref.returncode != 0:
        detail = atomic_ref.stderr.strip() or "git update-ref HEAD reattach failed"
        raise GitOperationError("create_commit", detail)
    logger.info("Reattached detached HEAD to branch '{}'", reattached_ref)
    return reattached_ref


def _run_prepare_commit_hooks(repo_root: Path, repo_git_dir: str, message_path: Path) -> str:
    """Run pre-commit hooks over ``message_path`` and return the final message."""
    hook_env = {"GIT_REFLOG_ACTION": "ralph create_commit"}
    hooks_dir = Path(repo_git_dir) / "hooks"
    _run_commit_hooks(repo_root, hooks_dir, message_path, hook_env)
    # filesystem-read-ok: Git hooks mutate this owned temporary commit-message file in place
    return message_path.read_text(encoding="utf-8")


def _candidate_commit_tree(repo_root: Path) -> str:
    """Write the index as a tree object and return its SHA."""
    tree_result = run_git(("write-tree",), cwd=repo_root, label="git-write-tree")
    if tree_result.returncode != 0:
        raise GitOperationError("create_commit", tree_result.stderr.strip())
    return tree_result.stdout.strip()


def _cas_publish_or_detect_advance(
    repo_root: Path,
    ref_name: str,
    candidate_sha: str,
    expected_head: str,
    hooks_dir: Path | None,
    hook_env: dict[str, str],
) -> CommitCreationResult:
    """CAS-publish ``candidate_sha`` onto ``ref_name``; detect concurrent advance.

    On a CAS failure the current HEAD is re-read: when it moved past
    ``expected_head`` the commit was created concurrently (or Ralph
    already advanced the branch) and the result is idempotent
    ``ALREADY_ADVANCED`` rather than an error -- preventing a duplicate
    commit and a commit/cleanup loop on re-dispatch.
    """
    update_ref = run_git(
        ("update-ref", ref_name, candidate_sha, expected_head),
        cwd=repo_root,
        label="git-update-ref-cas",
        options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph create_commit"}),
    )
    if update_ref.returncode != 0:
        current_head = get_head_sha(repo_root)
        if current_head != expected_head:
            return CommitCreationResult.already_advanced(current_head)
        detail = update_ref.stderr.strip() or "git update-ref failed without diagnostics"
        raise GitOperationError("create_commit", detail)
    if hooks_dir is not None:
        post_commit = hooks_dir / "post-commit"
        if post_commit.exists():
            post_result = run_git(
                ("hook", "run", "post-commit"),
                cwd=repo_root,
                label="git-hook-post-commit",
                options=GitRunOptions(env=hook_env),
            )
            if post_result.returncode != 0:
                logger.warning(
                    "post-commit hook failed after commit {}: {}",
                    candidate_sha[:8],
                    post_result.stderr.strip() or "no diagnostics",
                )
    return CommitCreationResult.created(candidate_sha)


def create_commit(
    repo_root: Path | str,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
    expected_head: str,
) -> CommitCreationResult:
    """Create a git commit.

    Args:
        repo_root: Path to the repository root.
        message: Commit message.
        author_name: Optional author name override.
        author_email: Optional author email override.
        expected_head: HEAD SHA required for the ref compare-and-swap.

    Returns:
        Typed result describing commit creation.

    Raises:
        GitOperationError: If commit fails.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        message = _append_ralph_workflow_coauthor_trailer(message)
        # Every git invocation in this function (the detached-HEAD reattach,
        # the tree-based idempotency short-circuit, the commit-tree + CAS
        # publish) already runs through ``run_git`` -> ``route_run_git``, so
        # the whole body is serialized on this repository's shared queue. The
        # steps stay in that order: the idempotency short-circuit must observe
        # HEAD's tree and the candidate tree inside the same serialized run.
        current_head_sha = repo.head.commit.hexsha if hasattr(repo, "head") else ""
        if not hasattr(repo, "git_dir"):
            if current_head_sha and current_head_sha != expected_head:
                return CommitCreationResult.already_advanced(current_head_sha)
            author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)
            actor = Actor(author_name, author_email)
            commit = _run_git_operation_with_stale_lock_recovery(
                "create_commit",
                lambda: repo.index.commit(message, author=actor, committer=actor),
            )
            return CommitCreationResult.created(commit.hexsha)
        author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)

        ref_name = _reattach_or_resolve_ref_name(Path(repo_root))

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as message_file:
            message_file.write(message)
            message_path = Path(message_file.name)
        try:
            message = _run_prepare_commit_hooks(Path(repo_root), str(repo.git_dir), message_path)
        finally:
            message_path.unlink(  # filesystem-write-ok: transient hook message scratch
                missing_ok=True
            )

        candidate_tree_sha = _candidate_commit_tree(Path(repo_root))

        # Idempotency short-circuit (AC11): a commit "already exists" iff the
        # content is already in place -- HEAD's tree equals the tree we are
        # about to commit. Matching by tree content (not by SHA) covers both
        # re-dispatch after an interrupt (HEAD still == expected_head) and a
        # commit Ralph rewrote or moved (rebase / cherry-pick / amend changed
        # the SHAs without changing the tree). A SHA-only match must NEVER
        # short-circuit on its own: after a detached-HEAD reattach the
        # reattached branch tip equals the pre-stage ``expected_head`` while
        # the index still holds pending changes -- short-circuiting there
        # dropped the work and routed the phase to ``failed_terminal``.
        if _tree_matches_head(Path(repo_root), current_head_sha, candidate_tree_sha):
            return CommitCreationResult.already_advanced(current_head_sha)

        commit_result = run_git(
            ("commit-tree", candidate_tree_sha, "-p", expected_head),
            cwd=Path(repo_root),
            label="git-commit-tree",
            options=GitRunOptions(
                env={
                    "GIT_AUTHOR_NAME": author_name,
                    "GIT_AUTHOR_EMAIL": author_email,
                    "GIT_COMMITTER_NAME": author_name,
                    "GIT_COMMITTER_EMAIL": author_email,
                    "GIT_REFLOG_ACTION": "ralph create_commit",
                },
                input_data=message,
            ),
        )
        if commit_result.returncode != 0:
            raise GitOperationError("create_commit", commit_result.stderr.strip())
        candidate_sha = commit_result.stdout.strip()
        result = _cas_publish_or_detect_advance(
            Path(repo_root),
            ref_name,
            candidate_sha,
            expected_head,
            Path(repo.git_dir) / "hooks",
            {"GIT_REFLOG_ACTION": "ralph create_commit"},
        )
        if result.status is CommitCreationStatus.CREATED:
            logger.info(
                "Created commit {}: {}",
                candidate_sha[:8],
                message.splitlines()[0] if message else "(no message)",
            )
        return result
    except Exception as exc:
        raise GitOperationError("create_commit", str(exc)) from exc
    finally:
        _close_repo(repo)


def push(
    repo_root: Path | str,
    remote: str,
    branch: str | None = None,
) -> None:
    """Push current branch to remote.

    Args:
        repo_root: Path to the repository root.
        remote: Remote name to push to.
        branch: Optional branch name override.

    Raises:
        GitOperationError: If push fails.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_branch = branch or repo.active_branch.name
        repo.remote(remote)
        # Publish idempotency (AC11): skip the push when the local SHA equals
        # the remote-tracking SHA. A re-dispatch, an interrupted run, or a
        # repeated fan-out must not re-issue a non-fast-forward push.
        local_sha_result = run_git(
            ("rev-parse", "--verify", active_branch),
            cwd=Path(repo_root),
            label="git-rev-parse-local-branch",
        )
        local_sha = local_sha_result.stdout.strip() if local_sha_result.returncode == 0 else ""
        remote_tracking_ref = f"refs/remotes/{remote}/{active_branch}"
        remote_sha_result = run_git(
            ("rev-parse", "--verify", remote_tracking_ref),
            cwd=Path(repo_root),
            label="git-rev-parse-remote-tracking",
        )
        remote_sha = remote_sha_result.stdout.strip() if remote_sha_result.returncode == 0 else ""
        if local_sha and remote_sha and local_sha == remote_sha:
            logger.info(
                "Push of {} to {}/{} skipped: local sha matches remote-tracking sha",
                active_branch,
                remote,
                active_branch,
            )
            return
        # The already-pushed short-circuit stays ABOVE the queue: a re-dispatch
        # that finds the local and remote-tracking SHAs already in sync never
        # re-issues a push. The push itself is taken through the shared queue
        # so it cannot interleave with a concurrent git call on this repository.
        _queued_remote_push(repo_root, repo, remote, active_branch)
        logger.info("Pushed {} to {}/{}", active_branch, remote, active_branch)
    except Exception as exc:
        raise GitOperationError("push", str(exc)) from exc
    finally:
        _close_repo(repo)


def get_head_sha(repo_root: Path | str) -> str:
    """Return current HEAD commit SHA.

    Args:
        repo_root: Path to the repository root.

    Returns:
        SHA of the current HEAD commit.

    Raises:
        GitOperationError: If HEAD cannot be read.

    The wrapping is deliberate and matches :func:`find_repo_root` and
    :func:`merge_base` in this module. Raw GitPython exceptions used to
    escape into the auto-integrate skip table, where they were recorded
    as an opaque ``unexpected failure`` that named neither the operation
    nor the repository.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return repo.head.commit.hexsha
    except Exception as exc:
        raise GitOperationError("get_head_sha", str(exc)) from exc
    finally:
        _close_repo(repo)


def merge_base(
    repo_root: Path | str,
    ref_a: str,
    ref_b: str,
) -> str:
    """Return merge-base SHA between two refs.

    Args:
        repo_root: Path to the repository root.
        ref_a: First ref (branch, tag, SHA).
        ref_b: Second ref (branch, tag, SHA).

    Returns:
        SHA of the merge base commit.

    Raises:
        GitOperationError: If merge base cannot be determined.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        bases = repo.merge_base(ref_a, ref_b)
        if not bases:
            msg = f"No merge base between {ref_a} and {ref_b}"
            raise GitOperationError("merge_base", msg)
        return bases[0].hexsha
    except Exception as exc:
        raise GitOperationError("merge_base", str(exc)) from exc
    finally:
        _close_repo(repo)


def get_current_branch(repo_root: Path | str) -> str:
    """Get the current branch name.

    Args:
        repo_root: Path to the repository root.

    Returns:
        Name of the current branch.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return repo.active_branch.name
    finally:
        _close_repo(repo)


def get_commits_between(
    repo_root: Path | str,
    from_ref: str,
    to_ref: str,
) -> list[str]:
    """Get list of commit SHAs between two refs.

    Args:
        repo_root: Path to the repository root.
        from_ref: Starting ref (exclusive).
        to_ref: Ending ref (inclusive).

    Returns:
        List of commit SHAs in reverse chronological order.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        commits = repo.iter_commits(f"{from_ref}..{to_ref}")
        return [c.hexsha for c in commits]
    finally:
        _close_repo(repo)


def append_to_gitignore(repo_root: Path | str, patterns: list[str]) -> None:
    """Append patterns to .gitignore.

    Args:
        repo_root: Path to the repository root.
        patterns: List of patterns to add.
    """
    gitignore_path = Path(repo_root) / ".gitignore"
    existing = set()
    if gitignore_path.exists():
        existing = set(gitignore_path.read_text().splitlines())

    new_patterns = [p for p in patterns if p not in existing]
    if new_patterns:
        payload = ("\n".join(new_patterns)) + "\n"
        _atomic_append_text(gitignore_path, payload)
        logger.debug("Appended {} patterns to .gitignore", len(new_patterns))


def _atomic_append_text(
    path: Path,
    payload: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Append ``payload`` to ``path`` atomically via sibling-staging + ``Path.replace``.

    Mirrors the pattern from ``ralph/mcp/transport/agy.py:99-127`` so a
    SIGKILL mid-write leaves the target file intact -- the new content is
    staged in a sibling file (with a sha256(payload)-derived hash plus
    os.getpid() suffix), then ``Path.replace()``-published atomically.

    Boundary normalization: when ``path`` exists and its content does NOT
    end with ``\\n``, the helper inserts a single ``\\n`` separator before
    the appended payload. Without this, appending ``"*.cache\\n"`` to a
    file containing ``"existing-without-newline"`` would publish
    ``"existing-without-newline*.cache\\n"`` -- a malformed single-line
    rule rather than two separate rules.

    Byte-preserving round-trip: the helper reads the existing file via
    ``Path.read_bytes()`` and writes the staged file via
    ``Path.write_bytes()``. This preserves CRLF (``\\r\\n``), BOM, and
    any other byte-level content the caller wrote -- a text-mode round
    trip via ``read_text``/``write_text`` would normalize CRLF to LF
    on POSIX (universal-newlines mode) and silently corrupt
    Windows-style or gitattributes-style content. The payload string
    is encoded with the same ``encoding`` argument for the write so the
    boundary insert (when needed) does not double-encode the existing
    bytes.

    Read-failure policy: when ``path.exists()`` is True but the read
    raises ``OSError`` (permission denied, broken FS, transient I/O), the
    helper fails closed by re-raising the underlying ``OSError``. A
    silent fallback to ``existing = ""`` would publish ``payload`` over
    the original content via ``Path.replace()`` -- exactly the silent
    corruption the atomic helper exists to prevent. Callers that want a
    best-effort fallback (e.g. bootstrap auto-seed) wrap the call in
    their own try/except. A missing ``path`` is treated as empty
    (legitimate creation case).

    If the staging write or replace fails, the staging file is removed
    before re-raising so the helper never leaves a dangling sibling.

    Args:
        path: Target file path. Parent directory must already exist (or
            be creatable via ``path.parent.mkdir(parents=True, exist_ok=True)``).
        payload: Text to append. The helper normalizes the boundary
            against the existing content (see above).
        encoding: Text encoding for the staging write. Default ``utf-8``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = b""
    if path.exists():
        existing = path.read_bytes()
    separator = b""
    if existing and not existing.endswith(b"\n"):
        separator = b"\n"
    _staging_hash = hashlib.sha256(payload.encode(encoding)).hexdigest()[:16]
    _staging_pid = os.getpid()
    staging = path.with_suffix(path.suffix + f".ralph-staging.{_staging_pid}.{_staging_hash}")
    try:
        # filesystem-write-ok: temp staging file (atomic replace target below)
        staging.write_bytes(existing + separator + payload.encode(encoding))
        staging.replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            staging.unlink()
        raise


def atomic_append_text(
    path: Path,
    payload: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Append text through Ralph's atomic sibling-staging publication path."""
    _atomic_append_text(path, payload, encoding=encoding)
