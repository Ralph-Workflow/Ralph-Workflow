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
    _lock_base_dir,
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
_HEX_SHA_LENGTH = 40
_PACKED_REF_PARTS = 2


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


@dataclass(frozen=True)
class _RepoQueueView:
    git: object
    git_dir: str


def _is_queue_view(candidate: object) -> bool:
    return isinstance(candidate, _RepoQueueView)


def _queue_routing_target(repo_root: Path | str, repo: Repo) -> Repo:
    if not _is_queue_view(repo) and not hasattr(repo, "git_dir"):
        view = _RepoQueueView(git=repo.git, git_dir=str(repo_root))
        return cast("Repo", view)
    return repo


def _queued_git_call(
    repo_root: Path | str,
    repo: Repo,
    method_name: str,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> object:
    return route_git_method(_queue_routing_target(repo_root, repo), method_name, args, kwargs)


def _queued_remote_push(repo_root: Path | str, repo: Repo, remote: str, branch: str) -> None:
    queue = get_queue_for_repo(Path(repo_root))
    with queue.execute_lock:
        try:
            repo.remote(remote).push(branch)
        except BaseException as exc:
            queue.record_outcome(_queue_outcome_kind(exc))
            raise
        queue.record_outcome("success")


_LOCK_BACKOFF_S: tuple[float, ...] = _BASE_LOCK_BACKOFF_S
_STALE_GIT_LOCK_AGE_SECONDS: float = _BASE_STALE_GIT_LOCK_AGE_SECONDS
_sleep_fn: Callable[[float], None] = _base_sleep_fn
_probe_lsof_fuser: Callable[[Path, float], Literal["live", "dead", "unknown"] | bool] = (
    _base_probe_lsof_fuser
)
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
        probe_lsof_fuser_fn=(
            None if _probe_lsof_fuser is _base_probe_lsof_fuser else _probe_lsof_fuser
        ),
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
    repo_root: Path | str | None = None,
) -> T:
    token = _lock_base_dir.set(Path(repo_root).resolve() if repo_root is not None else None)
    try:
        return _base_run_git_operation_with_stale_lock_recovery(
            operation,
            action,
            recover_fn=_recover_stale_git_lock,
            sleep_fn=_sleep_fn,
            holder_fn=_find_lock_holder,
        )
    finally:
        _lock_base_dir.reset(token)


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
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            status_output = cast(
                "str", _queued_git_call(repo_root, repo, "status", ("--porcelain",))
            )
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
    if not line:
        return False
    if line.startswith("??"):
        return False
    return line[0] not in {" ", "?"}


def _git_status_porcelain_lines(repo_root: Path) -> list[str]:
    result = run_git(("status", "--porcelain"), cwd=repo_root, label="git-status")
    if result.returncode != 0:
        raise GitOperationError(
            "git-status",
            f"git status --porcelain failed (rc={result.returncode}): "
            f"{result.stderr.strip() or '<no stderr>'}",
        )
    return result.stdout.splitlines()


def stage_all(repo_root: Path | str) -> None:
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_repo = repo

        def _stage() -> None:
            _ = _queued_git_call(repo_root, active_repo, "add", kwargs={"A": True})

        _run_git_operation_with_stale_lock_recovery("stage_all", _stage, repo_root)
        logger.debug("Staged all changes in {}", repo_root)
    except Exception as exc:
        raise GitOperationError("stage_all", str(exc)) from exc
    finally:
        _close_repo(repo)


def write_tree(repo_root: Path | str) -> str:
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_repo = repo

        def _write() -> str:
            tree_sha = _queued_git_call(repo_root, active_repo, "write_tree")
            return str(tree_sha).strip()

        return str(
            _run_git_operation_with_stale_lock_recovery("write_tree", _write, repo_root)
        ).strip()
    except Exception as exc:
        raise GitOperationError("write_tree", str(exc)) from exc
    finally:
        _close_repo(repo)


def stage_files(repo_root: Path | str, files: list[str]) -> None:
    if not files:
        logger.debug("No files requested for selective staging in {}", repo_root)
        return
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_repo = repo

        def _stage() -> None:
            _ = _queued_git_call(repo_root, active_repo, "add", ("--all", "--", *files))

        _run_git_operation_with_stale_lock_recovery("stage_files", _stage, repo_root)
        logger.debug("Staged {} selected paths in {}", len(files), repo_root)
    except Exception as exc:
        raise GitOperationError("stage_files", str(exc)) from exc
    finally:
        _close_repo(repo)


def _current_head_sha(repo_root: Path) -> str | None:
    direct = _read_head_sha_direct(repo_root)
    if direct is not None:
        return direct
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
    try:
        repo = Repo(repo_root)
        try:
            return str(repo.commit(head_sha).tree.hexsha)
        finally:
            _close_repo(repo)
    except Exception as exc:
        logger.debug("Failed reading head tree from GitPython: {}", exc)
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
    if not current_head_sha:
        return False
    head_tree = _head_tree_sha(repo_root, current_head_sha)
    return head_tree is not None and head_tree == candidate_tree


def _reattach_or_resolve_ref_name(repo_root: Path) -> str:
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
    hook_env = {"GIT_REFLOG_ACTION": "ralph create_commit"}
    hooks_dir = Path(repo_git_dir) / "hooks"
    _run_commit_hooks(repo_root, hooks_dir, message_path, hook_env)
    return message_path.read_text(encoding="utf-8")


def _candidate_commit_tree(repo_root: Path) -> str:
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
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        message = _append_ralph_workflow_coauthor_trailer(message)
        current_head_sha = repo.head.commit.hexsha if hasattr(repo, "head") else ""
        if not hasattr(repo, "git_dir"):
            if current_head_sha and current_head_sha != expected_head:
                return CommitCreationResult.already_advanced(current_head_sha)
            author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)
            actor = Actor(author_name, author_email)
            commit = _run_git_operation_with_stale_lock_recovery(
                "create_commit",
                lambda: repo.index.commit(message, author=actor, committer=actor),
                repo_root,
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
            message_path.unlink(missing_ok=True)

        candidate_tree_sha = _candidate_commit_tree(Path(repo_root))

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
        result = _run_git_operation_with_stale_lock_recovery(
            "create_commit",
            lambda: _cas_publish_or_detect_advance(
                Path(repo_root),
                ref_name,
                candidate_sha,
                expected_head,
                Path(repo.git_dir) / "hooks",
                {"GIT_REFLOG_ACTION": "ralph create_commit"},
            ),
            repo_root,
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


class CommitTreeMismatchError(GitOperationError):
    def __init__(self, message: str) -> None:
        super().__init__("create_commit", message)


def _commit_parent_sha(repo_root: Path, commit_sha: str) -> str:
    try:
        repo = Repo(repo_root)
        try:
            parents = repo.commit(commit_sha).parents
            return str(parents[0].hexsha) if parents else ""
        finally:
            _close_repo(repo)
    except Exception as exc:
        logger.debug("Failed reading commit parent from GitPython: {}", exc)
    result = run_git(
        ("rev-parse", "--verify", f"{commit_sha}^"),
        cwd=repo_root,
        label="git-rev-parse-commit-parent",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _undo_unapproved_commit(
    repo_root: Path,
    ref_name: str,
    created_sha: str,
    parent_sha: str,
    approved_tree_sha: str,
) -> bool:
    if parent_sha:
        undo = run_git(
            ("update-ref", ref_name, parent_sha, created_sha),
            cwd=repo_root,
            label="git-update-ref-undo-unapproved-commit",
            options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph undo unapproved commit"}),
        )
    else:
        undo = run_git(
            ("update-ref", "-d", ref_name, created_sha),
            cwd=repo_root,
            label="git-update-ref-delete-unapproved-commit",
            options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph undo unapproved commit"}),
        )
    restored = True
    if undo.returncode != 0:
        logger.error(
            "Could not undo the unapproved commit {} on {}: {}",
            created_sha[:8],
            ref_name,
            undo.stderr.strip() or "no diagnostics",
        )
        restored = False
    restore = run_git(
        ("read-tree", approved_tree_sha),
        cwd=repo_root,
        label="git-read-tree-restore-approved-tree",
    )
    if restore.returncode != 0:
        logger.error(
            "Could not restore the index to the approved tree {}: {}",
            approved_tree_sha[:8],
            restore.stderr.strip() or "no diagnostics",
        )
        restored = False
    return restored


def create_commit_with_hooks(
    repo_root: Path | str,
    message: str,
    *,
    expected_head: str,
    expected_tree_sha: str,
    author_name: str | None = None,
    author_email: str | None = None,
) -> CommitCreationResult:
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        message = _append_ralph_workflow_coauthor_trailer(message)
        current_head_sha = repo.head.commit.hexsha if hasattr(repo, "head") else ""
        if current_head_sha and current_head_sha != expected_head:
            return CommitCreationResult.already_advanced(current_head_sha)
        author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)
        ref_name = _reattach_or_resolve_ref_name(Path(repo_root))

        staged_tree_sha = _candidate_commit_tree(Path(repo_root))
        if staged_tree_sha != expected_tree_sha:
            raise CommitTreeMismatchError(
                f"staged tree SHA {staged_tree_sha} does not match approved tree SHA "
                f"{expected_tree_sha}"
            )
        if _tree_matches_head(Path(repo_root), current_head_sha, staged_tree_sha):
            return CommitCreationResult.already_advanced(current_head_sha)

        with tempfile.TemporaryDirectory(prefix="ralph-commit-") as scratch:
            message_path = Path(scratch) / "COMMIT_EDITMSG"
            message_path.write_text(message, encoding="utf-8")
            commit_result = run_git(
                (
                    "commit",
                    "--allow-empty",
                    "--cleanup=verbatim",
                    "--no-gpg-sign",
                    "--file",
                    str(message_path),
                ),
                cwd=Path(repo_root),
                label="git-commit",
                options=GitRunOptions(
                    env={
                        "GIT_AUTHOR_NAME": author_name,
                        "GIT_AUTHOR_EMAIL": author_email,
                        "GIT_COMMITTER_NAME": author_name,
                        "GIT_COMMITTER_EMAIL": author_email,
                        "GIT_REFLOG_ACTION": "ralph create_commit",
                    },
                ),
            )
        if commit_result.returncode != 0:
            detail = commit_result.stderr.strip() or commit_result.stdout.strip()
            raise GitOperationError("create_commit", detail or "git commit failed")

        created_sha = _current_head_sha(Path(repo_root)) or ""
        if not created_sha:
            raise GitOperationError("create_commit", "git commit did not advance HEAD")
        committed_tree_sha = _head_tree_sha(Path(repo_root), created_sha) or ""
        parent_sha = _commit_parent_sha(Path(repo_root), created_sha)
        if committed_tree_sha != expected_tree_sha:
            undone = _undo_unapproved_commit(
                Path(repo_root),
                ref_name,
                created_sha,
                parent_sha,
                expected_tree_sha,
            )
            if not undone:
                raise CommitTreeMismatchError(
                    f"committed tree SHA {committed_tree_sha} does not match approved tree SHA "
                    f"{expected_tree_sha}; the unapproved commit {created_sha[:8]} could NOT be "
                    "undone and is still published - remove it manually"
                )
            raise CommitTreeMismatchError(
                f"committed tree SHA {committed_tree_sha} does not match approved tree SHA "
                f"{expected_tree_sha}"
            )
        if parent_sha and expected_head and parent_sha != expected_head:
            _undo_unapproved_commit(
                Path(repo_root),
                ref_name,
                created_sha,
                parent_sha,
                expected_tree_sha,
            )
            return CommitCreationResult.already_advanced(parent_sha)
        logger.info(
            "Created commit {}: {}",
            created_sha[:8],
            message.splitlines()[0] if message else "(no message)",
        )
        return CommitCreationResult.created(created_sha)
    except GitOperationError:
        raise
    except Exception as exc:
        raise GitOperationError("create_commit", str(exc)) from exc
    finally:
        _close_repo(repo)


def push(
    repo_root: Path | str,
    remote: str,
    branch: str | None = None,
) -> None:
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_branch = branch or repo.active_branch.name
        repo.remote(remote)
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
        _queued_remote_push(repo_root, repo, remote, active_branch)
        logger.info("Pushed {} to {}/{}", active_branch, remote, active_branch)
    except Exception as exc:
        raise GitOperationError("push", str(exc)) from exc
    finally:
        _close_repo(repo)


def _is_hex_sha(candidate: str) -> bool:
    return len(candidate) == _HEX_SHA_LENGTH and all(
        c in "0123456789abcdefABCDEF" for c in candidate
    )


def _resolve_gitdir_paths(root: Path) -> tuple[Path | None, Path | None]:
    dot_git = root / ".git"
    gitdir: Path | None = None
    if dot_git.is_dir():
        gitdir = dot_git
    elif dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            target = Path(text.split(":", 1)[1].strip())
            gitdir = target if target.is_absolute() else (root / target).resolve()
    elif (root / "HEAD").is_file() and ((root / "refs").is_dir() or (root / "objects").is_dir()):
        gitdir = root

    if gitdir is None:
        return None, None

    commondir: Path | None = None
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        c_text = commondir_file.read_text(encoding="utf-8").strip()
        c_target = Path(c_text)
        commondir = c_target if c_target.is_absolute() else (gitdir / c_target).resolve()
    return gitdir, commondir


def _read_ref_from_disk(ref: str, search_dirs: list[Path]) -> str | None:
    for d in search_dirs:
        ref_path = d / ref
        if ref_path.is_file():
            val = ref_path.read_text(encoding="utf-8").strip()
            if _is_hex_sha(val):
                return val
    for d in search_dirs:
        packed = d / "packed-refs"
        if not packed.is_file():
            continue
        for raw_line in packed.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split()
            if len(parts) == _PACKED_REF_PARTS and parts[1] == ref and _is_hex_sha(parts[0]):
                return parts[0]
    return None


def _read_head_sha_direct(repo_root: Path | str) -> str | None:
    try:
        gitdir, commondir = _resolve_gitdir_paths(Path(repo_root))
        if gitdir is None:
            return None
        head_file = gitdir / "HEAD"
        if not head_file.is_file():
            return None
        content = head_file.read_text(encoding="utf-8").strip()
        if _is_hex_sha(content):
            return content
        if not content.startswith("ref:"):
            return None
        ref = content.split(":", 1)[1].strip()
        search_dirs = [gitdir]
        if commondir is not None and commondir != gitdir:
            search_dirs.append(commondir)
        return _read_ref_from_disk(ref, search_dirs)
    except Exception:
        return None


def get_head_sha(repo_root: Path | str) -> str:
    direct_sha = _read_head_sha_direct(repo_root)
    if direct_sha is not None:
        return direct_sha

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
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        commits = repo.iter_commits(f"{from_ref}..{to_ref}")
        return [c.hexsha for c in commits]
    finally:
        _close_repo(repo)


def append_to_gitignore(repo_root: Path | str, patterns: list[str]) -> None:
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
    _atomic_append_text(path, payload, encoding=encoding)
