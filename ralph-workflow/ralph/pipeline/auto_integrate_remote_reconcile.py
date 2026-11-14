from __future__ import annotations

import contextlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.git.merge import WORKTREE_FOUND, branch_sha, worktree_lookup
from ralph.git.operations import find_main_worktree_root, get_head_sha, is_repo_clean
from ralph.git.rebase.rebase import (
    RebaseNoOp,
    RebaseSuccess,
    rebase_in_progress,
    rebase_onto,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_reclaim import reclaim_dirty_target_worktree
from ralph.pipeline._target_reconciliation_handoff import finish_target_substep
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    IntegrationRecord,
    bind_integration_record_root,
    clear_record,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStopResolver,
    current_rebase_identity,
    resolution_session_from_config,
)
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
    assert_never,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


CLEAN_ABORTED_RECONCILIATION_CONFLICT = "cleanly aborted conflict:"


@dataclass(frozen=True)
class ReconciliationOutcome:
    reconciled: bool
    reason: str
    cleanly_aborted: bool = False

    def __iter__(self) -> Iterator[bool | str]:
        yield self.reconciled
        yield self.reason


def reconcile_target_onto_remote(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    conflict_resolution_config: UnifiedConfig | None = None,
    reclaim_target_worktree: bool = True,
) -> ReconciliationOutcome:
    try:
        owner = _reconciliation_owner(repo_root, target)
    except Exception:
        owner = None
    if owner is not None:
        with integration_transaction(owner) as outcome:
            if not outcome.acquired or (
                owner != repo_root and retained_integration_reason(owner) is not None
            ):
                return ReconciliationOutcome(
                    False,
                    outcome.describe("target worktree busy; reconciliation will retry"),
                )
            checked_owner, pre_target_sha, reason = _reconciliation_preconditions(
                repo_root,
                target,
                remote,
                reclaim_target_worktree=reclaim_target_worktree,
                expected_owner=owner,
            )
            if reason is not None or checked_owner != owner or pre_target_sha is None:
                return ReconciliationOutcome(
                    False,
                    reason or f"target '{target}' changed owner before reconciliation",
                )
            return _reconcile_owned_target(
                repo_root,
                owner,
                target,
                remote,
                pre_target_sha,
                rebase_stop_resolver,
                conflict_resolution_config,
            )

    return _reconcile_unowned_target(
        repo_root,
        target,
        remote,
        rebase_stop_resolver=rebase_stop_resolver,
        conflict_resolution_config=conflict_resolution_config,
    )


def _reconcile_owned_target(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    pre_target_sha: str,
    rebase_stop_resolver: RebaseStopResolver | None,
    conflict_resolution_config: UnifiedConfig | None,
    *,
    is_temporary: bool = False,
) -> ReconciliationOutcome:
    record = IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=pre_target_sha,
        pre_target_sha=branch_sha(owner, f"{remote}/{target}"),
        operation_kind="target_reconcile",
        owning_worktree=str(owner),
        initiating_feature_sha=get_head_sha(repo_root)
        if owner.resolve() != repo_root.resolve()
        else None,
    )
    try:
        write_record(repo_root, record)
    except Exception as exc:
        return ReconciliationOutcome(
            False, f"reconciliation of {target} deferred: recovery record unavailable: {exc}"
        )
    try:
        outcome = rebase_onto(f"{remote}/{target}", repo_root=owner)
        if isinstance(outcome, (RebaseSuccess, RebaseNoOp)) and not rebase_in_progress(owner):
            return _clear_successful_reconciliation_record(repo_root, target, remote)
        if rebase_in_progress(owner) and rebase_stop_resolver is not None:
            return _resolve_owned_reconciliation(
                repo_root,
                owner,
                target,
                remote,
                record,
                rebase_stop_resolver,
                conflict_resolution_config,
                is_temporary=is_temporary,
            )
        if rebase_in_progress(owner):
            abort_rebase_discarding_progress(owner)
    except Exception as exc:
        return _abort_restore_or_retain_record(
            repo_root, owner, target, remote, pre_target_sha, str(exc), is_temporary=is_temporary
        )
    return _abort_restore_or_retain_record(
        repo_root, owner, target, remote, pre_target_sha, "conflicted", is_temporary=is_temporary
    )


def _resolve_owned_reconciliation(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    record: IntegrationRecord,
    resolver: RebaseStopResolver,
    conflict_resolution_config: UnifiedConfig | None,
    *,
    is_temporary: bool = False,
) -> ReconciliationOutcome:
    _original, onto = current_rebase_identity(owner)
    write_record(
        repo_root,
        record.with_transition(
            MARK_RESOLVING_REBASE,
            resolving_rebase=True,
            pre_target_sha=onto,
        ),
    )
    detail = "resolution interrupted"
    try:
        with bind_integration_record_root(owner, repo_root):
            outcome = _resolve_rebase_stop(
                owner,
                f"{remote}/{target}",
                resolver,
                conflict_resolution_config,
                record,
            )
        match outcome:
            case ResolvedOutcome():
                if not rebase_in_progress(owner):
                    return _clear_successful_reconciliation_record(repo_root, target, remote)
            case WaitingOutcome(external_condition=condition, resumes_when=resumes_when):
                detail = f"waiting for {condition}; resumes when {resumes_when}"
            case RetryLaterOutcome(until=until, on_change=on_change):
                detail = on_change or f"retry after {until}"
            case NextStrategyOutcome(strategy=strategy):
                detail = f"continuing with autonomous strategy: {strategy}"
            case ConfigFailureOutcome(file=file):
                detail = f"fix {file} then run ralph --check-config"
            case UnknownOutcome(error=error):
                detail = f"unknown reconciliation outcome: {error}"
            case _ as unreachable:
                assert_never(unreachable)
    except Exception as exc:
        detail = str(exc)
    if is_temporary:
        try:
            if rebase_in_progress(owner):
                abort_rebase_discarding_progress(owner)
            clear_record(repo_root)
        except Exception:
            pass
        return ReconciliationOutcome(
            False,
            f"reconciliation of {target} with {remote}/{target} "
            f"{CLEAN_ABORTED_RECONCILIATION_CONFLICT} {detail}",
            cleanly_aborted=True,
        )
    current = read_record(repo_root)
    if current is not None and current.resolution_gate_key is not None:
        write_record(repo_root, current.with_resolution_gate_key(None))
    return ReconciliationOutcome(
        False,
        f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}",
    )


def _resolve_rebase_stop(
    owner: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: UnifiedConfig | None,
    record: IntegrationRecord,
) -> StopResolutionOutcome:
    if resolve_rebase_in_progress is not _DEFAULT_RESOLVE_REBASE_IN_PROGRESS:
        from ralph.pipeline.conflict_resolution.rebase_loop import (
            resolution_session_from_config,
        )

        session = resolution_session_from_config(config) if config is not None else None
        raw_res: object = None
        try:
            raw_res = resolve_rebase_in_progress(owner, target, resolver, session=session)
        except TypeError:
            fallback_fn = cast(
                "Callable[[Path, str, RebaseStopResolver], object]",
                resolve_rebase_in_progress,
            )
            raw_res = fallback_fn(owner, target, resolver)
        if isinstance(raw_res, bool):
            return (
                ResolvedOutcome()
                if raw_res
                else RetryLaterOutcome(on_change="rebase resolution failed")
            )
        if isinstance(
            raw_res,
            (
                ResolvedOutcome,
                WaitingOutcome,
                RetryLaterOutcome,
                NextStrategyOutcome,
                ConfigFailureOutcome,
                UnknownOutcome,
            ),
        ):
            return raw_res
        return (
            ResolvedOutcome()
            if bool(raw_res)
            else RetryLaterOutcome(on_change="rebase resolution failed")
        )
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    result = attempt_stop_resolution(
        None,
        StopResolutionTrigger(
            name="rebase_stop",
            root=owner,
            target=target,
            resolver=resolver,
            record=read_record(owner) or record,
            conflict_config=None if config is None else config.conflict_resolution,
        ),
    )
    if isinstance(result, StopResolutionBlocked):
        return WaitingOutcome(result.reason, "resolution attempt key changes")
    if not isinstance(
        result,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return UnknownOutcome(TypeError("rebase stop resolution returned an invalid result"))
    return result


def _clear_successful_reconciliation_record(
    repo_root: Path, target: str, remote: str
) -> ReconciliationOutcome:
    try:
        retained = read_record(repo_root)
        if retained is not None:
            finish_target_substep(repo_root, retained)
        else:
            clear_record(repo_root)
    except Exception as exc:
        return ReconciliationOutcome(
            False,
            f"reconciliation of {target} with {remote}/{target} retained for recovery: "
            f"record cleanup failed: {exc}",
        )
    return ReconciliationOutcome(True, "")


_TEMP_WORKTREE_PREFIX = "ralph-reconcile-"


def _is_temporary_reconciliation_worktree(path: Path) -> bool:
    return path.name.startswith(_TEMP_WORKTREE_PREFIX)


def _cleanup_temporary_worktree(
    primary_root: Path,
    temp_dir: Path,
    repo_root: Path,
    pre_target_sha: str | None = None,
) -> None:
    with contextlib.suppress(Exception):
        if rebase_in_progress(temp_dir):
            abort_rebase_discarding_progress(temp_dir)

    with contextlib.suppress(Exception):
        run_git(
            ("worktree", "remove", "--force", str(temp_dir)),
            cwd=primary_root,
            label="reconciliation:remove-temp-worktree",
        )

    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    with contextlib.suppress(Exception):
        run_git(
            ("worktree", "prune"),
            cwd=primary_root,
            label="reconciliation:prune-temp-worktree",
        )

    with contextlib.suppress(Exception):
        retained = read_record(repo_root)
        if retained is not None and retained.owning_worktree == str(temp_dir):
            clear_record(repo_root)


def _reconciliation_owner(repo_root: Path, target: str) -> Path | None:
    try:
        primary_root = find_main_worktree_root(repo_root)
        verdict, owner = worktree_lookup(primary_root, target)
    except Exception:
        return None

    if verdict != WORKTREE_FOUND or owner is None:
        return None

    if _is_temporary_reconciliation_worktree(owner):
        with integration_transaction(owner) as outcome:
            if not outcome.acquired:
                return owner
            _cleanup_temporary_worktree(primary_root, owner, repo_root)
            with contextlib.suppress(Exception):
                verdict, owner = worktree_lookup(primary_root, target)
                if verdict == WORKTREE_FOUND and owner is not None:
                    return owner
            return None

    return owner


def _unowned_target_preconditions(
    primary_root: Path, target: str, remote: str
) -> tuple[str | None, str | None, str | None]:
    remote_ref = f"{remote}/{target}"
    try:
        pre_sha = branch_sha(primary_root, target)
        remote_sha = branch_sha(primary_root, remote_ref)
    except Exception:
        return None, None, f"target '{target}' is unavailable for reconciliation"
    if pre_sha is None:
        return None, None, f"target '{target}' is unavailable for reconciliation"
    if remote_sha is None:
        return (
            None,
            None,
            f"target '{target}' is unavailable for reconciliation: missing {remote_ref}",
        )
    return pre_sha, remote_sha, None


def _reconcile_in_temporary_workspace(
    *,
    repo_root: Path,
    primary_root: Path,
    target: str,
    remote: str,
    pre_target_sha: str,
    rebase_stop_resolver: RebaseStopResolver | None,
    conflict_resolution_config: UnifiedConfig | None,
) -> ReconciliationOutcome:
    safe_target = target.replace("/", "_")
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{_TEMP_WORKTREE_PREFIX}{safe_target}-"))

    add_res = run_git(
        ("worktree", "add", str(temp_dir), target),
        cwd=primary_root,
        label="reconciliation:add-temp-worktree",
    )
    if add_res.returncode != 0:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        with contextlib.suppress(Exception):
            run_git(
                ("worktree", "prune"),
                cwd=primary_root,
                label="reconciliation:prune-failed-temp-worktree",
            )
        return ReconciliationOutcome(
            False,
            f"target '{target}' could not allocate reconciliation workspace: "
            f"{add_res.stderr.strip() or add_res.stdout.strip()}",
        )

    try:
        with integration_transaction(temp_dir) as outcome:
            if not outcome.acquired:
                return ReconciliationOutcome(
                    False,
                    outcome.describe("target worktree busy; reconciliation will retry"),
                )

            wt_target_sha = branch_sha(temp_dir, target)
            if wt_target_sha is None or wt_target_sha != pre_target_sha:
                return ReconciliationOutcome(
                    False, f"target '{target}' changed before reconciliation"
                )

            return _reconcile_owned_target(
                repo_root,
                temp_dir,
                target,
                remote,
                pre_target_sha,
                rebase_stop_resolver,
                conflict_resolution_config,
                is_temporary=True,
            )
    finally:
        _cleanup_temporary_worktree(primary_root, temp_dir, repo_root, pre_target_sha)


def _reconcile_unowned_target(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    conflict_resolution_config: UnifiedConfig | None = None,
) -> ReconciliationOutcome:
    try:
        primary_root = find_main_worktree_root(repo_root)
    except Exception:
        return ReconciliationOutcome(False, f"target '{target}' is unavailable for reconciliation")

    pre_target_sha, remote_target_sha, reason = _unowned_target_preconditions(
        primary_root, target, remote
    )
    if reason is not None or pre_target_sha is None or remote_target_sha is None:
        return ReconciliationOutcome(
            False, reason or f"target '{target}' is unavailable for reconciliation"
        )

    if pre_target_sha == remote_target_sha:
        return ReconciliationOutcome(True, "")

    return _reconcile_in_temporary_workspace(
        repo_root=repo_root,
        primary_root=primary_root,
        target=target,
        remote=remote,
        pre_target_sha=pre_target_sha,
        rebase_stop_resolver=rebase_stop_resolver,
        conflict_resolution_config=conflict_resolution_config,
    )


def _reconciliation_preconditions(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    reclaim_target_worktree: bool,
    expected_owner: Path,
) -> tuple[Path | None, str | None, str | None]:
    try:
        verdict, owner = worktree_lookup(find_main_worktree_root(repo_root), target)
        if verdict != WORKTREE_FOUND or owner is None:
            return None, None, f"target '{target}' has no owning worktree for reconciliation"
        if owner != expected_owner:
            return None, None, f"target '{target}' changed owner before reconciliation"
        if not is_repo_clean(owner) and (
            not reclaim_target_worktree or reclaim_dirty_target_worktree(owner, target) is None
        ):
            return (
                None,
                None,
                f"target worktree is dirty; skipped reconciliation of {remote}/{target}",
            )
        pre_target_sha = branch_sha(owner, target)
    except Exception:
        return None, None, f"target '{target}' is unavailable for reconciliation"
    if pre_target_sha is None:
        return None, None, f"target '{target}' disappeared before reconciliation"
    return owner, pre_target_sha, None


def _abort_restore_or_retain_record(
    repo_root: Path,
    owner: Path,
    target: str,
    remote: str,
    pre_target_sha: str,
    detail: str,
    *,
    is_temporary: bool = False,
) -> ReconciliationOutcome:
    try:
        if rebase_in_progress(owner):
            abort_rebase_discarding_progress(owner)
        if rebase_in_progress(owner) and not is_temporary:
            return ReconciliationOutcome(
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}",
            )
        if branch_sha(owner, target) != pre_target_sha:
            if not is_temporary:
                return ReconciliationOutcome(
                    False,
                    f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                    "target moved during reconciliation",
                )
            with contextlib.suppress(Exception):
                run_git(
                    ("update-ref", f"refs/heads/{target}", pre_target_sha),
                    cwd=owner,
                    label="reconciliation:restore-target",
                )
    except Exception as exc:
        if not is_temporary:
            return ReconciliationOutcome(
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                f"restore failed: {exc}",
            )
    try:
        retained = read_record(repo_root)
        if retained is not None:
            finish_target_substep(repo_root, retained)
        else:
            clear_record(repo_root)
    except Exception as exc:
        if not is_temporary:
            return ReconciliationOutcome(
                False,
                f"reconciliation of {target} with {remote}/{target} retained for recovery: {detail}; "
                f"record cleanup failed: {exc}",
            )
    return ReconciliationOutcome(
        False,
        f"reconciliation of {target} with {remote}/{target} "
        f"{CLEAN_ABORTED_RECONCILIATION_CONFLICT} {detail}",
        cleanly_aborted=True,
    )


__all__ = [
    "CLEAN_ABORTED_RECONCILIATION_CONFLICT",
    "ReconciliationOutcome",
    "reconcile_target_onto_remote",
]
