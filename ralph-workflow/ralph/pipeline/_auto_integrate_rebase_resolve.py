from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from loguru import logger

from ralph.git.rebase.rebase import RebaseConflicts, RebaseSuccess
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_outcome import record_conflict, record_resolution_exhausted
from ralph.pipeline.auto_integrate_record import clear_record
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.status import conflict_status_bar_session
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, rebase_resolution_signature
from ralph.pipeline.integration_decision import NextAction
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import (
    StartupRetentionReason,
)
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_producer import classify_resolution_failure
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseFailed, RebaseNoOp
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import AgentRunner
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType

__all__ = [
    "RebaseResolutionResult",
    "RebaseRunResult",
    "_clear_record_if_no_inflight_op",
    "_resolve_conflicted_rebase",
    "resolve_conflicted_rebase",
    "verify_terminal_state",
]


@dataclass(frozen=True)
class RebaseRunResult:
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed
    merge_attempted: bool
    merge_outcome: MergeResult | None
    short_circuit: RebaseState | None
    skip_reason: str | None = None


def verify_terminal_state(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    try:
        post_attempt_verify(
            root,
            expected_head_sha=expected_head_sha,
            owns_resolution=owns_resolution,
        )
    except TerminalStateViolationError as exc:
        logger.error(
            "auto_integrate_rebase_merge: terminal-state invariant "
            "violation: {}; the recovery preamble will reclaim this on "
            "the next run, and the durable record is RETAINED so the "
            "next recovery can still restore the pre-attempt state. "
            "The operator should be aware the merge / fallback path "
            "leaked state.",
            exc,
        )


def resolve_conflicted_rebase(
    root: Path,
    target: str,
    rebase_stop_resolver: RebaseStopResolver,
    display: ParallelDisplay | None,
    conflict_resolution_config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> RebaseRunResult | None:
    from ralph.pipeline import auto_integrate_rebase_merge as _merge_mod

    if not _merge_mod.set_resolving_rebase(root, True):
        logger.warning(
            "auto_integrate: could not record the in-flight rebase resolution "
            "for '{}'; falling back to the endpoint merge",
            target,
        )
        return None
    blocked_reason: str | None = None
    with conflict_status_bar_session(display, root):
        from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
        from ralph.pipeline.auto_integrate_resolve import resolution_gate_record
        from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
        from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

        outcome = attempt_stop_resolution(
            None,
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target=target,
                resolver=rebase_stop_resolver,
                record=resolution_gate_record(root, target),
                conflict_config=conflict_resolution_config,
                agent_runner=agent_runner,
            ),
        )
        res: RebaseResolutionResult
        if isinstance(outcome, StopResolutionBlocked):
            blocked_reason = outcome.reason
            res = RebaseResolutionResult(
                False,
                outcome.reason,
                ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        elif isinstance(outcome, RebaseResolutionResult):
            blocked_reason = None
            res = outcome
        else:
            raise TypeError("rebase stop resolution returned an invalid result")
    if res.succeeded and not _merge_mod.set_resolving_rebase(root, False):
        logger.warning(
            "auto_integrate: could not clear completed rebase resolution flag for '{}'", target
        )
    record = _merge_mod.read_record(root)
    if blocked_reason is not None:
        return _blocked_run(record, target, blocked_reason)
    if record is not None and record.rebase_continue_pending:
        return _pending_continue_run(record, target)
    return _rebase_run_result(root, record, target, res)


def _blocked_run(
    record: IntegrationRecord | None,
    target: str,
    blocked_reason: str,
) -> RebaseRunResult:
    blocked_cause = (
        record.last_failure_cause
        if record is not None and record.last_failure_cause is not ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    return RebaseRunResult(
        rebase_outcome=RebaseConflicts(files=[]),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=record_conflict(
            reason=blocked_reason,
            target=target,
            failure_cause=blocked_cause,
        ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
    )


def _pending_continue_run(record: IntegrationRecord, target: str) -> RebaseRunResult:
    pending_reason = record.rebase_continue_error or "verified rebase continuation pending"
    pending_cause = (
        ResolutionFailureCause.CONFIG_FAILURE
        if record.last_failure_cause is ResolutionFailureCause.CONFIG_FAILURE
        else ResolutionFailureCause.CONTEXT_FAILURE
    )
    return RebaseRunResult(
        rebase_outcome=RebaseConflicts(files=[]),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=record_conflict(
            reason=pending_reason,
            target=target,
            failure_cause=pending_cause,
        ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
    )


def _rebase_outcome(result: RebaseResolutionResult) -> ResolutionOutcomeType:
    if result.succeeded:
        return Resolved()
    return classify_resolution_failure(
        terminal_reason=result.terminal_reason,
        failure_cause=result.failure_cause,
        reason=result.reason,
    )


def _conflict_run(
    target: str,
    reason: str,
    failure_cause: ResolutionFailureCause,
) -> RebaseRunResult:
    return RebaseRunResult(
        rebase_outcome=RebaseConflicts(files=[]),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=record_conflict(
            reason=reason,
            target=target,
            failure_cause=failure_cause,
        ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
    )


def _rebase_run_result(
    root: Path,
    record: IntegrationRecord | None,
    target: str,
    result: RebaseResolutionResult,
) -> RebaseRunResult:
    outcome = _rebase_outcome(result)
    failure_cause = (
        result.failure_cause
        if result.failure_cause is not ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    failure_reason = (
        result.reason or "rebase resolution incomplete; progress retained for next agent"
    )
    if record is not None and not result.succeeded:
        from ralph.pipeline.auto_integrate_record import write_record

        write_record(root, record.model_copy(update={"last_failure_cause": failure_cause}))
    match outcome:
        case Resolved():
            logger.info("auto_integrate: resolved the conflicted rebase onto '{}'", target)
            return RebaseRunResult(
                rebase_outcome=RebaseSuccess(),
                merge_attempted=False,
                merge_outcome=None,
                short_circuit=None,
            )
        case NeedsOperator():
            return _conflict_run(target, failure_reason, failure_cause)
        case RetryLater():
            return _conflict_run(target, failure_reason, failure_cause)
        case Exhausted():
            AttemptLedger(root).charge_ladder_attempt(
                rebase_resolution_signature(
                    record.pre_feature_sha if record is not None else None,
                    record.pre_target_sha if record is not None else target,
                    record.resolving_stop_sha if record is not None else None,
                ),
                NextAction.LAST_RESORT_RECOVERY.value,
            )
            return RebaseRunResult(
                rebase_outcome=RebaseConflicts(files=[]),
                merge_attempted=False,
                merge_outcome=None,
                short_circuit=record_resolution_exhausted(
                    reason=failure_reason,
                    target=target,
                    failure_cause=failure_cause,
                    terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
                ),
            )
        case ConfigFailure(file=file):
            return _conflict_run(
                target,
                f"fix {file} then run ralph --check-config",
                ResolutionFailureCause.CONFIG_FAILURE,
            )
        case _ as unreachable:
            assert_never(unreachable)


_resolve_conflicted_rebase = resolve_conflicted_rebase


def _clear_record_if_no_inflight_op(root: Path) -> None:
    from ralph.pipeline import auto_integrate_rebase_merge as _merge_mod

    try:
        clean = _merge_mod.merge_state(
            root
        ) == _merge_mod.MERGE_STATE_NONE and not _merge_mod.rebase_in_progress(root)
    except Exception as state_exc:
        logger.warning(
            "auto_integrate: could not prove no in-flight operation remains"
            " in {} ({}); retaining the durable record for startup recovery",
            root,
            state_exc,
        )
        return
    if not clean:
        logger.warning(
            "auto_integrate: in-progress merge/rebase state remains in {}"
            " after a failed integration; retaining the durable record so"
            " startup recovery can reconcile it",
            root,
        )
        return
    clear_record(root)
