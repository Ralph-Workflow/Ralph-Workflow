from __future__ import annotations

from pathlib import Path

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import is_ancestor
from ralph.git.operation_snapshot import OperationState, observe_operation_snapshot
from ralph.git.rebase.rebase_continuation import (
    RebaseContinuationError,
    continue_rebase_at,
    rebase_in_progress_at,
)
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._landed_predicate import (
    LandedDecision,
    operation_landed,
)
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.auto_integrate_backup_refs import create_stale_resolution_snapshot
from ralph.pipeline.auto_integrate_record import (
    MARK_PENDING_REBASE_CONTINUE,
    MARK_RESOLVING_REBASE,
    IntegrationRecord,
    read_record,
    write_record,
)


def prepare_pending_rebase(root: Path, target: str | None = None) -> None:
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
    if not rebase_in_progress_at(root):
        raise ValueError(
            "no rebase is in progress; a rebase stopped outside the resolver is not "
            "recorded as a pending continuation"
        )
    pending = record.with_transition(
        MARK_PENDING_REBASE_CONTINUE,
        rebase_continue_pending=True,
    )
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
    snapshot = observe_operation_snapshot(root)
    return (
        bool(
            record.rebase_continue_head
            and record.rebase_continue_stop
            and record.rebase_continue_tree
        )
        and snapshot.operation_state is OperationState.REBASE_PAUSED
        and snapshot.head_sha == record.rebase_continue_head
        and snapshot.rebase_head == record.rebase_continue_stop
        and snapshot.index_tree == record.rebase_continue_tree
        and snapshot.stop_resolved
    )


def finish_pending_rebase(root: Path) -> None:
    record = read_record(root)
    if record is not None:
        if classify_git_operation(
            record
        ) is GitOperationKind.AWAITING_CONTINUE and not rebase_in_progress_at(root):
            promoted = _promote_completed_rebase(root, record)
            if isinstance(promoted, str):
                raise ValueError(promoted)
            return
        write_record(
            root,
            record.with_transition(
                MARK_PENDING_REBASE_CONTINUE,
                rebase_continue_pending=False,
                rebase_continue_metadata=(),
                rebase_restore_metadata=None,
                rebase_continue_head=None,
                rebase_continue_stop=None,
                rebase_continue_tree=None,
                rebase_continue_error=None,
            ),
        )


def retain_pending_rebase_error(root: Path, error: Exception) -> None:
    record = read_record(root)
    if record is not None and classify_git_operation(record) is GitOperationKind.AWAITING_CONTINUE:
        write_record(
            root,
            record.with_transition(
                MARK_PENDING_REBASE_CONTINUE,
                rebase_continue_error=str(error)[:2000],
            ),
        )


def resume_pending_rebase(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
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
    if (
        classify_git_operation(record) is GitOperationKind.AWAITING_CONTINUE
        and record.rebase_continue_tree is None
    ):
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
        except (RebaseContinuationError, OSError, RuntimeError, ValueError, KeyError) as exc:
            if (
                _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
                == record.rebase_continue_stop
            ):
                retain_pending_rebase_error(root, exc)
                return str(exc)
    if rebase_in_progress_at(root):
        return _retain_next_rebase_stop(root)
    completion = _promote_completed_rebase(root, record)
    return completion if isinstance(completion, str) else completion.record


def _promote_completed_rebase(
    root: Path,
    record: IntegrationRecord,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> CommitCompletion | str:
    decision = operation_landed(root, record)
    if decision.landed:
        head = decision.observed_head
        if head is None:
            return "completed rebase HEAD is unreadable; landing withheld"
    else:
        landed = _land_interrupted_continuation(root, record)
        if landed is None:
            return _landing_failure(decision)
        head = landed
    completion = complete_commit(
        record,
        integrated_feature_sha=head,
        resolving_rebase=False,
        rebase_continue_pending=False,
        rebase_continue_metadata=(),
        rebase_restore_metadata=None,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
        rebase_continue_error=None,
    )
    write_record(root, completion.record, backend=backend)
    return completion


def _land_interrupted_continuation(root: Path, record: IntegrationRecord) -> str | None:
    anchor = record.rebase_continue_head
    tree = record.rebase_continue_tree
    onto = record.pre_target_sha
    if not (
        record.rebase_continue_pending
        and anchor
        and tree
        and onto
        and anchor == record.pre_feature_sha
        and not rebase_in_progress_at(root)
    ):
        return None
    snapshot = observe_operation_snapshot(root)
    if not (
        snapshot.operation_state is OperationState.NONE
        and snapshot.merge_head is None
        and snapshot.head_sha == anchor
        and snapshot.index_tree == tree
        and snapshot.stop_resolved
        and _git_value(root, "cat-file", "-t", onto) == "commit"
        and _git_value(root, "rev-list", f"{onto}..{anchor}") == anchor
        and len((_git_value(root, "show", "-s", "--format=%P", anchor) or "").split()) == 1
        and not is_ancestor(root, onto, anchor)
    ):
        return None
    landed = _commit_continuation(root, anchor, tree, onto)
    if landed is None:
        return None
    branch = _git_value(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    try:
        create_stale_resolution_snapshot(root, branch, anchor, record.owning_worktree)
    except RuntimeError:
        return None
    moved = run_git(
        (
            "update-ref",
            "-m",
            "ralph: land interrupted rebase continuation",
            "HEAD",
            landed,
            anchor,
        ),
        cwd=root,
        label="pending-rebase:land-ref",
    )
    return landed if moved.returncode == 0 else None


def _commit_continuation(root: Path, anchor: str, tree: str, onto: str) -> str | None:
    identity = _git_value(root, "show", "-s", "--format=%an%x00%ae%x00%ad", "--date=raw", anchor)
    raw = _git_value(root, "cat-file", "commit", anchor)
    if identity is None or raw is None:
        return None
    name, email, date = identity.split("\0")
    headers, _separator, message = raw.partition("\n\n")
    if any(line.startswith("encoding ") for line in headers.splitlines()):
        return None
    created = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "commit-tree", tree, "-p", onto),
        cwd=root,
        label="pending-rebase:land-commit",
        options=GitRunOptions(
            env={
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_AUTHOR_DATE": date,
            },
            input_data=f"{message}\n",
        ),
    )
    commit = created.stdout.strip()
    return commit if created.returncode == 0 and commit else None


def promote_completed_pending_rebase(
    root: Path,
    record: IntegrationRecord,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> IntegrationRecord | str:
    if classify_git_operation(record) is not GitOperationKind.AWAITING_CONTINUE:
        return record
    if rebase_in_progress_at(root):
        return record
    completion = _promote_completed_rebase(root, record, backend=backend)
    if isinstance(completion, str):
        return completion
    return completion.record


def _advanced_saved_stop(root: Path, record: IntegrationRecord) -> bool:
    stop = _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
    return bool(
        stop and stop != record.rebase_continue_stop and operation_landed(root, record).landed
    )


def _retain_next_rebase_stop(root: Path) -> str:
    finish_pending_rebase(root)
    updated = read_record(root)
    if updated is not None:
        write_record(
            root,
            updated.with_transition(
                MARK_RESOLVING_REBASE,
                resolving_rebase=True,
            ),
        )
    return "rebase advanced to another conflict; retained for agent continuation"


def _complete_pending_rebase_snapshot(
    root: Path, record: IntegrationRecord
) -> IntegrationRecord | None:
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    snapshot = observe_operation_snapshot(root)
    original, onto = current_rebase_identity(root)
    head = snapshot.head_sha
    stop = snapshot.rebase_head
    if (
        not head
        or not stop
        or not original
        or not onto
        or original != record.pre_feature_sha
        or onto != record.pre_target_sha
        or (record.rebase_continue_head is not None and head != record.rebase_continue_head)
        or (record.rebase_continue_stop is not None and stop != record.rebase_continue_stop)
        or not snapshot.stop_resolved
    ):
        return None
    prepare_pending_rebase(root)
    return read_record(root)


def _landing_failure(decision: LandedDecision) -> str:
    return decision.operator_status("all recorded Git state")


def _sequencer_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
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
        record = record.with_transition(
            MARK_PENDING_REBASE_CONTINUE,
            rebase_restore_metadata=tuple(current.items()),
        )
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
    write_record(
        root,
        record.with_transition(
            MARK_PENDING_REBASE_CONTINUE,
            rebase_restore_metadata=None,
        ),
    )


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
