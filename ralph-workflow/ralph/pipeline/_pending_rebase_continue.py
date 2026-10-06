"""Preserve a proved rebase stop while its normal continuation is blocked."""

from __future__ import annotations

from pathlib import Path

from ralph.git.merge import staged_conflict_marker_paths, unmerged_paths
from ralph.git.rebase.rebase_continuation import continue_rebase_at, rebase_in_progress_at
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record


def prepare_pending_rebase(root: Path, target: str | None = None) -> None:
    """Persist the verified index before invoking Git continuation."""
    record = read_record(root)
    if record is None:
        if target is None:
            return
        from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

        original, onto = current_rebase_identity(root)
        if not original or not onto or not rebase_in_progress_at(root):
            raise ValueError("cannot adopt rebase with unreadable original and target identities")
        record = IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=original,
            pre_target_sha=onto,
            resolving_rebase=True,
        )
    pending = record.model_copy(update={"rebase_continue_pending": True})
    write_record(root, pending)
    pending = pending.model_copy(
        update={
            "rebase_continue_head": _git_value(root, "rev-parse", "--verify", "HEAD"),
            "rebase_continue_stop": _git_value(root, "rev-parse", "--verify", "REBASE_HEAD"),
        }
    )
    write_record(root, pending)
    prepared = pending.model_copy(
        update={
            "rebase_continue_tree": _git_value(root, "write-tree"),
            "rebase_continue_metadata": _sequencer_snapshot(root),
        }
    )
    write_record(root, prepared)
    if not all(
        (
            prepared.rebase_continue_head,
            prepared.rebase_continue_stop,
            prepared.rebase_continue_tree,
        )
    ):
        raise ValueError("cannot snapshot verified rebase stop; continuation retained")


def pending_rebase_identity_matches(root: Path, record: IntegrationRecord) -> bool:
    """Prove that retrying continuation uses the same verified stop and index."""
    return (
        bool(
            record.rebase_continue_head
            and record.rebase_continue_stop
            and record.rebase_continue_tree
        )
        and rebase_in_progress_at(root)
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.rebase_continue_head
        and _git_value(root, "rev-parse", "--verify", "REBASE_HEAD") == record.rebase_continue_stop
        and _git_value(root, "write-tree") == record.rebase_continue_tree
        and not unmerged_paths(root)
        and not staged_conflict_marker_paths(root)
    )


def finish_pending_rebase(root: Path) -> None:
    """Release only the stop snapshot after Git proves continuation advanced."""
    record = read_record(root)
    if record is not None:
        if record.rebase_continue_pending and not _resolved_stop_landed(root, record):
            raise ValueError(
                "continued rebase does not match the verified stop tree; landing withheld"
            )
        write_record(
            root,
            record.model_copy(
                update={
                    "rebase_continue_pending": False,
                    "rebase_continue_metadata": (),
                    "rebase_restore_metadata": None,
                    "rebase_continue_head": None,
                    "rebase_continue_stop": None,
                    "rebase_continue_tree": None,
                    "rebase_continue_error": None,
                }
            ),
        )


def retain_pending_rebase_error(root: Path, error: Exception) -> None:
    """Persist actionable hook/lock diagnostics without changing the prepared index."""
    record = read_record(root)
    if record is not None and record.rebase_continue_pending:
        write_record(root, record.model_copy(update={"rebase_continue_error": str(error)[:2000]}))


def resume_pending_rebase(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    """Retry the exact prepared continuation and classify the resulting operation."""
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

    return _resume_prepared_rebase(root, record)


def _resume_prepared_rebase(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    if record.rebase_continue_tree is None:
        repaired = _complete_pending_rebase_snapshot(root, record)
        if repaired is None:
            return "pending rebase snapshot incomplete; continuation retained"
        record = repaired
    if rebase_in_progress_at(root) and _advanced_saved_stop(root, record):
        return _retain_next_rebase_stop(root)
    if rebase_in_progress_at(root):
        if not pending_rebase_identity_matches(root, record):
            return "pending rebase identity changed or conflict evidence remains; continuation retained"
        try:
            restore_pending_rebase_sequence(root, record)
            continue_rebase_at(root)
        except Exception as exc:
            if (
                _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
                == record.rebase_continue_stop
            ):
                retain_pending_rebase_error(root, exc)
                return str(exc)
    if rebase_in_progress_at(root):
        return _retain_next_rebase_stop(root)
    return _promote_completed_rebase(root, record)


def _promote_completed_rebase(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    if not _resolved_stop_landed(root, record):
        return "completed rebase does not prove the verified stop landed; landing withheld"
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    integrated = record.model_copy(
        update={
            "phase": "integrated",
            "integrated_feature_sha": head,
            "resolving_rebase": False,
            "rebase_continue_pending": False,
            "rebase_continue_metadata": (),
            "rebase_restore_metadata": None,
            "rebase_continue_head": None,
            "rebase_continue_stop": None,
            "rebase_continue_tree": None,
            "rebase_continue_error": None,
        }
    )
    write_record(root, integrated)
    return integrated


def _advanced_saved_stop(root: Path, record: IntegrationRecord) -> bool:
    stop = _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
    return bool(
        stop and stop != record.rebase_continue_stop and _resolved_stop_landed(root, record)
    )


def _retain_next_rebase_stop(root: Path) -> str:
    finish_pending_rebase(root)
    updated = read_record(root)
    if updated is not None:
        write_record(root, updated.model_copy(update={"resolving_rebase": True}))
    return "rebase advanced to another conflict; retained for agent continuation"


def _complete_pending_rebase_snapshot(
    root: Path, record: IntegrationRecord
) -> IntegrationRecord | None:
    """Reprove a staged stop after a failed tree query without accepting changed parents."""
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    original, onto = current_rebase_identity(root)
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    stop = _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
    if (
        not head
        or not stop
        or not original
        or not onto
        or original != record.pre_feature_sha
        or onto != record.pre_target_sha
        or (record.rebase_continue_head is not None and head != record.rebase_continue_head)
        or (record.rebase_continue_stop is not None and stop != record.rebase_continue_stop)
        or unmerged_paths(root)
        or staged_conflict_marker_paths(root)
    ):
        return None
    prepare_pending_rebase(root)
    return read_record(root)


def _resolved_stop_landed(root: Path, record: IntegrationRecord) -> bool:
    """Accept crash recovery only when the first continued commit has the proved tree."""
    if not record.rebase_continue_head or not record.rebase_continue_tree:
        return False
    if (
        _git_value(root, "rev-parse", f"{record.rebase_continue_head}^{{tree}}")
        == record.rebase_continue_tree
    ):
        from ralph.git.merge import is_ancestor

        return is_ancestor(root, record.rebase_continue_head, "HEAD")
    commits = _git_value(
        root, "rev-list", "--reverse", "--ancestry-path", f"{record.rebase_continue_head}..HEAD"
    )
    first = commits.splitlines()[0] if commits else None
    if first is None:
        return _git_value(root, "rev-parse", "HEAD^{tree}") == record.rebase_continue_tree
    return (
        _git_value(root, "rev-parse", f"{first}^{{tree}}") == record.rebase_continue_tree
        and _git_value(root, "show", "-s", "--format=%P", first) == record.rebase_continue_head
    )


def _sequencer_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    """Save the exact replay queue before Git may consume a command without updating HEAD."""
    entries: list[tuple[str, str]] = []
    for name in ("git-rebase-todo", "done", "msgnum", "end"):
        location = _git_value(root, "rev-parse", "--git-path", f"rebase-merge/{name}")
        if location is None:
            raise ValueError("cannot locate rebase sequencer state")
        path = Path(location)
        if not path.is_absolute():
            path = root / path
        if DEFAULT_FILE_BACKEND.exists(path):
            entries.append((name, DEFAULT_FILE_BACKEND.read_text(path)))
    return tuple(entries)


def restore_pending_rebase_sequence(root: Path, record: IntegrationRecord) -> None:
    """Restore consumed queue entries only when no prepared stop commit reached HEAD."""
    if not record.rebase_continue_metadata or not pending_rebase_identity_matches(root, record):
        return
    from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    if current_rebase_identity(root) != (record.pre_feature_sha, record.pre_target_sha):
        raise ValueError("rebase operation identity changed; queue retained")
    saved = dict(record.rebase_continue_metadata)
    current = dict(_sequencer_snapshot(root))
    if record.rebase_restore_metadata is None:
        original_todo = saved.get("git-rebase-todo", "")
        current_todo = current.get("git-rebase-todo", "")
        consumed = original_todo[: len(original_todo) - len(current_todo)]
        if (
            not original_todo.endswith(current_todo)
            or current.get("done", "") != saved.get("done", "") + consumed
        ):
            raise ValueError("rebase sequencer changed outside the prepared continuation; retained")
        if not _replay_commands_are_picks(root, consumed):
            raise ValueError("consumed rebase commands require supervised repair; queue retained")
        record = record.model_copy(update={"rebase_restore_metadata": tuple(current.items())})
        write_record(root, record)
    else:
        before = dict(record.rebase_restore_metadata)
        if any(
            current.get(name) not in {before.get(name), content} for name, content in saved.items()
        ):
            raise ValueError("interrupted rebase queue restoration changed; retained")
    for name, content in record.rebase_continue_metadata:
        if name not in {"git-rebase-todo", "done", "msgnum", "end"}:
            raise ValueError("invalid saved rebase sequencer entry")
        location = _git_value(root, "rev-parse", "--git-path", f"rebase-merge/{name}")
        if location is None:
            raise ValueError("cannot locate saved rebase sequencer entry")
        path = Path(location)
        if not path.is_absolute():
            path = root / path
        atomic_write_bytes_if_changed(
            DEFAULT_FILE_BACKEND,
            path,
            content.encode("utf-8"),
            tmp_path=path.with_name(path.name + ".ralph-pending"),
            sync_directory=True,
        )
    write_record(root, record.model_copy(update={"rebase_restore_metadata": None}))


def _replay_commands_are_picks(root: Path, commands: str) -> bool:
    for line in commands.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        command, separator, rest = line.partition(" ")
        oid = rest.split(maxsplit=1)[0] if rest else ""
        if (
            command != "pick"
            or not separator
            or not oid
            or _git_value(root, "cat-file", "-t", oid) != "commit"
        ):
            return False
    return True
