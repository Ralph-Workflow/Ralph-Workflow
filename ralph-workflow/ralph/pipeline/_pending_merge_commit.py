from __future__ import annotations

from typing import TYPE_CHECKING, Final

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git import subprocess_runner
from ralph.git.merge import (
    commit_merge_in_progress,
)
from ralph.git.operation_snapshot import (
    OperationSnapshot,
    OperationState,
    observe_operation_snapshot,
)
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
from ralph.pipeline.auto_integrate_record import (
    MARK_PENDING_MERGE_COMMIT,
    MARK_RESOLVING_MERGE,
    IntegrationRecord,
    read_record,
    write_record,
)

_HEX_LEN: Final[int] = 40


def _is_valid_oid(val: str) -> bool:
    return len(val) == _HEX_LEN and all(c in "0123456789abcdefABCDEF" for c in val)


def _read_gitdir_ref_file(root: Path, filename: str) -> str | None:
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _ = _resolve_gitdir_paths(root)
        if gitdir is None:
            return None
        target_file = gitdir / filename
        if target_file.is_file():
            sha = target_file.read_text(encoding="utf-8", errors="replace").strip()
            if _is_valid_oid(sha):
                return sha
    except Exception as exc:
        logger.debug("Failed reading direct gitdir ref {}: {}", filename, exc)
    return None


def _direct_git_value(root: Path, args: tuple[str, ...]) -> str | None:
    if args in (("rev-parse", "--verify", "HEAD"), ("rev-parse", "HEAD")):
        try:
            from ralph.git.operations import get_head_sha

            return get_head_sha(root)
        except Exception:
            return None
    if args in (("rev-parse", "--verify", "MERGE_HEAD"), ("rev-parse", "MERGE_HEAD")):
        return _read_gitdir_ref_file(root, "MERGE_HEAD")
    if args in (("rev-parse", "--verify", "REBASE_HEAD"), ("rev-parse", "REBASE_HEAD")):
        return _read_gitdir_ref_file(root, "REBASE_HEAD") or _read_gitdir_ref_file(
            root, "rebase-merge/stopped-sha"
        )
    return None


def _git_value(root: Path, *args: str) -> str | None:
    direct = _direct_git_value(root, args)
    if direct is not None:
        return direct
    result = subprocess_runner.run_git(args, cwd=root, label="pending-merge:identity")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def mark_pending_merge(root: Path, target: str, *, resolving: bool = False) -> None:
    snapshot = observe_operation_snapshot(root, target=target)
    record = read_record(root) or IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=snapshot.head_sha or "",
        pre_target_sha=snapshot.merge_head,
    )
    paths = record.resolving_paths
    if resolving and snapshot.unmerged_query_ok:
        paths = tuple(sorted(set(paths) | set(snapshot.unmerged_paths)))
    write_record(
        root,
        record.with_transition(
            MARK_RESOLVING_MERGE,
            resolving_merge=resolving,
            resolving_paths=paths,
        ).with_transition(
            MARK_PENDING_MERGE_COMMIT,
            merge_commit_pending=not resolving,
        ),
    )


def prepare_pending_merge(root: Path, target: str) -> str | None:
    mark_pending_merge(root, target)
    snapshot = observe_operation_snapshot(root, target=target)
    head = snapshot.head_sha
    parent = snapshot.merge_head
    tree = snapshot.index_tree
    if head is None or parent is None or tree is None:
        return "cannot identify verified merge index and parents; resolved merge retained"
    record = read_record(root) or IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=head,
        pre_target_sha=parent,
    )
    write_record(
        root,
        record.with_transition(
            MARK_PENDING_MERGE_COMMIT,
            merge_commit_head=head,
            merge_commit_parent=parent,
            merge_commit_tree=tree,
        ),
    )
    return None


def pending_merge_identity_matches(root: Path, record: IntegrationRecord) -> bool:
    snapshot = observe_operation_snapshot(root, target=record.target)
    return _pending_merge_identity_matches(record, snapshot)


def _pending_merge_identity_matches(
    record: IntegrationRecord,
    snapshot: OperationSnapshot,
) -> bool:
    return (
        bool(record.merge_commit_head and record.merge_commit_parent and record.merge_commit_tree)
        and snapshot.operation_state is OperationState.MERGE_PAUSED
        and snapshot.head_sha == record.merge_commit_head
        and snapshot.merge_head == record.merge_commit_parent
        and snapshot.index_tree == record.merge_commit_tree
        and snapshot.unmerged_query_ok
        and not snapshot.unmerged_paths
        and not snapshot.staged_conflict_marker_paths
    )


def _complete_pending_snapshot(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    snapshot = observe_operation_snapshot(root, target=record.target)
    if (
        any((record.merge_commit_head, record.merge_commit_parent, record.merge_commit_tree))
        or not record.pre_feature_sha
        or not record.pre_target_sha
        or snapshot.operation_state is not OperationState.MERGE_PAUSED
        or snapshot.head_sha != record.pre_feature_sha
        or snapshot.merge_head != record.pre_target_sha
        or not snapshot.unmerged_query_ok
        or snapshot.unmerged_paths
        or snapshot.staged_conflict_marker_paths
    ):
        return "pending merge snapshot incomplete or resolution unfinished; merge retained"
    failure = prepare_pending_merge(root, record.target)
    prepared = read_record(root)
    if failure is not None or prepared is None:
        return failure or "pending merge snapshot unreadable; merge retained"
    return prepared


def resume_pending_merge(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    if record.repair_commit_controls is not None:
        from ralph.pipeline._pending_repair_edits import capture_commit_controls

        if capture_commit_controls(root) != record.repair_commit_controls:
            return "commit hooks or signing configuration changed during repair; restore checks before retry"
    if record.repair_pending_diff is not None:
        from ralph.pipeline._pending_repair_edits import resume_source_repair

        repaired_source = resume_source_repair(root, record)
        if isinstance(repaired_source, str):
            return repaired_source
        record = repaired_source

    return _resume_prepared_merge(root, record)


def _resume_prepared_merge(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    if not all((record.merge_commit_head, record.merge_commit_parent, record.merge_commit_tree)):
        repaired = _complete_pending_snapshot(root, record)
        if isinstance(repaired, str):
            return repaired
        record = repaired
    snapshot = observe_operation_snapshot(root, target=record.target)
    if snapshot.operation_state is OperationState.MERGE_PAUSED:
        if not _pending_merge_identity_matches(record, snapshot):
            return "pending merge identity changed or conflict evidence remains; commit not retried"
        reasons: list[str] = []
        if not commit_merge_in_progress(root, on_failure=reasons.append):
            return reasons[0] if reasons else "verified merge commit remains pending"
    elif snapshot.operation_state is OperationState.UNKNOWN:
        return "pending merge state unreadable; commit not retried"
    completion = _promote_completed_merge(root, record)
    if isinstance(completion, str):
        return completion
    return completion.record


def _promote_completed_merge(root: Path, record: IntegrationRecord) -> CommitCompletion | str:
    parents = _git_value(root, "show", "-s", "--format=%P", "HEAD")
    tree = _git_value(root, "rev-parse", "HEAD^{tree}")
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    if (
        head is None
        or tree != record.merge_commit_tree
        or parents != f"{record.merge_commit_head} {record.merge_commit_parent}"
    ):
        return "pending merge commit does not match verified tree and parents; landing withheld"
    completion = complete_commit(
        record.with_transition(
            MARK_RESOLVING_MERGE,
            resolving_merge=False,
        ).with_transition(
            MARK_PENDING_MERGE_COMMIT,
            merge_commit_pending=False,
            merge_commit_head=None,
            merge_commit_parent=None,
            merge_commit_tree=None,
        ),
        integrated_feature_sha=head,
    )
    write_record(root, completion.record)
    return completion
