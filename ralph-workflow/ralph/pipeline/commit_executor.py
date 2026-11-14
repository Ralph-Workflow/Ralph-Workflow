from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.checked_accessors import as_mapping
from ralph.config import _env
from ralph.config.enums import Verbosity
from ralph.display.parallel_display import (
    ParallelDisplay,
    get_display_context,
    resolve_active_display,
)
from ralph.git._lock_recovery import reap_stale_locks
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
from ralph.git.commit_review import (
    add_to_git_exclude,
    is_recognized_secret_path,
)
from ralph.git.operations import (
    CommitTreeMismatchError,
    create_commit,
    create_commit_with_hooks,
    get_head_sha,
    has_uncommitted_changes,
    list_changed_paths,
    stage_all,
    write_tree,
)
from ralph.git.operations import (
    stage_files as _stage_files,
)
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
    read_commit_message_payload_from_path,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline import _commit_executor_artifacts, _commit_failure_pending, _commit_scope
from ralph.pipeline._auto_integrate_recovery_git_state import (
    common_git_dir,
    rebase_bookkeeping_dir,
)
from ralph.pipeline._commit_executor_branch import (
    _is_git_worktree,
    _is_head_detached,
    _reattach_head_if_detached,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import CommitResidualEvent, Event, PipelineEvent
from ralph.policy.models import PolicyBundle
from ralph.workspace.scope import CONFIG_DIR_NAME


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
    return _commit_executor_artifacts.phase_output_artifact_paths(
        phase, drain=drain, policy_bundle=policy_bundle
    )


def default_mcp_capabilities_for_phase(
    phase: str, *, agents_policy: AgentsPolicy | None = None
) -> set[str]:
    return _commit_executor_artifacts.default_mcp_capabilities_for_phase(
        phase, agents_policy=agents_policy
    )


if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import TypeGuard

    from ralph.display.context import DisplayContext
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace import FsWorkspace

_CommitScopeResolution = _commit_scope._CommitScopeResolution
_commit_include_paths_from_changed = _commit_scope._commit_include_paths_from_changed
_resolve_commit_scope = _commit_scope._resolve_commit_scope

_PORCELAIN_STATUS_PREFIX_LEN = 3
_HEX_SHA_LEN = 40
_SECRET_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.*.local",
    "secrets.yml",
    "credentials.json",
)

type _EnvGetter = Callable[[str], str | None]
_DEFAULT_ENV_GETTER: _EnvGetter = _env.getenv

_PROPOSAL_REF_PREFIX = "refs/ralph/proposals/"
_REJECTED_PROPOSAL_REF_PREFIX = "refs/ralph/rejected-proposals/"
_PROPOSAL_TREE_ARGS = ("for-each-ref", "--format=%(refname) %(tree)", _PROPOSAL_REF_PREFIX)
_COMMITTED_TREE_ARGS = ("log", "--format=%T", "HEAD", "--branches", "--tags", "--remotes")


def _git_stdout(repo_root: Path, args: tuple[str, ...], label: str) -> str:
    try:
        result = run_git(args, cwd=repo_root, label=label)
    except Exception:
        return ""
    return result.stdout if result.returncode == 0 else ""


def _proposal_sweep(
    repo_root: Path, opts: dict[str, object]
) -> tuple[str | None, tuple[str, ...], int]:
    raw_refs = _git_stdout(repo_root, _PROPOSAL_TREE_ARGS, "commit:proposal-refs")
    raw_committed = (
        _git_stdout(repo_root, _COMMITTED_TREE_ARGS, "commit:committed-trees")
        if raw_refs.strip()
        else ""
    )
    trees: dict[str, str] = {}
    for line in raw_refs.splitlines():
        parts = line.strip().partition(" ")
        if parts[0] and parts[2]:
            trees[parts[0]] = parts[2]
    committed = set(raw_committed.splitlines())
    keep_refs = [ref for ref, tree in sorted(trees.items()) if tree not in committed]
    raw_rejected = _git_stdout(
        repo_root,
        ("for-each-ref", "--format=%(refname)", _REJECTED_PROPOSAL_REF_PREFIX),
        "commit:rejected-proposals",
    )
    for raw_marker in raw_rejected.splitlines():
        marker = raw_marker.strip()
        if not marker or not marker.startswith(_REJECTED_PROPOSAL_REF_PREFIX):
            continue
        proposal_ref = _PROPOSAL_REF_PREFIX + marker[len(_REJECTED_PROPOSAL_REF_PREFIX) :]
        if proposal_ref not in keep_refs:
            keep_refs.append(proposal_ref)
    rollback_ref = opts.get("rollback_ref")
    if isinstance(rollback_ref, str) and rollback_ref.strip() not in keep_refs:
        keep_refs.insert(0, rollback_ref.strip())
    raw_run_id = opts.get("run_id")
    run_ids = {ref.split("/")[3] for ref in keep_refs if ref.startswith(_PROPOSAL_REF_PREFIX)} - {
        ""
    }
    run_id = raw_run_id.strip() if isinstance(raw_run_id, str) and raw_run_id.strip() else None
    if run_id is None and len(run_ids) == 1:
        run_id = run_ids.pop()
    limit = _proposal_retention_limit(repo_root, opts)
    prefix = _PROPOSAL_REF_PREFIX if run_id is None else f"{_PROPOSAL_REF_PREFIX}{run_id}"
    protected = sum(1 for ref in keep_refs if ref == prefix or ref.startswith(f"{prefix}/"))
    return run_id, tuple(keep_refs), max(0, limit - protected)


def _proposal_retention_limit(repo_root: Path, opts: dict[str, object]) -> int:
    bundle = opts.get("policy_bundle")
    if isinstance(bundle, PolicyBundle):
        limit = bundle.pipeline.commit_review.proposal_retention
        if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1:
            return limit
    try:
        from ralph.policy.loader import load_toml

        data = load_toml(repo_root / CONFIG_DIR_NAME / "pipeline.toml", kind="pipeline")
        commit_review = as_mapping(data.get("commit_review"), field="commit_review")
        value = commit_review.get("proposal_retention")
    except Exception:
        value = None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    from ralph.policy.loader import cached_default_pipeline_policy

    return cached_default_pipeline_policy().commit_review.proposal_retention


def _prune_proposals(repo_root: Path, opts: dict[str, object]) -> None:
    run_id, keep_refs, max_retained = _proposal_sweep(repo_root, opts)
    _prune_fn = opts.get("prune_fn")
    with suppress(Exception):
        if callable(_prune_fn):
            _prune_fn(repo_root, run_id=run_id, keep_refs=keep_refs, max_retained=max_retained)
        else:
            from ralph.git.proposal_seal import prune

            prune(repo_root, run_id=run_id, keep_refs=keep_refs, max_retained=max_retained)


def _render_commit_message_via_display(repo_root: Path, display_context: object) -> None:
    display: object | None = None
    if isinstance(display_context, ParallelDisplay):
        display = display_context
    elif display_context is not None:
        try:
            display = resolve_active_display(None, cast("DisplayContext", display_context))
        except Exception:
            display = None
    if display is None:
        return
    with suppress(Exception):
        cast("ParallelDisplay", display).emit_commit_message(repo_root)


def _record_display_outcome(display: object | None, outcome: str, **opts: object) -> None:
    target: object | None = display
    if target is None:
        ctx = opts.get("display_context")
        if isinstance(ctx, ParallelDisplay):
            target = ctx
        elif ctx is not None:
            with suppress(Exception):
                target = resolve_active_display(None, cast("DisplayContext", ctx))
    if target is not None and hasattr(target, "record_artifact_outcome"):
        with suppress(Exception):
            cast("ParallelDisplay", target).record_artifact_outcome(outcome)


def _is_write_tree_fn(value: object) -> TypeGuard[Callable[[Path | str], str]]:
    return callable(value)


def _is_create_commit_with_hooks_fn(value: object) -> TypeGuard[_CreateCommitWithHooksFn]:
    return callable(value)


if TYPE_CHECKING:

    class _CreateCommitFn(Protocol):
        def __call__(
            self,
            repo_root: Path | str,
            message: str,
            *,
            expected_head: str,
        ) -> CommitCreationResult: ...

    class _CreateCommitWithHooksFn(Protocol):
        def __call__(
            self,
            repo_root: Path | str,
            message: str,
            *,
            expected_head: str,
            expected_tree_sha: str,
        ) -> CommitCreationResult: ...

    class _StageAllFn(Protocol):
        def __call__(self, repo_root: Path | str) -> None: ...

    class _HasCommitWorkFn(Protocol):
        def __call__(self, repo_root: Path) -> bool: ...

    class _RenderCommitMessageFn(Protocol):
        def __call__(self, repo_root: Path, display_context: object) -> None: ...


def get_last_commit_error() -> str | None:
    return _commit_failure_pending.pending_reason()


def set_last_commit_error(error: str | None) -> None:
    _commit_failure_pending.record_failure(error)


reattach_head_if_detached = _reattach_head_if_detached


def _attempt_repair_git_state(repo_root: Path, **opts: object) -> bool:
    if not _is_git_worktree(repo_root):
        return False
    repaired = False
    if _is_head_detached(repo_root) and _reattach_head_if_detached(repo_root, **opts):
        repaired = True

    git_dir = rebase_bookkeeping_dir(repo_root) or (repo_root / ".git")
    if git_dir.is_dir():
        common_dir = common_git_dir(repo_root)
        for lock_path in reap_stale_locks(git_dir, common_dir, operation="commit-repair"):
            logger.info("Removed stale git lock: {}", lock_path)
            repaired = True
    return repaired


COMMIT_TREE_SHA_MISMATCH_CODE = "COMMIT_TREE_SHA_MISMATCH"
COMMIT_APPROVAL_MISSING_CODE = "COMMIT_APPROVAL_MISSING"


class CommitApprovalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _verify_approved_tree(
    repo_root: Path,
    approved_tree_sha: str,
    write_tree_fn: Callable[[Path | str], str] | None,
) -> None:
    writer = write_tree_fn if write_tree_fn is not None else write_tree
    actual_tree = writer(repo_root)
    if actual_tree != approved_tree_sha:
        raise CommitApprovalError(
            COMMIT_TREE_SHA_MISMATCH_CODE,
            f"tree SHA {actual_tree} does not match approved tree SHA {approved_tree_sha}",
        )


def _perform_commit_creation(
    repo_root: Path,
    message: str,
    payload: dict[str, object],
    create_commit_fn: _CreateCommitFn,
    stage_all_fn: _StageAllFn,
    repair_opts: Mapping[str, object],
    expected_tree_sha: str | None = None,
    require_approved_tree: bool = False,
    write_tree_fn: Callable[[Path | str], str] | None = None,
) -> str:

    approved_tree_sha: str | None = expected_tree_sha
    if approved_tree_sha is None and require_approved_tree:
        raise CommitApprovalError(
            COMMIT_APPROVAL_MISSING_CODE,
            "no approved tree SHA for this commit attempt; the commit is refused",
        )

    def _create(root: Path, commit_message: str, *, expected_head: str) -> CommitCreationResult:
        if approved_tree_sha is None:
            return create_commit_fn(str(root), commit_message, expected_head=expected_head)
        create_with_hooks = repair_opts.get("create_commit_with_hooks_fn")
        hook_committer: _CreateCommitWithHooksFn = (
            create_with_hooks
            if _is_create_commit_with_hooks_fn(create_with_hooks)
            else create_commit_with_hooks
        )
        try:
            return hook_committer(
                str(root),
                commit_message,
                expected_head=expected_head,
                expected_tree_sha=approved_tree_sha,
            )
        except CommitTreeMismatchError as exc:
            raise CommitApprovalError(COMMIT_TREE_SHA_MISMATCH_CODE, str(exc)) from exc

    def _stage_and_create() -> CommitCreationResult:
        head = get_head_sha(repo_root)
        _stage_commit_scope(repo_root, payload, stage_all_fn)
        if approved_tree_sha is not None and write_tree_fn is not None:
            _verify_approved_tree(repo_root, approved_tree_sha, write_tree_fn)
        return _create(repo_root, message, expected_head=head)

    try:
        result = _stage_and_create()
    except CommitApprovalError:
        raise
    except Exception:
        if _attempt_repair_git_state(repo_root, **repair_opts):
            logger.info("Retrying commit creation after git state repair")
            result = _stage_and_create()
        else:
            raise
    if result.status is not CommitCreationStatus.CREATED or result.sha is None:
        if _attempt_repair_git_state(repo_root, **repair_opts):
            logger.info("Retrying commit creation after git state repair")
            expected_head = get_head_sha(repo_root)
            _stage_commit_scope(repo_root, payload, stage_all_fn)
            if approved_tree_sha is not None and write_tree_fn is not None:
                _verify_approved_tree(repo_root, approved_tree_sha, write_tree_fn)
            with suppress(Exception):
                result = _create(repo_root, message, expected_head=expected_head)
        if result.status is not CommitCreationStatus.CREATED or result.sha is None:
            err_msg = result.error or f"commit creation returned {result.status.value}"
            logger.warning("Commit was not created: {}", err_msg)
            raise RuntimeError(err_msg)
    if result.sha is None:
        raise RuntimeError("commit creation produced no sha")
    return result.sha


def _handle_post_commit_actions(
    repo_root: Path,
    sha: str,
    display: ParallelDisplay | None,
    verbosity: Verbosity,
    opts: dict[str, object],
) -> None:
    _prune_proposals(repo_root, opts)

    with suppress(Exception):
        _render_commit_fn = cast(
            "_RenderCommitMessageFn",
            opts.get("render_commit_message_fn")
            if callable(opts.get("render_commit_message_fn"))
            else _render_commit_message_via_display,
        )
        render_target: object = display if display is not None else get_display_context(display)
        _render_commit_fn(repo_root, render_target)

    if verbosity != Verbosity.QUIET:
        _record_display_outcome(display, f"sha={sha[:8]}", **opts)
    with suppress(Exception):
        cleanup_commit_message_artifacts(repo_root)


def execute_commit_effect(
    effect: CommitEffect,
    repo_root: Path,
    display: ParallelDisplay | None = None,
    **opts: object,
) -> Event:
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
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
    _raw_write_tree = opts.get("write_tree_fn")
    _write_tree_fn: Callable[[Path | str], str] | None = (
        _raw_write_tree if _is_write_tree_fn(_raw_write_tree) else None
    )
    try:
        payload = read_commit_message_payload_from_path(Path(effect.message_file))
        message = read_commit_message_from_path(Path(effect.message_file)) or ""
        if payload is None or not message:
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
            expected_tree_sha=effect.expected_tree_sha,
            require_approved_tree=effect.require_approved_tree,
            write_tree_fn=_write_tree_fn,
        )
        set_last_commit_error(None)
        logger.info("Created commit: {}", sha[:8])
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        _commit_failure_pending.record_failure(err_msg, effect.expected_tree_sha)
        logger.error("Commit failed ({}): {}", type(exc).__name__, exc)
        _record_display_outcome(display, "failed", **opts)
        return PipelineEvent.COMMIT_FAILURE

    _handle_post_commit_actions(repo_root, sha, display, verbosity, opts)
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
    return PipelineEvent.COMMIT_SUCCESS


def _has_content(message_file: str) -> bool:
    path = Path(message_file)
    if not DEFAULT_FILE_BACKEND.exists(path):
        return False
    return bool(DEFAULT_FILE_BACKEND.read_text(path, encoding="utf-8").strip())


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


def _changed_commit_paths(repo_root: Path) -> list[str]:
    return sorted(set(list_changed_paths(repo_root)))


def _repo_has_commit_work(repo_root: Path) -> bool:
    return has_uncommitted_changes(repo_root)


def cleanup_commit_message_artifacts(repo_root: Path) -> None:
    delete_commit_message_artifacts(repo_root)


def commit_effect(workspace_root: Path, expected_tree_sha: str | None = None) -> CommitEffect:
    return CommitEffect(
        message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT),
        expected_tree_sha=expected_tree_sha,
    )


def clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    **opts: object,
) -> None:
    _commit_executor_artifacts.clear_phase_output_artifacts(workspace, phase, **opts)


repo_has_commit_work = _repo_has_commit_work
