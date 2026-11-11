"""Continue retained resolver work under recovery's existing integration lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE, is_ancestor, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    MARK_RESOLVING_REBASE,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.config_file_fingerprint_watcher import (
    compute_config_fingerprint_for_workspace,
)
from ralph.pipeline.rebase_state import StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope

_REBASE_RECEIPT_ENTRIES = 2
_WAITING_STATE: dict[str, tuple[object, ...] | None] = {"last_key": None}


def reset_continuation_gate_state() -> None:
    """Reset the in-memory continuation gate state (for test isolation)."""
    _WAITING_STATE["last_key"] = None


def _emit_waiting_line(msg: str, key: tuple[object, ...] | None = None) -> None:
    if key is None or key != _WAITING_STATE["last_key"]:
        _WAITING_STATE["last_key"] = key
        logger.bind(recovery=True).info("{}", msg)


def _current_stop_identity(root: Path, record: IntegrationRecord) -> str | None:
    """Return the identity of the current stopped commit in rebase or merge."""
    if record.resolving_merge:
        try:
            if merge_state(root) == MERGE_STATE_IN_PROGRESS:
                sha = _git_value(root, "rev-parse", "--verify", "MERGE_HEAD")
                if sha:
                    return sha.strip()
                return record.pre_target_sha
        except Exception as exc:
            logger.debug("Failed to read MERGE_HEAD stop identity: {}", exc)
    try:
        if rebase_in_progress_at(root):
            sha = _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
            if sha:
                return sha.strip()
            return record.resolving_stop_sha or record.pre_target_sha
    except Exception as exc:
        logger.debug("Failed to read REBASE_HEAD stop identity: {}", exc)
    return record.resolving_stop_sha or record.pre_target_sha or record.pre_feature_sha


def compute_continuation_retry_gate_key(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: list[str] | tuple[str, ...] | None = None,
    config_path: Path | None = None,
    stop_identity: str | None = None,
) -> tuple[str | None, tuple[str, ...], str | None]:
    """Compute the (stop identity, sorted eligible-agent set, config fingerprint) key."""
    stop_id = stop_identity if stop_identity is not None else _current_stop_identity(root, record)

    eligible_agents: tuple[str, ...] = ()
    if recovery_controller is not None:
        candidates: list[str] = []
        if chain_agents is not None:
            candidates = list(chain_agents)
        else:
            candidates_set: set[str] = set()
            chain_tuple = recovery_controller._get_chain_agents(phase)
            if chain_tuple is not None:
                candidates_set.update(chain_tuple)
            snap = recovery_controller.snapshot()
            timeouts = snap.get("unavailable_timeouts")
            if isinstance(timeouts, dict):
                candidates_set.update(str(k) for k in timeouts)
            attempts = snap.get("backoff_attempts")
            if isinstance(attempts, dict):
                candidates_set.update(str(k) for k in attempts)
            candidates = sorted(candidates_set)
        available = recovery_controller.agents_now_available(phase, candidates)
        eligible_agents = tuple(sorted(available))

    cfg_fp = compute_config_fingerprint_for_workspace(root, config_path)
    return (stop_id, eligible_agents, cfg_fp)


def _continue_merge_resolution(
    root: Path,
    record: IntegrationRecord,
    target: str,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    *,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> str | None:
    matches = active_operation_matches(root, record)
    if not matches:
        return "active merge identity differs from retained ownership; operator work preserved"
    gate_key = compute_continuation_retry_gate_key(
        root,
        record,
        config,
        recovery_controller=recovery_controller,
        phase=phase,
        chain_agents=chain_agents,
        config_path=config_path,
        stop_identity=stop_identity,
    )
    effective_gate_key = record.resolution_gate_key
    if effective_gate_key is None:
        disk_record = read_record(root)
        if disk_record is not None:
            effective_gate_key = disk_record.resolution_gate_key

    if not force_retry and effective_gate_key is not None and effective_gate_key == gate_key:
        _emit_waiting_line(
            "WAITING: continuation resolution deferred; conflict identity, eligible agents, and configuration unchanged",
            key=gate_key,
        )
        return "WAITING: continuation retry gate active; conflict identity and eligible agents unchanged"

    reset_continuation_gate_state()
    if conflict_resolver is None:
        latest_record = read_record(root) or record
        write_record(root, latest_record.with_resolution_gate_key(gate_key))
        return "merge resolution retained; continuation resolver required"

    from ralph.pipeline.auto_integrate_resolve import _resolve_and_commit_with_reason

    result = _resolve_and_commit_with_reason(root, target, conflict_resolver)
    if result.outcome != "success":
        latest_record = read_record(root) or record
        write_record(root, latest_record.with_resolution_gate_key(gate_key))
        return result.reason or "merge resolution remains pending after continuation"
    return None


def _continue_rebase_resolution(
    root: Path,
    record: IntegrationRecord,
    target: str,
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    *,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> str | None:
    matches = active_operation_matches(root, record)
    if not matches:
        return "active rebase identity differs from retained ownership; operator work preserved"
    gate_key = compute_continuation_retry_gate_key(
        root,
        record,
        config,
        recovery_controller=recovery_controller,
        phase=phase,
        chain_agents=chain_agents,
        config_path=config_path,
        stop_identity=stop_identity,
    )
    effective_gate_key = record.resolution_gate_key
    if effective_gate_key is None:
        disk_record = read_record(root)
        if disk_record is not None:
            effective_gate_key = disk_record.resolution_gate_key

    if not force_retry and effective_gate_key is not None and effective_gate_key == gate_key:
        _emit_waiting_line(
            "WAITING: continuation resolution deferred; conflict identity, eligible agents, and configuration unchanged",
            key=gate_key,
        )
        return "WAITING: continuation retry gate active; conflict identity and eligible agents unchanged"

    reset_continuation_gate_state()
    if rebase_stop_resolver is None:
        latest_record = read_record(root) or record
        write_record(root, latest_record.with_resolution_gate_key(gate_key))
        return "rebase resolution retained; continuation resolver required"

    from ralph.pipeline._auto_integrate_rebase_resolve import resolve_rebase_with_config

    resolved, reason, failure_cause = resolve_rebase_with_config(
        root,
        target,
        rebase_stop_resolver,
        config.conflict_resolution if config is not None else None,
    )
    if not resolved:
        latest_record = read_record(root) or record
        write_record(root, latest_record.with_resolution_gate_key(gate_key))
        return reason or (
            f"rebase resolution remains pending after continuation (cause: {failure_cause.value})"
        )
    return None


def continue_retained_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: list[str] | tuple[str, ...] | None = None,
    config_path: Path | None = None,
    force_retry: bool = False,
    stop_identity: str | None = None,
) -> IntegrationRecord | str:
    """Resume the recorded operation and promote only a terminal Git result."""
    target = record.pre_target_sha if record.operation_kind == "target_reconcile" else record.target
    if not target:
        return "retained resolution target unreadable; progress preserved"
    if record.resolving_merge and merge_state(root) == MERGE_STATE_IN_PROGRESS:
        failure = _continue_merge_resolution(
            root,
            record,
            target,
            config,
            conflict_resolver,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            force_retry=force_retry,
            stop_identity=stop_identity,
        )
        if failure is not None:
            return failure
    elif record.resolving_rebase and rebase_in_progress_at(root):
        failure = _continue_rebase_resolution(
            root,
            record,
            target,
            config,
            rebase_stop_resolver,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            force_retry=force_retry,
            stop_identity=stop_identity,
        )
        if failure is not None:
            return failure
    return _promote_completed_resolution(root, record)


def active_operation_matches(root: Path, record: IntegrationRecord) -> bool:
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    if rebase_in_progress_at(root):
        return current_rebase_identity(root) == (record.pre_feature_sha, record.pre_target_sha)
    return (
        merge_state(root) == MERGE_STATE_IN_PROGRESS
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.pre_feature_sha
        and _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") == record.pre_target_sha
    )


def _terminal_state_refusal(root: Path) -> str | None:
    """Verify the operation left no live markers; name the refusal when it did.

    A leftover operation marker Git still shows (cherry-pick, sequencer,
    merge state) is live Git state, not completion proof.  Named as a
    typed refusal the caller retains through the conflict /
    identity-mismatch mapping -- never as "lacks proof" withholding,
    which must not exist.
    """
    from ralph.pipeline.auto_integrate_recovery_terminal import (
        TerminalStateViolationError,
        post_attempt_verify,
    )

    try:
        post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    except TerminalStateViolationError as exc:
        return (
            f"retained integration completion unproven; Git still shows an operation marker: {exc}"
        )
    return None


def _promote_completed_resolution(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    refusal = _terminal_state_refusal(root)
    if refusal is None and (rebase_in_progress_at(root) or merge_state(root) != MERGE_STATE_NONE):
        refusal = "retained integration operation remains active; continuation required"
    if refusal is not None:
        return refusal
    current = read_record(root)
    if current is None:
        return "retained integration ownership unreadable after continuation"
    if current.is_integrated:
        return current
    if current.rebase_continue_pending:
        from ralph.pipeline._pending_rebase_continue import resume_pending_rebase

        return resume_pending_rebase(root, current)
    legacy = _legacy_completed_rebase(root, current)
    if legacy is not None:
        write_record(root, legacy.record)
        return legacy.record
    replay = _stale_replay_record(root, current)
    return current if replay is None else replay


promote_completed_resolution = _promote_completed_resolution


def _stale_replay_record(root: Path, record: IntegrationRecord) -> IntegrationRecord | None:
    """Mark a record whose resolution claim Git disproves for a fresh replay.

    Only a feature-integration record is replayed here: a target
    reconciliation owns its work in another worktree and is continued by
    the reconciliation path, not by the feature replay.
    """
    from ralph.pipeline._owned_landing_reintegration import retained_resolution_is_stale

    if not (
        record.operation_kind == "feature_integrate"
        and (record.resolving_rebase or record.resolving_merge)
        and retained_resolution_is_stale(root, record)
    ):
        return None
    replay = record.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=False,
        resolving_merge=False,
    ).with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
        owning_worktree=record.owning_worktree or str(root),
    )
    write_record(root, replay)
    return replay


def _legacy_completed_rebase(root: Path, record: IntegrationRecord) -> CommitCompletion | None:
    if not record.resolving_rebase or not record.pre_target_sha:
        return None
    branch = _git_value(root, "symbolic-ref", "--quiet", "HEAD")
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    if branch is None or head is None:
        return None
    # Distinct reflog contract #1 (commondir reflog): this reads
    # commondir/logs/refs/heads/<branch> -- one row of a multi-part legacy
    # rebase completion proof, combined with branch, HEAD, the rebase-finish
    # subject, and ancestry. This is a crash-recovery seam, NOT diagnostic
    # evidence; do not reuse it for the worktree-private logs/HEAD seam
    # (see _auto_integrate_recovery_git_state.detached_branch_name).
    history = run_git(
        ("reflog", "show", "--format=%H%x00%gs", "-2", branch),
        cwd=root,
        label="recovery:legacy-rebase-receipt",
    )
    rows = history.stdout.splitlines()
    if history.returncode or len(rows) != _REBASE_RECEIPT_ENTRIES:
        return None
    latest, _, action = rows[0].partition("\0")
    previous, _, _action = rows[1].partition("\0")
    if (
        latest != head
        or previous != record.pre_feature_sha
        or action != f"rebase (finish): {branch} onto {record.pre_target_sha}"
        or not is_ancestor(root, record.pre_target_sha, head)
    ):
        return None
    return complete_commit(
        record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=False),
        integrated_feature_sha=head,
    )


def recover_before_attempt(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    prior_attempt: RebaseState | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    from ralph.pipeline.auto_integrate_recovery import (
        recover_incomplete_integration,
        recovery_retained_record,
    )
    from ralph.pipeline.integration_resolution import retained_integration_reason

    if retained_integration_reason(scope.root) is None:
        return None
    recovered = recover_incomplete_integration(
        scope,
        config=config,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
    )
    if recovery_retained_record(recovered):
        if prior_attempt is not None:
            return prior_attempt.set_recovery_record_retained(
                StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
            )
        return recovered
    if recovered is not None and recovered.fast_forwarded:
        return recovered
    if not config.general.auto_integrate_enabled and recovered is not None:
        from ralph.pipeline.auto_integrate import auto_integrate_after_commit

        return auto_integrate_after_commit(
            config_for_owned_integration(config),
            scope,
            recovered,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
        )
    return None


def config_for_owned_integration(config: UnifiedConfig) -> UnifiedConfig:
    """Disabling new integrations does not abandon an already-owned landing."""
    return (
        config
        if config.general.auto_integrate_enabled
        else config.model_copy(
            update={
                "general": config.general.model_copy(update={"auto_integrate_enabled": True}),
            }
        )
    )
