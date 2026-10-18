"""Commit effect execution for the pipeline runner."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.config.enums import Verbosity
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)
from ralph.git.commit_cleanup import (
    add_to_git_exclude,
    is_recognized_secret_path,
)
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
from ralph.git.operations import (
    create_commit,
    get_head_sha,
    has_uncommitted_changes,
    stage_all,
)
from ralph.git.operations import stage_files as _stage_files
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
    read_commit_message_payload_from_path,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    resolve_phase_required_artifact,
)
from ralph.pipeline._auto_integrate_recovery_git_state import (
    common_dir_lock_paths,
    detached_branch_name,
    detached_head_no_state,
    lock_holder_is_dead,
    reattach_head_to_branch,
    rebase_bookkeeping_dir,
    remove_path,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import CommitResidualEvent, Event, PipelineEvent
from ralph.prompts.commit_evidence import build_commit_evidence_bundle

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from ralph.display.context import DisplayContext
    from ralph.policy.models import AgentsPolicy, PolicyBundle
    from ralph.workspace import FsWorkspace

_PORCELAIN_STATUS_PREFIX_LEN = 3
_SECRET_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.*.local",
    "secrets.yml",
    "credentials.json",
)


def _render_commit_message_via_display(repo_root: Path, display_context: object) -> None:
    """Render the commit message via the consolidated display surface.

    When the resolved display is a ``ParallelDisplay`` instance, dispatch
    directly to its ``emit_commit_message`` method. When it is a
    ``DisplayContext`` (or None), fall back to the active-display resolver.
    """
    display: object | None = None
    if isinstance(display_context, ParallelDisplay):
        display = display_context
    elif display_context is not None:
        try:
            display = resolve_active_display(
                None, cast("DisplayContext", display_context)
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        except Exception:
            display = None
    if display is None:
        return
    with suppress(Exception):
        cast("ParallelDisplay", display).emit_commit_message(
            repo_root
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


@dataclass(frozen=True)
class _CommitScopeResolution:
    include_paths: tuple[str, ...] | None


if TYPE_CHECKING:

    class _CreateCommitFn(Protocol):
        def __call__(
            self,
            repo_root: Path | str,
            message: str,
            *,
            expected_head: str,
        ) -> CommitCreationResult: ...

    class _StageAllFn(Protocol):
        def __call__(self, repo_root: Path | str) -> None: ...

    class _HasCommitWorkFn(Protocol):
        def __call__(self, repo_root: Path) -> bool: ...

    class _RenderCommitMessageFn(Protocol):
        def __call__(self, repo_root: Path, display_context: object) -> None: ...


class _CommitExecutionRecord:
    last_error: str | None = None


_execution_record = _CommitExecutionRecord()


def get_last_commit_error() -> str | None:
    """Return the error message from the most recent failed commit execution."""
    return _execution_record.last_error


def set_last_commit_error(error: str | None) -> None:
    """Record the error message from a commit execution failure."""
    _execution_record.last_error = error


def _is_git_worktree(repo_root: Path) -> bool:
    """Check if repo_root is inside a valid git working tree."""
    try:
        res = run_git(
            ("rev-parse", "--is-inside-work-tree"),
            cwd=repo_root,
            label="check-git-worktree",
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def _is_head_detached(repo_root: Path) -> bool:
    """Check if HEAD is currently detached."""
    if not _is_git_worktree(repo_root):
        return False
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None and detached_head_no_state(repo_root, git_dir):
        return True
    try:
        result = run_git(
            ("symbolic-ref", "--quiet", "HEAD"),
            cwd=repo_root,
            label="commit:head-symbolic-ref",
        )
        return result.returncode != 0
    except Exception:
        return False


def _branch_exists(repo_root: Path, branch: str) -> bool:
    """Check if a branch exists locally in refs/heads/."""
    if not branch or not branch.strip():
        return False
    try:
        res = run_git(
            ("show-ref", "--verify", f"refs/heads/{branch.strip()}"),
            cwd=repo_root,
            label="check-branch",
        )
        return res.returncode == 0
    except Exception:
        return False


def _detached_branch_from_reflog(repo_root: Path) -> str | None:
    """Find the branch detached from by inspecting the reflog."""
    try:
        result = run_git(
            ("reflog", "--format=%H%x00%gs", "-n", "50"),
            cwd=repo_root,
            label="commit:reflog-source-branch",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "\x00" not in line:
            continue
        _sha, subject = line.split("\x00", 1)
        for marker in ("checkout: moving from ", "switch branch to "):
            idx = subject.find(marker)
            if idx == -1:
                continue
            rest = subject[idx + len(marker) :]
            src, *dest_parts = rest.split(" to ", 1)
            src = src.strip()
            if src and _branch_exists(repo_root, src):
                return src
            if dest_parts:
                dest = dest_parts[0].strip()
                if dest and _branch_exists(repo_root, dest):
                    return dest
    return None


def _branch_from_worktree_metadata(repo_root: Path) -> str | None:
    """Inspect git worktree list --porcelain to find the branch for this worktree."""
    try:
        res = run_git(
            ("worktree", "list", "--porcelain"),
            cwd=repo_root,
            label="commit:worktree-list",
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    current_matches = False
    try:
        resolved_root = repo_root.resolve()
    except Exception:
        resolved_root = repo_root
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path_str = line[len("worktree ") :].strip()
            try:
                current_matches = Path(path_str).resolve() == resolved_root
            except Exception:
                current_matches = False
        elif current_matches and line.startswith("branch refs/heads/"):
            b = line[len("branch refs/heads/") :].strip()
            if _branch_exists(repo_root, b):
                return b
    return None


def _candidate_branch_names(repo_root: Path, **opts: object) -> Iterator[str]:
    # Authoritative-source-first ordering (AC-10 / WU-2): the
    # reflog and worktree metadata describe what branch the user
    # was actually on before the detach; the integration target is
    # the weakest signal (it is the *destination* of a successful
    # integration, not the work's source branch). The previous
    # order yielded ``config.general.auto_integrate_target`` (the
    # integration target) before the reflog, which caused the
    # resolver to commit work on the integration target instead of
    # the feature branch when both tips happened to coincide after
    # a reset/snapshot. The reflog-derived branch now wins in that
    # shared-tip case; the integration target is the last-resort
    # config fallback. ``detached_branch_name`` (the .git/HEAD
    # residual) is still first; ``repo_root.name`` is the last
    # fallback.
    branch = detached_branch_name(repo_root)
    if branch:
        yield branch

    from_reflog = _detached_branch_from_reflog(repo_root)
    if from_reflog:
        yield from_reflog

    from_wt = _branch_from_worktree_metadata(repo_root)
    if from_wt:
        yield from_wt

    for key in ("branch", "feature_branch", "target_branch"):
        val = opts.get(key)
        if isinstance(val, str) and val.strip():
            yield val.strip()

    config = opts.get("config")
    if config is not None:
        for attr in ("feature_branch", "branch", "target_branch"):
            attr_val: object = getattr(config, attr, None)
            if isinstance(attr_val, str) and attr_val.strip():
                yield attr_val.strip()
        general_obj: object = getattr(config, "general", None)
        if general_obj is not None:
            for general_attr in ("feature_branch", "branch", "auto_integrate_target"):
                gen_val: object = getattr(general_obj, general_attr, None)
                if isinstance(gen_val, str) and gen_val.strip():
                    yield gen_val.strip()

    yield repo_root.name


def _resolve_branch_for_reattach(repo_root: Path, **opts: object) -> str | None:
    """Resolve the target branch to safely reattach a detached HEAD to.

    A detached commit that is not at the tip of the chosen branch
    is a safety hazard: ``git symbolic-ref HEAD refs/heads/<branch>``
    would orphan the detached commit. The resolver therefore requires
    EXACT branch-tip equality (the same invariant
    ``_auto_integrate_recovery_git_state._resolve_reflog_candidate``
    enforces for reflog-derived candidates) for every candidate --
    reflog, config, worktree metadata, or repository name. A
    candidate whose branch tip is ahead of, behind, or diverged from
    the detached HEAD is rejected.

    The same integration-target and occupied-worktree exclusions
    the ancestry-verified fast-forward fallback applies (see
    :mod:`ralph.pipeline._commit_reattach_fallback`) are also
    applied here (AC-10): reattaching onto the integration target
    strands the work on the destination branch rather than the
    feature branch, and reattaching onto a branch currently
    checked out in another worktree strands another agent's
    working copy. Excluding them on the exact-tip path closes
    the same-gap the ancestry fallback already guards.

    The caller (``_reattach_head_if_detached``) logs a warning
    and refuses to reattach when no safe candidate is found; the
    detached work remains visible and the next commit attempt
    surfaces the unresolved state instead of silently losing the
    work.
    """
    from ralph.pipeline._commit_reattach_fallback import (
        integration_target_branch,
        occupied_worktree_branches,
    )

    detached_head_sha = _detached_head_sha(repo_root)
    integration_target = integration_target_branch(**opts)
    occupied = occupied_worktree_branches(repo_root, **opts)
    for candidate in _candidate_branch_names(repo_root, **opts):
        if not _branch_exists(repo_root, candidate):
            continue
        if integration_target and candidate == integration_target:
            logger.debug(
                "reattach exact-tip: skipping integration target '{}' (clobber risk)",
                candidate,
            )
            continue
        if candidate in occupied:
            logger.debug(
                "reattach exact-tip: skipping occupied worktree branch '{}'",
                candidate,
            )
            continue
        if not _branch_tip_matches(repo_root, candidate, detached_head_sha):
            logger.debug(
                "reattach candidate '{}' tip does not match detached HEAD {}; skipping",
                candidate,
                detached_head_sha or "<unreadable>",
            )
            continue
        return candidate
    return None


def _detached_head_sha(repo_root: Path) -> str | None:
    """Return the detached HEAD's current OID, or ``None`` if HEAD is attached."""
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=repo_root,
            label="commit:detached-head-sha",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    head_sha = result.stdout.strip()
    return head_sha or None


def _branch_tip_matches(repo_root: Path, branch: str, head_sha: str | None) -> bool:
    """Return True iff ``refs/heads/<branch>`` exists and its tip equals ``head_sha``."""
    if not head_sha:
        return False
    try:
        result = run_git(
            ("rev-parse", "--verify", f"refs/heads/{branch}"),
            cwd=repo_root,
            label="commit:branch-tip-sha",
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == head_sha


def _reattach_head_if_detached(repo_root: Path, **opts: object) -> bool:
    """If HEAD is detached, safely reattach to the verified feature branch.

    Three-step strategy (AC-10 / GAP-1):

    1. **Exact-tip match** (the original invariant). If a candidate
       branch's tip equals the detached HEAD's SHA, the
       ``git symbolic-ref HEAD refs/heads/<branch>`` reattach is
       lossless -- the reflog and the branch are already
       consistent.
    2. **Ancestry-verified fast-forward** (new). When the detached
       HEAD is a *descendant* of an existing branch's tip
       (``git merge-base --is-ancestor <branch-tip> <detached-head>``)
       and the candidate is neither the integration target nor a
       branch currently checked out in another worktree, the
       branch is fast-forwarded to the detached SHA via the
       compare-and-swap ``git update-ref <ref> <new-oid> <old-oid>``
       (the observed branch tip is the ``<old-oid>``). The CAS
       refuses to clobber a concurrent ref change, so a divergent
       reflog-orphan state surfaces as a non-zero exit and the
       resolver falls through to step 3.
    3. **Fresh branch** (new). When no candidate is safe, a fresh
       branch is created at the detached SHA with a collision-safe
       name (``<preferred>-<7-char-sha>`` on collision; never
       overwrites an existing branch). The integration target is
       never a preferred name -- fast-forwarding it would clobber
       remote commits.
    """
    from ralph.pipeline._commit_reattach_fallback import (
        create_fresh_branch_for_detached,
        fast_forward_reattach,
        integration_target_branch,
        occupied_worktree_branches,
    )

    if not _is_head_detached(repo_root):
        return False
    branch = _resolve_branch_for_reattach(repo_root, **opts)
    if branch:
        logger.info("Detached HEAD detected; safely reattaching to branch '{}'", branch)
        reattach_head_to_branch(repo_root, branch)
        return True
    # Step 2: ancestry-verified fast-forward fallback.
    detached_head_sha = _detached_head_sha(repo_root)
    integration_target = integration_target_branch(**opts)
    occupied = occupied_worktree_branches(repo_root, **opts)
    if fast_forward_reattach(
        repo_root,
        detached_head_sha=detached_head_sha,
        candidates=_candidate_branch_names(repo_root, **opts),
        branch_exists=_branch_exists,
        integration_target=integration_target,
        occupied=occupied,
    ):
        return True
    # Step 3: fresh branch with collision-safe naming.
    fresh = create_fresh_branch_for_detached(
        repo_root,
        detached_head_sha=detached_head_sha,
        candidates=_candidate_branch_names(repo_root, **opts),
        branch_exists=_branch_exists,
        integration_target=integration_target,
        occupied=occupied,
    )
    if fresh:
        logger.info(
            "Detached HEAD reattached to fresh branch '{}' (no safe candidate)",
            fresh,
        )
        reattach_head_to_branch(repo_root, fresh)
        return True
    logger.warning("Detached HEAD detected but target branch could not be resolved")
    return False


def _attempt_repair_git_state(repo_root: Path, **opts: object) -> bool:
    """Repair repairable git state (detached HEAD, stale locks) so commit can succeed."""
    if not _is_git_worktree(repo_root):
        return False
    repaired = False
    if _is_head_detached(repo_root) and _reattach_head_if_detached(repo_root, **opts):
        repaired = True

    git_dir = rebase_bookkeeping_dir(repo_root) or (repo_root / ".git")
    if git_dir.is_dir():
        index_lock = git_dir / "index.lock"
        if index_lock.exists() and lock_holder_is_dead(index_lock):
            logger.info("Removing stale index.lock: {}", index_lock)
            remove_path(index_lock)
            repaired = True
        head_lock = git_dir / "HEAD.lock"
        if head_lock.exists() and lock_holder_is_dead(head_lock):
            logger.info("Removing stale HEAD.lock: {}", head_lock)
            remove_path(head_lock)
            repaired = True
        for lock_path in common_dir_lock_paths(repo_root):
            if lock_path.exists() and lock_holder_is_dead(lock_path):
                logger.info("Removing stale common dir lock: {}", lock_path)
                remove_path(lock_path)
                repaired = True
    return repaired


def _perform_commit_creation(
    repo_root: Path,
    message: str,
    payload: dict[str, object],
    create_commit_fn: _CreateCommitFn,
    stage_all_fn: _StageAllFn,
    repair_opts: Mapping[str, object],
) -> str:
    """Attempt commit creation with repairable git state retry, returning commit sha."""
    expected_head = get_head_sha(repo_root)
    _stage_commit_scope(repo_root, payload, stage_all_fn)
    result = create_commit_fn(str(repo_root), message, expected_head=expected_head)
    if result.status is not CommitCreationStatus.CREATED or result.sha is None:
        if _attempt_repair_git_state(repo_root, **repair_opts):
            logger.info("Retrying commit creation after git state repair")
            expected_head = get_head_sha(repo_root)
            _stage_commit_scope(repo_root, payload, stage_all_fn)
            result = create_commit_fn(str(repo_root), message, expected_head=expected_head)
        if result.status is not CommitCreationStatus.CREATED or result.sha is None:
            err_msg = result.error or f"commit creation returned {result.status.value}"
            logger.warning("Commit was not created: {}", err_msg)
            raise RuntimeError(err_msg)
    if result.sha is None:
        raise RuntimeError("commit creation produced no sha")
    return result.sha


def execute_commit_effect(
    effect: CommitEffect,
    repo_root: Path,
    display: ParallelDisplay | None = None,
    **opts: object,
) -> Event:
    """Execute a commit effect, creating or skipping a git commit."""
    verbosity = cast(
        "Verbosity", opts.get("verbosity", Verbosity.VERBOSE)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    _raw_create = opts.get("create_commit_fn")
    _create_commit_fn: _CreateCommitFn = cast(
        "_CreateCommitFn", _raw_create if callable(_raw_create) else create_commit
    )
    _raw_stage = opts.get("stage_all_fn")
    _stage_all_fn: _StageAllFn = cast(
        "_StageAllFn", _raw_stage if callable(_raw_stage) else stage_all
    )
    _raw_has_work = opts.get("has_commit_work_fn")
    _has_commit_work_fn: _HasCommitWorkFn = cast(
        "_HasCommitWorkFn", _raw_has_work if callable(_raw_has_work) else _repo_has_commit_work
    )
    _raw_residual_work = opts.get("has_residual_work_fn")
    _has_residual_work_fn: _HasCommitWorkFn | None = cast(
        "_HasCommitWorkFn | None", _raw_residual_work if callable(_raw_residual_work) else None
    )
    try:
        payload = _read_commit_effect_payload(effect)
        message = _read_commit_effect_message(effect)
        if payload is None or not message:
            # "Empty" only when it really is: a file that failed the artifact
            # grammar (a NUL in a Files path, a malformed section) also lands
            # here, and reporting it as empty sends the next pass looking for
            # the wrong problem while the rejected artifact is retained.
            logger.error(
                "Commit message file is {}: {}",
                "empty" if not _has_content(effect.message_file) else "invalid",
                effect.message_file,
            )
            raise ValueError("commit message artifact is invalid")
        if payload.get("type") == "skip" or message.strip().lower().startswith("skip:"):
            logger.info("Commit agent requested skip — skipping commit execution")
            cleanup_commit_message_artifacts(repo_root)
            set_last_commit_error(None)
            return PipelineEvent.COMMIT_SKIPPED
        _reattach_head_if_detached(repo_root, **opts)
        if not _has_commit_work_fn(repo_root):
            logger.info("Skipping commit because the worktree is empty")
            cleanup_commit_message_artifacts(repo_root)
            set_last_commit_error(None)
            return PipelineEvent.COMMIT_SUCCESS
        try:
            before_paths = tuple(_changed_commit_paths(repo_root))
        except Exception:
            before_paths = ()
        include_paths = _commit_include_paths(repo_root, payload)
        sha = _perform_commit_creation(
            repo_root,
            message,
            payload,
            _create_commit_fn,
            _stage_all_fn,
            opts,
        )
        set_last_commit_error(None)
        logger.info("Created commit: {}", sha[:8])
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        set_last_commit_error(err_msg)
        logger.error("Commit failed ({}): {}", type(exc).__name__, exc)
        return PipelineEvent.COMMIT_FAILURE

    with suppress(Exception):
        _render_commit_fn = cast(
            "_RenderCommitMessageFn",
            opts.get("render_commit_message_fn")
            if callable(opts.get("render_commit_message_fn"))
            else _render_commit_message_via_display,
        )
        render_target: object = display if display is not None else get_display_context(display)
        _render_commit_fn(repo_root, render_target)
    if verbosity != Verbosity.QUIET and hasattr(display, "record_artifact_outcome"):
        with suppress(Exception):
            cast("ParallelDisplay", display).record_artifact_outcome(f"sha={sha[:8]}")
    with suppress(Exception):
        cleanup_commit_message_artifacts(repo_root)
    if _has_residual_work_fn is not None:
        try:
            if _has_residual_work_fn(repo_root):
                remaining_paths = tuple(_changed_commit_paths(repo_root))
                committed_paths = tuple(
                    path for path in (include_paths or list(before_paths)) if path in before_paths
                )
                if remaining_paths and set(committed_paths).isdisjoint(remaining_paths):
                    logger.info("Commit left uncommitted changes; preparing another commit pass")
                    return CommitResidualEvent(
                        committed_paths=committed_paths,
                        remaining_paths=remaining_paths,
                        sha=sha,
                    )
                if remaining_paths:
                    logger.warning("Residual commit paths overlap committed paths; no re-entry")
        except Exception as exc:
            logger.warning("Unable to inspect residual commit work: {}", exc)
    return _commit_success_event()


def _commit_success_event() -> Event:
    return PipelineEvent.COMMIT_SUCCESS


def _has_content(message_file: str) -> bool:
    """Return whether the commit-message artifact holds any content at all."""
    path = Path(message_file)
    if not DEFAULT_FILE_BACKEND.exists(path):
        return False
    # filesystem-read-ok: the artifact was already read on this path; this
    # distinguishes an absent/blank file from one that failed the grammar.
    return bool(DEFAULT_FILE_BACKEND.read_text(path, encoding="utf-8").strip())


def _read_commit_effect_payload(effect: CommitEffect) -> dict[str, object] | None:
    return read_commit_message_payload_from_path(Path(effect.message_file))


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _stage_commit_scope(
    repo_root: Path,
    payload: dict[str, object],
    stage_all_fn: _StageAllFn,
) -> None:
    if stage_all_fn is stage_all:
        add_to_git_exclude(repo_root, list(_SECRET_EXCLUDE_PATTERNS))
    include_paths = _commit_include_paths(repo_root, payload)
    if include_paths is None:
        stage_all_fn(str(repo_root))
        return
    _stage_files(
        str(repo_root),
        [path for path in include_paths if not is_recognized_secret_path(path)],
    )


def _commit_include_paths(repo_root: Path, payload: dict[str, object]) -> list[str] | None:
    raw_files = payload.get("files")
    raw_excluded = payload.get("excluded_files")
    if not isinstance(raw_files, list) and not isinstance(raw_excluded, list):
        return None
    return _commit_include_paths_from_changed(
        payload, _changed_commit_paths(repo_root), repo_root=repo_root
    )


def _commit_include_paths_from_changed(
    payload: dict[str, object],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> list[str] | None:
    resolution = _resolve_commit_scope(payload, changed_paths, repo_root=repo_root)
    if resolution.include_paths is None:
        return None
    return list(resolution.include_paths)


def _resolve_commit_scope(
    payload: dict[str, object],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> _CommitScopeResolution:
    raw_files = payload.get("files")
    raw_excluded = payload.get("excluded_files")

    if not isinstance(raw_files, list) and not isinstance(raw_excluded, list):
        return _CommitScopeResolution(include_paths=None)

    changed = _dedupe_repo_relative_paths(changed_paths)
    if isinstance(raw_files, list):
        normalized_changed = {_normalize_repo_relative_path(path) for path in changed}
        include_paths: list[str] = []
        for raw_path in raw_files:
            if not isinstance(raw_path, str):
                continue
            normalized = _normalize_repo_relative_path(raw_path)
            _reject_symlink_in_commit_scope(normalized, repo_root)
            if normalized not in normalized_changed:
                raise ValueError(
                    "Commit artifact requested file "
                    f"'{normalized}' that is not part of the current changed set"
                )
            if normalized not in include_paths:
                include_paths.append(normalized)
        requested_paths = set(include_paths)
        if requested_paths != normalized_changed:
            raise ValueError(
                "Commit artifact file selection must exactly match the current changed set: "
                f"expected {sorted(normalized_changed)!r}, got {sorted(requested_paths)!r}"
            )
        return _CommitScopeResolution(include_paths=tuple(include_paths))
    if not isinstance(raw_excluded, list):
        return _CommitScopeResolution(include_paths=None)
    excluded: set[str] = set()
    for item in raw_excluded:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized_excluded = _normalize_repo_relative_path(raw_path)
        _reject_symlink_in_commit_scope(normalized_excluded, repo_root)
        excluded.add(normalized_excluded)
    filtered_paths: tuple[str, ...] = tuple(
        path for path in changed if _normalize_repo_relative_path(path) not in excluded
    )
    return _CommitScopeResolution(include_paths=filtered_paths)


def _reject_symlink_in_commit_scope(normalized_path: str, repo_root: Path | None) -> None:
    """Reject ``normalized_path`` if it escapes ``repo_root`` via symlinks.

    Defense-in-depth: a malformed commit payload could include a path
    that is a symlink OR a path whose parent directory is a symlink
    chain pointing outside the worktree. ``_normalize_repo_relative_path``
    only checks the literal path string (absolute / ``..`` segments) and
    does not look at the filesystem. Without this guard, a symlink at
    a relative path (or any symlink in the parent chain) could stage a
    file outside the worktree when ``git add`` resolves the symlink
    target.

    Two checks are applied:

    1. **Literal-path symlink check** (defense-in-depth at the boundary):
       walks every parent directory AND the candidate path itself and
       rejects the first symlink found. Catches the obvious case where
       ``foo`` is a symlink in the worktree pointing somewhere else.

    2. **Resolved-path escape check** (defense-in-depth against parent
       symlink chains): resolves the candidate path with ``strict=False``
       (so broken symlinks also fire) and verifies the resolved
       location is still under ``repo_root``. Catches the parent-chain
       case where ``dir/file.txt`` is regular, but ``dir`` is a symlink
       pointing outside the worktree (the literal-path check above
       catches it too, but the resolved-path check provides a clean
       error message for the escape attempt).

    The checks are best-effort: a missing path is allowed through (the
    symlink won't fire if it doesn't exist; ``_stage_files`` will
    surface a real I/O error if needed).
    """
    if repo_root is None:
        return
    # An embedded NUL is named for what it is. Without this, ``resolve()``
    # below raises ``ValueError: embedded null byte``, the escape check
    # catches it, and the commit fails claiming the path is "outside
    # repository root" — a lie the agent cannot act on, while the artifact
    # is retained and the next pass fails the same way.
    if "\x00" in normalized_path:
        raise ValueError(
            f"Refusing to stage a path containing an embedded NUL in commit scope: "
            f"{normalized_path!r}"
        )
    repo_root_resolved = Path(repo_root).resolve(strict=False)
    candidate = repo_root / normalized_path
    if candidate.is_symlink():
        raise ValueError(f"Refusing to stage symlink in commit scope: {normalized_path!r}")
    for parent in candidate.parents:
        if parent == repo_root_resolved or parent == Path(repo_root):
            break
        if parent.is_symlink():
            raise ValueError(
                f"Refusing to stage path whose parent directory is a symlink "
                f"chain escape in commit scope: {normalized_path!r}"
            )
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(repo_root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to stage path outside repository root in commit scope: {normalized_path!r}"
        ) from exc


def _dedupe_repo_relative_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        normalized = _normalize_repo_relative_path(path)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _normalize_repo_relative_path(raw_path: str) -> str:
    path = PurePosixPath(raw_path.strip())
    if not raw_path.strip():
        raise ValueError("Commit artifact paths must not be empty")
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Commit artifact path '{raw_path}' must stay inside the repository root")
    normalized = path.as_posix()
    if normalized in {".", ""}:
        raise ValueError("Commit artifact paths must reference a concrete repository file")
    return normalized


def _changed_commit_paths(repo_root: Path) -> list[str]:
    """Compatibility seam for callers that need a fresh evidence snapshot."""
    return list(build_commit_evidence_bundle(repo_root).changed_files)


def _repo_has_commit_work(repo_root: Path) -> bool:
    return has_uncommitted_changes(repo_root)


def cleanup_commit_message_artifacts(repo_root: Path) -> None:
    """Remove commit message artifacts left by a prior commit phase."""
    delete_commit_message_artifacts(repo_root)


def commit_effect(workspace_root: Path) -> CommitEffect:
    """Build a CommitEffect pointing at the standard commit message artifact path."""
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    **opts: object,
) -> None:
    """Remove stale per-phase artifacts before invoking an agent.

    Planning artifacts are an exception: fresh-vs-preserve invalidation is
    owned by prompt materialization, which has the semantic context to
    distinguish fresh planning from loopback, retry, and resume. Clearing plan
    outputs again here reintroduces a second, less-informed authority and can
    delete the live plan handoff on non-fresh planning entries.
    """
    drain = cast(
        "str | None", opts.get("drain")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    policy_bundle = cast(
        "PolicyBundle | None", opts.get("policy_bundle")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    effective_drain = drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if policy_bundle is not None
        else None
    )
    if required_artifact is not None and required_artifact.artifact_type == "plan":
        return
    for path in phase_output_artifact_paths(phase, drain=drain, policy_bundle=policy_bundle):
        workspace.remove(path)


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
    """Return paths of all output artifacts produced by a phase."""
    paths: list[str] = []
    effective_drain = drain or phase
    ra = (
        build_required_artifacts(policy_bundle.artifacts).get(effective_drain)
        if policy_bundle is not None
        else None
    )
    if ra is not None:
        paths.append(ra.artifact_path)
        if ra.markdown_path is not None:
            paths.append(ra.markdown_path)
    if policy_bundle is not None:
        phase_def = policy_bundle.pipeline.phases.get(phase)
        if phase_def is not None:
            if phase_def.parallelization is not None:
                paths.append(".agent/artifacts/parallel_development_summary.md")
            if phase_def.role == "commit" and ra is None:
                paths.append(COMMIT_MESSAGE_ARTIFACT)
    return tuple(paths)


def default_mcp_capabilities_for_phase(
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    """Return the default MCP capability set for a given phase."""
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
            agents_policy=agents_policy,
        ).capabilities
    )


repo_has_commit_work = _repo_has_commit_work
