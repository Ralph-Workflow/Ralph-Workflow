from __future__ import annotations

from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.git.branch_relation import branch_relation
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline._auto_integrate_once import _integrate_once
from ralph.pipeline._planning_obstruction import identify_planning_obstruction
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_outcome import record_conflict, record_skip
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.commit_executor import (
    reattach_head_if_detached as _reattach_head_if_detached,
)
from ralph.pipeline.integration_resolution import (
    TreeReadinessAssessment,
    assess_tree_readiness,
    retained_integration_reason,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    WaitingOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_cleanup import PlanningBaseline, ResidueCleanupOutcome
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope


PAUSED_REBASE_RECOVERY_PREFERS_MERGE: Final[bool] = True


def capture_pre_planning_baseline(root: Path) -> PlanningBaseline | None:
    from ralph.pipeline.integration_cleanup import RunGitCleanupPort, capture_planning_baseline

    try:
        return capture_planning_baseline(root, git=RunGitCleanupPort())
    except Exception as exc:
        logger.warning("pre-planning baseline capture failed: {}", exc)
        return None


def resolve_planning_residue(
    root: Path,
    baseline: PlanningBaseline,
    *,
    target: str | None = None,
) -> ResidueCleanupOutcome | None:
    from ralph.pipeline.integration_cleanup import (
        OwnerResidueLedgerPort,
        RunGitCleanupPort,
        run_residue_cleanup,
    )

    try:
        outcome = run_residue_cleanup(
            root,
            baseline=baseline,
            git=RunGitCleanupPort(),
            ledger=OwnerResidueLedgerPort(),
            target=target,
        )
    except Exception as exc:
        logger.warning("planning residue cleanup failed: {}", exc)
        return None
    if outcome.operation_blocked or outcome.operation_unknown:
        _route_blocked_operation(root, outcome, target=target)
    return outcome


def _route_blocked_operation(
    root: Path,
    outcome: ResidueCleanupOutcome,
    *,
    target: str | None,
) -> None:
    from ralph.pipeline._integration_owner_seam import decide_seam_action
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.integration_owner import (
        RecordFactOutcome,
        ladder_rung_for_record_fact_outcome,
    )

    label = "unknown-operation" if outcome.operation_kind is None else outcome.operation_kind.name
    try:
        action = decide_seam_action(root, record=read_record(root), target=target)
    except Exception as exc:
        logger.warning("planning residue: operation-ladder routing unreadable: {}", exc)
        return
    if isinstance(action, RecordFactOutcome):
        rung = ladder_rung_for_record_fact_outcome(action)
        logger.warning(
            "planning residue cleanup deferred: git {} in progress; durable record "
            "facts failed closed ({}); preserving the work to backup refs and "
            "escalating through cleanup-ladder rung {}",
            label,
            action.value,
            rung.name.lower(),
        )
        return
    logger.warning(
        "planning residue cleanup deferred: git {} in progress; the operation ladder "
        "routes through the owner seam: {}",
        label,
        action.name,
    )


def integrate_before_planning(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    readiness: TreeReadinessAssessment | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
) -> RebaseState | None:
    root = scope.root
    if retained_integration_reason(root) is not None:
        from ralph.pipeline._integration_continuation import config_for_owned_integration

        config = config_for_owned_integration(config)
        recovered = recover_incomplete_integration(
            scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            force_retry=True,
        )
        if recovery_retained_record(recovered) or (
            recovered is not None and recovered.fast_forwarded
        ):
            return recovered
    target = (
        resolve_integration_target(config, root) if config.general.auto_integrate_enabled else None
    )
    if target is None:
        return None
    unfinished = _finish_paused_rebase(config, root, target, rebase_stop_resolver)
    if unfinished is not None:
        return unfinished
    _reattach_head_if_detached(root, config=config, auto_integrate_target=target)
    if readiness is None:
        readiness = assess_tree_readiness(root)
    if readiness.integration_ready and not branch_relation(root, target).needs_catch_up:
        return auto_integrate_after_commit(
            config,
            scope,
            state,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
            sleep=sleep,
            jitter=jitter,
        )
    staged_block = _record_staged_block(root, target, readiness=readiness)
    if staged_block is not None:
        return staged_block
    from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    return _integrate_with_force_merge(
        config,
        root,
        target,
        conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )


def _evaluate_paused_resolution(
    result: object,
    target: str,
) -> tuple[bool, RebaseState | None]:
    from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked

    if isinstance(result, StopResolutionBlocked):
        return True, record_conflict(reason=result.reason, target=target)
    match result:
        case ResolvedOutcome():
            return True, None
        case WaitingOutcome():
            return True, record_conflict(
                reason=result.external_condition or "waiting", target=target
            )
        case RetryLaterOutcome():
            return True, record_conflict(reason=result.on_change or "retry later", target=target)
        case ConfigFailureOutcome():
            return True, record_conflict(reason=str(result), target=target)
        case _:
            return False, None


def _abort_and_fallback_merge(
    config: UnifiedConfig,
    root: Path,
    target: str,
    resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    from ralph.git.merge import observe_branch_sha
    from ralph.git.rebase.rebase import abort_rebase
    from ralph.pipeline.auto_integrate_record import clear_record

    saved_feature_tip: str | None = None
    try:
        tip, ok = observe_branch_sha(root, "feature")
        if ok and tip:
            saved_feature_tip = tip
    except Exception:
        pass

    try:
        abort_rebase(repo_root=root)
        clear_record(root)
        if saved_feature_tip is not None:
            from ralph.git.subprocess_runner import run_git

            run_git(
                ("update-ref", "refs/heads/feature", saved_feature_tip),
                cwd=root,
                label="auto-integrate:update-feature-ref",
            )
        outcome, _retry = _integrate_once(
            config,
            root,
            target,
            conflict_resolver=None,
            require_in_sync=True,
            rebase_stop_resolver=resolver,
            prefer_merge=PAUSED_REBASE_RECOVERY_PREFERS_MERGE,
        )
        return outcome
    except Exception as exc:
        logger.warning("abort_rebase or merge fallback failed: {}", exc)
    return None


def _finish_paused_rebase(
    config: UnifiedConfig,
    root: Path,
    target: str,
    resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    try:
        if not rebase_in_progress(root):
            return None
    except Exception:
        return None
    from ralph.pipeline.conflict_resolution.rebase_loop import finish_resolved_rebase_stops

    if finish_resolved_rebase_stops(root, target) or resolver is None:
        return None
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_resolve import resolution_gate_record
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    result = attempt_stop_resolution(
        None,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target=target,
            resolver=resolver,
            record=read_record(root) or resolution_gate_record(root, target),
            conflict_config=config.conflict_resolution,
            force_retry=True,
        ),
    )
    handled, state = _evaluate_paused_resolution(result, target)
    if handled:
        return state
    return _abort_and_fallback_merge(config, root, target, resolver)


def _record_staged_block(
    root: Path,
    target: str,
    *,
    readiness: TreeReadinessAssessment | None = None,
) -> RebaseState | None:
    if readiness is None:
        readiness = assess_tree_readiness(root)
    if readiness.integration_ready:
        return None
    reason = (
        readiness.blocking_reasons[0]
        if readiness.blocking_reasons
        else "worktree not clean; uncommitted tracked changes deferred catch-up integration"
    )
    kind = identify_planning_obstruction(reason)
    recorded = record_skip(reason=reason, target=target)
    return recorded.model_copy(update={"last_planning_obstruction": kind.value})


def _integrate_with_force_merge(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
    display: ParallelDisplay | None,
) -> RebaseState | None:
    outcome, _retry = _integrate_once(
        config,
        root,
        target,
        conflict_resolver,
        require_in_sync=True,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )
    return outcome
