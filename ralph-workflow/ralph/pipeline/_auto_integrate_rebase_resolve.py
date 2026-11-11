"""In-place rebase-stop resolution companions for the rebase/merge engine.

:func:`resolve_conflicted_rebase` / :func:`resolve_rebase_with_config`
resolve a paused rebase commit-by-commit before the endpoint-merge
fallback is considered, :func:`verify_terminal_state` runs the shared
terminal-state probe for the merge / fallback exits, and
:data:`RebaseRunResult` is the closed outcome vocabulary of one
:func:`run_rebase_or_merge` attempt. They live in this private
companion module so :mod:`ralph.pipeline.auto_integrate_rebase_merge`
stays under the repo-structure file-size cap; the engine driver keeps
the skip-reason registry, the range routing, and the fallback branch
table, and re-exports every name defined here unchanged.

The outcome of one resolution attempt is
:class:`ralph.pipeline.rebase_resolution_result.RebaseResolutionResult`:
``(succeeded, reason, failure_cause)``. The typed cause travels with the
outcome because the reason text alone cannot tell a broken configuration
(``CONFIG_FAILURE``) apart from an unresolved conflict.

Names that tests monkeypatch on the
``ralph.pipeline.auto_integrate_rebase_merge`` module
(``set_resolving_rebase``, ``read_record``, ``resolve_rebase_in_progress``,
``rebase_in_progress``) are reached through a deferred module attribute
access, so the monkeypatch surface is unchanged by the split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.rebase.rebase import RebaseConflicts, RebaseSuccess
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_outcome import record_conflict
from ralph.pipeline.auto_integrate_record import clear_record
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.status import conflict_status_bar_session
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.rebase_state import (
    StartupRetentionReason,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseFailed, RebaseNoOp
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import AgentRunner
    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "RebaseResolutionResult",
    "RebaseRunResult",
    # Compatibility seams re-exported by
    # ``ralph.pipeline.auto_integrate_rebase_merge``.
    "_clear_record_if_no_inflight_op",
    "_resolve_conflicted_rebase",
    "_resolve_rebase_with_config",
    "resolve_conflicted_rebase",
    "resolve_rebase_with_config",
    "verify_terminal_state",
]


@dataclass(frozen=True)
class RebaseRunResult:
    """Outcome of :func:`run_rebase_or_merge`.

    ``short_circuit`` carries the state to return when the attempt stopped
    early; otherwise the engine and merge outcomes describe the path taken.
    ``skip_reason`` names the registered reason when the rebase was skipped.
    """

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
    """Run :func:`post_attempt_verify` on a merge / fallback exit path.

    Catches :class:`TerminalStateViolationError` and logs a loud
    ``ERROR`` -- the caller continues to record its outcome (a
    merge / fallback can legitimately finish with a feature that
    already moved, so the only invariant we can check here is the
    absence of stale in-progress markers). Extracted into its
    own helper so the three call sites in this module share one
    diagnostic shape.
    """
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


class _RebaseResolutionOutcome(tuple[bool, str | None]):
    """The two-value compatibility outcome of a rebase-resolution attempt.

    The engine driver re-exports the two-value resolver seam and its
    callers unpack exactly two values, so that shape stays available
    here. The typed cause rides along as an attribute so a caller that
    needs it does not lose it; the canonical three-value transport is
    :class:`ralph.pipeline.rebase_resolution_result.RebaseResolutionResult`.
    """

    failure_cause: ResolutionFailureCause

    def __new__(
        cls,
        succeeded: bool,
        reason: str | None,
        failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
    ) -> _RebaseResolutionOutcome:
        obj = super().__new__(cls, (succeeded, reason))
        obj.failure_cause = failure_cause
        return obj

    @property
    def succeeded(self) -> bool:
        return bool(self[0])

    @property
    def reason(self) -> str | None:
        val = self[1]
        return str(val) if isinstance(val, str) else None


def resolve_conflicted_rebase(
    root: Path,
    target: str,
    rebase_stop_resolver: RebaseStopResolver,
    display: ParallelDisplay | None,
    conflict_resolution_config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> RebaseRunResult | None:
    """Resolve the paused rebase and retain unfinished agent work for continuation.

    Persist resolution ownership before launching an agent. A completed replay
    returns the clean rebase result; a verified blocked continuation or partial
    agent result returns a retained record so no endpoint fallback discards it.
    If ownership cannot be recorded before the agent starts, return control to
    the existing fallback without launching an unowned resolution session.
    """
    # Late binding through the engine-driver namespace: tests monkeypatch
    # ``auto_integrate_rebase_merge.set_resolving_rebase`` there.
    from ralph.pipeline import auto_integrate_rebase_merge as _merge_mod

    if not _merge_mod.set_resolving_rebase(root, True):
        logger.warning(
            "auto_integrate: could not record the in-flight rebase resolution "
            "for '{}'; falling back to the endpoint merge",
            target,
        )
        return None
    with conflict_status_bar_session(display, root):
        res = resolve_rebase_with_config(
            root,
            target,
            rebase_stop_resolver,
            conflict_resolution_config,
            agent_runner,
        )
        resolved = res.succeeded
        exhaustion_reason = res.reason
        failure_cause = res.failure_cause
    if resolved and not _merge_mod.set_resolving_rebase(root, False):
        logger.warning(
            "auto_integrate: could not clear completed rebase resolution flag for '{}'", target
        )
    record = _merge_mod.read_record(root)
    if record is not None and record.rebase_continue_pending:
        return RebaseRunResult(
            rebase_outcome=RebaseConflicts(files=[]),
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason=record.rebase_continue_error or "verified rebase continuation pending",
                target=target,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    if not resolved:
        effective_cause = (
            failure_cause
            if failure_cause != ResolutionFailureCause.NONE
            else ResolutionFailureCause.UNRESOLVED_CONFLICT
        )
        effective_reason = (
            exhaustion_reason or "rebase resolution incomplete; progress retained for next agent"
        )
        if record is not None:
            from ralph.pipeline.auto_integrate_record import write_record

            write_record(
                root,
                record.model_copy(update={"last_failure_cause": effective_cause}),
            )
        return RebaseRunResult(
            rebase_outcome=RebaseConflicts(files=[]),
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason=effective_reason,
                target=target,
                failure_cause=effective_cause,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    logger.info("auto_integrate: resolved the conflicted rebase onto '{}'", target)
    return RebaseRunResult(
        rebase_outcome=RebaseSuccess(),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=None,
    )


#: The engine driver imports this name; it stays the same callable.
_resolve_conflicted_rebase = resolve_conflicted_rebase


def resolve_rebase_with_config(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> RebaseResolutionResult:
    """Return rebase completion and terminal resolver-chain evidence.

    The outcome carries the typed root cause beside the reason text, so
    the run loop can name a broken configuration instead of re-deriving
    it from the prose.
    """
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    session = (
        ResolutionSession()
        if config is None
        else ResolutionSession(
            inactivity_timeout_seconds=config.inactivity_timeout_seconds,
            max_rounds_per_stop=config.max_rounds_per_stop,
            max_rebase_conflict_stops=config.max_rebase_conflict_stops,
            max_fallback_agents=config.max_fallback_agents,
            total_resolution_cap_seconds=config.total_resolution_cap_seconds,
        )
    )
    # Late binding through the engine-driver namespace: tests monkeypatch
    # ``auto_integrate_rebase_merge.resolve_rebase_in_progress`` there.
    from ralph.pipeline import auto_integrate_rebase_merge as _merge_mod

    resolved = (
        agent_runner.resolve_rebase(root, target, resolver, session=session)
        if agent_runner is not None
        else _merge_mod.resolve_rebase_in_progress(root, target, resolver, session=session)
    )
    if resolved:
        return RebaseResolutionResult(True, None, ResolutionFailureCause.NONE)
    carried_cause = (
        session.last_failure_cause
        if session.last_failure_cause != ResolutionFailureCause.NONE
        else (
            ResolutionFailureCause.CONFIG_FAILURE
            if session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE
            else ResolutionFailureCause.NONE
        )
    )
    if (
        not session.charge_conflict_budget
        or session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        # Something OTHER than the resolver failed: Ralph's own plumbing
        # (a dead tool surface, a transport loop, an unanswered relay), a
        # launch that never happened, a name the registry could not
        # produce, or an operator's wall-clock cap. Recording any of
        # those as an exhausted chain wrote durable terminal evidence
        # that the AGENTS had given up -- and the next seam then refused
        # to try, for a conflict none of them ever got to attempt.
        logger.warning(
            "auto_integrate: rebase conflict resolution for '{}' ended on {} "
            "without the resolver chain being spent; not recording it as exhausted",
            target,
            session.terminal_reason.value if session.terminal_reason else "no terminal evidence",
        )
        if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH:
            carried_reason = session.operator_action_message or session.exhaustion_reason
            if not carried_reason and session.unresolved_paths:
                from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

                carried_reason = gitlink_escalation_message(session.unresolved_paths)
        elif carried_cause == ResolutionFailureCause.CONFIG_FAILURE:
            carried_reason = session.exhaustion_reason or (
                session.terminal_reason.value if session.terminal_reason is not None else None
            )
        else:
            carried_reason = None
        return RebaseResolutionResult(False, carried_reason, carried_cause)
    return RebaseResolutionResult(
        False,
        session.exhaustion_reason,
        carried_cause
        if carried_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )


def _resolve_rebase_with_config(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> _RebaseResolutionOutcome:
    """The two-value compatibility seam over :func:`resolve_rebase_with_config`.

    ``ralph.pipeline.auto_integrate_rebase_merge`` re-exports this name
    and its callers unpack exactly two values, so the legacy shape stays
    available here. The typed cause stays reachable on the outcome's
    ``failure_cause`` attribute; the canonical three-value transport is
    :func:`resolve_rebase_with_config`.
    """
    result = resolve_rebase_with_config(root, target, resolver, config, agent_runner)
    return _RebaseResolutionOutcome(result.succeeded, result.reason, result.failure_cause)


def _clear_record_if_no_inflight_op(root: Path) -> None:
    """Clear the durable record ONLY when no in-progress operation remains.

    Terminal-state invariant: clearing the record while ``MERGE_HEAD``
    or rebase bookkeeping is still on disk ORPHANS that state --
    startup recovery reconciles only operations whose record it holds,
    so the leftover markers fail ``check_rebase_preconditions`` at
    every later seam with nothing ever able to clean them. When the
    proof of cleanliness cannot be obtained the record is retained:
    a spurious retention costs one redundant recovery pass at next
    startup, a spurious clear costs the worktree its integration
    forever.
    """
    # Late binding through the engine-driver namespace: tests monkeypatch
    # ``auto_integrate_rebase_merge.rebase_in_progress`` there.
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
