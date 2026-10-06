"""Durable ownership of a verified merge awaiting its normal commit hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    commit_merge_in_progress,
    merge_state,
    staged_conflict_marker_paths,
    unmerged_paths,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record


def _git_value(root: Path, *args: str) -> str | None:
    result = run_git(args, cwd=root, label="pending-merge:identity")
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def mark_pending_merge(root: Path, target: str, *, resolving: bool = False) -> None:
    """Protect resolver edits before invoking the agent or capturing index identities."""
    record = read_record(root) or IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=_git_value(root, "rev-parse", "--verify", "HEAD") or "",
        pre_target_sha=_git_value(root, "rev-parse", "--verify", "MERGE_HEAD"),
    )
    paths = record.resolving_paths
    if resolving:
        current = unmerged_paths(root)
        if "<unmerged-path-query-failed>" not in current:
            paths = tuple(sorted(set(paths) | set(current)))
    write_record(
        root,
        record.model_copy(update={
            "resolving_merge": resolving, "merge_commit_pending": not resolving,
            "resolving_paths": paths,
        }),
    )


def prepare_pending_merge(root: Path, target: str) -> str | None:
    """Persist the exact resolved index and merge parents before attempting commit."""
    mark_pending_merge(root, target)
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    parent = _git_value(root, "rev-parse", "--verify", "MERGE_HEAD")
    tree = _git_value(root, "write-tree")
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
        record.model_copy(
            update={
                "merge_commit_head": head,
                "merge_commit_parent": parent,
                "merge_commit_tree": tree,
            }
        ),
    )
    return None


def pending_merge_identity_matches(root: Path, record: IntegrationRecord) -> bool:
    """Prove a live merge still contains exactly the verified index and parents."""
    return (
        bool(record.merge_commit_head and record.merge_commit_parent and record.merge_commit_tree)
        and merge_state(root) == MERGE_STATE_IN_PROGRESS
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.merge_commit_head
        and _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") == record.merge_commit_parent
        and _git_value(root, "write-tree") == record.merge_commit_tree
        and not unmerged_paths(root)
        and not staged_conflict_marker_paths(root)
    )


def _complete_pending_snapshot(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    """Reprove a clean resolved index only while both original merge parents remain."""
    if (
        any((record.merge_commit_head, record.merge_commit_parent, record.merge_commit_tree))
        or not record.pre_feature_sha
        or not record.pre_target_sha
        or merge_state(root) != MERGE_STATE_IN_PROGRESS
        or _git_value(root, "rev-parse", "--verify", "HEAD") != record.pre_feature_sha
        or _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") != record.pre_target_sha
        or unmerged_paths(root)
        or staged_conflict_marker_paths(root)
    ):
        return "pending merge snapshot incomplete or resolution unfinished; merge retained"
    failure = prepare_pending_merge(root, record.target)
    prepared = read_record(root)
    if failure is not None or prepared is None:
        return failure or "pending merge snapshot unreadable; merge retained"
    return prepared


def resume_pending_merge(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    """Retry only the recorded merge commit; refuse changed index or parent evidence.

    An already-created commit is accepted only when its tree and both ordered
    parents match the prepared record, covering a crash before record promotion.
    Failure retains the original record and repository state for a later retry.
    """
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
    state = merge_state(root)
    if state == MERGE_STATE_IN_PROGRESS:
        if not pending_merge_identity_matches(root, record):
            return "pending merge identity changed or conflict evidence remains; commit not retried"
        reasons: list[str] = []
        if not commit_merge_in_progress(root, on_failure=reasons.append):
            return reasons[0] if reasons else "verified merge commit remains pending"
    elif state != MERGE_STATE_NONE:
        return "pending merge state unreadable; commit not retried"
    return _promote_completed_merge(root, record)


def _promote_completed_merge(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    parents = _git_value(root, "show", "-s", "--format=%P", "HEAD")
    tree = _git_value(root, "rev-parse", "HEAD^{tree}")
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    if (
        head is None
        or tree != record.merge_commit_tree
        or parents != f"{record.merge_commit_head} {record.merge_commit_parent}"
    ):
        return "pending merge commit does not match verified tree and parents; landing withheld"
    integrated = record.model_copy(
        update={
            "phase": "integrated",
            "integrated_feature_sha": head,
            "resolving_merge": False,
            "merge_commit_pending": False,
            "merge_commit_head": None,
            "merge_commit_parent": None,
            "merge_commit_tree": None,
        }
    )
    write_record(root, integrated)
    return integrated
