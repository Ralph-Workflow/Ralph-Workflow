"""The bounded integration attempt loop of the auto-integrate engine.

Auto-integration runs at several seams -- after every commit, at every
clean phase boundary, before planning -- and every one of them asks the
same two questions: may this attempt rebase onto the target, and, when
the fast-forward loses a race with a moving mainline, may it try again?

This module owns that bounded attempt loop and nothing else: how many
attempts it makes, what it refreshes between them, when it gives up and
how the spent budget is reported to the operator. The engine that decides
WHICH seam runs -- and the landing the loop drives -- stays in
:mod:`ralph.pipeline.auto_integrate`.

Why the collaborators arrive as arguments
-----------------------------------------

The engine's collaborators (``observe_conflict_identity``,
``wait_before_retry``, ``read_record_outcome`` and the engine's own
context and skip helpers) are patched as attributes of
:mod:`ralph.pipeline.auto_integrate`. A module-level import here would
silently ignore every such patch, so the engine hands them over in
:class:`AttemptLoopSeams`, built from its own module namespace at CALL
time: the seam the loop uses is the seam the engine patched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.pipeline._auto_integrate_config import configured_target
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_budget_seam import (
    apply_completed_attempt_budget,
    carry_budget_through_skip,
    charge_failed_attempt,
)
from ralph.pipeline.auto_integrate_conflict_budget import resolve_failure_ledger
from ralph.pipeline.auto_integrate_context import record_refresh
from ralph.pipeline.auto_integrate_outcome import record_skip
from ralph.pipeline.auto_integrate_record import RecordReadOutcomeDisposition
from ralph.pipeline.auto_integrate_recovery import TerminalStateViolationError
from ralph.pipeline.auto_integrate_resolution_state import CONFLICT_RESOLUTION_STRATEGIES
from ralph.pipeline.integration_actions import current_clock
from ralph.pipeline.rebase_state import StartupRetentionReason

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
    from ralph.pipeline.auto_integrate_record import RecordReadOutcome
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.workspace.scope import WorkspaceScope

#: Maximum end-to-end integration attempts per commit. Attempt N+1 runs
#: only when attempt N completed a rebase/merge but the fast-forward did
#: not land (AC-08); the retry re-integrates onto the moved tip. The
#: exhausted budget is not silent: the record names it (see
#: :func:`record_attempt_budget_spent`).
MAX_INTEGRATION_ATTEMPTS = 3


class _ResolveContext(Protocol):
    """Resolves the (root, branch, target, refresh) integration context."""

    def __call__(
        self,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
    ) -> tuple[Path, str | None, str | None, str | None] | None:
        """Return the integration context, or ``None`` for a no-op seam."""
        ...


class _EarlySkips(Protocol):
    """Turns the integration context into a recorded skip or a usable one."""

    def __call__(
        self,
        ctx: tuple[Path, str | None, str | None, str | None] | None,
        *,
        missing_target: str,
    ) -> tuple[RebaseState | None, tuple[Path, str, str] | None]:
        """Return the recorded skip (if any) and the narrowed context."""
        ...


class _ObserveConflictIdentity(Protocol):
    """Observes the endpoint pair that identifies one integration attempt."""

    def __call__(self, root: Path, target: str) -> ConflictIdentity:
        """Return the observed conflict identity."""
        ...


class _ConfiguredResolverAllowed(Protocol):
    """Asks the failure ledger whether this attempt may invoke the resolver."""

    def __call__(
        self,
        state: RebaseState,
        target: str,
        identity: ConflictIdentity,
        *,
        ledger: AgentFailureLedger | None = None,
        candidates: Sequence[str] = (),
    ) -> bool:
        """Return whether the resolver may run for this attempt."""
        ...


class _RecoverBeforeAttempt(Protocol):
    """Reclaims an interrupted attempt before a new one starts."""

    def __call__(
        self,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
        record: RebaseState | None,
        conflict_resolver: ConflictResolver | None,
        rebase_stop_resolver: RebaseStopResolver | None,
    ) -> RebaseState | None:
        """Return the reclaimed state, or ``None`` when there is nothing to reclaim."""
        ...


class _FreshenAttemptTarget(Protocol):
    """Returns the current remote verdict or local-fleet observation."""

    def __call__(
        self,
        config: UnifiedConfig,
        root: Path,
        target: str,
        *,
        attempt: int,
        initial_refresh: str | None,
        rebase_stop_resolver: RebaseStopResolver | None,
        prior: RebaseState | None = None,
        force_refresh: bool = False,
        ledger: AgentFailureLedger | None = None,
        candidates: Sequence[str] = (),
    ) -> tuple[RebaseState | None, str | None]:
        """Return the remote record (if any) and the refresh outcome."""
        ...


class _IntegrateOnce(Protocol):
    """Runs one rebase-or-merge integration attempt."""

    def __call__(
        self,
        config: UnifiedConfig,
        root: Path,
        target: str,
        conflict_resolver: ConflictResolver | None,
        *,
        prefer_merge: bool = False,
        refresh: str | None = None,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        display: ParallelDisplay | None = None,
        publish: bool = True,
        require_in_sync: bool = False,
        clock: Clock | None = None,
    ) -> tuple[RebaseState | None, bool]:
        """Return the attempt record and whether a landing race merits a retry."""
        ...


class _WaitBeforeRetry(Protocol):
    """Pauses before the next integration attempt."""

    def __call__(
        self,
        attempt: int,
        *,
        sleep: Callable[[float], None],
        jitter: Callable[[], float],
    ) -> None:
        """Wait for the bounded backoff of ``attempt``."""
        ...


class _ReadRecordOutcome(Protocol):
    """Reads the durable integration record and returns its closed outcome."""

    def __call__(self, workspace_root: Path) -> RecordReadOutcome:
        """Return the record outcome for ``workspace_root``."""
        ...


@dataclass(frozen=True, slots=True)
class AttemptLoopSeams:
    """The engine collaborators the attempt loop resolves at call time.

    Every field is a seam of :mod:`ralph.pipeline.auto_integrate`. The
    engine fills them from its own module namespace when it starts an
    attempt, so the loop uses the seam the engine patched -- not the one
    this module imported.
    """

    resolve_context: _ResolveContext
    early_skips: _EarlySkips
    observe_conflict_identity: _ObserveConflictIdentity
    configured_resolver_allowed: _ConfiguredResolverAllowed
    recover_before_attempt: _RecoverBeforeAttempt
    freshen_target: _FreshenAttemptTarget
    integrate_once: _IntegrateOnce
    wait_before_retry: _WaitBeforeRetry
    read_record_outcome: _ReadRecordOutcome
    jitter_value: Callable[[], float]


def record_attempt_budget_spent(record: RebaseState) -> RebaseState:
    """Append the spent-attempt-budget fact to a record's skip reason.

    The underlying concurrency reason is kept as the headline -- it is
    what an operator needs in order to act -- and the exhaustion is
    appended, so the ``auto-integrate:`` line says both WHY the landing
    kept failing and that the bounded loop stopped trying.
    """
    base = record.last_reason or "fast-forward did not land"
    return record.model_copy(
        update={
            "last_reason": (f"{base}; exhausted {MAX_INTEGRATION_ATTEMPTS} integration attempts")
        }
    )


def run_attempt_loop(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
    seams: AttemptLoopSeams,
) -> RebaseState | None:
    """Resolve context, integrate, and retry bounded fast-forward races."""
    ctx = seams.resolve_context(config, workspace_scope)
    # The refresh outcome every decision below is made against; carried
    # down so the conflict and short-circuit records say how fresh the
    # mainline pointer was too (AC-04).
    refresh: str | None = ctx[3] if ctx is not None else None
    early_skip, usable_ctx = seams.early_skips(
        ctx,
        missing_target=configured_target(config),
    )
    if early_skip is not None:
        return carry_budget_through_skip(
            early_skip,
            prior=state,
        )
    if usable_ctx is None:
        # R2/AC8: ladder rung 3 -- only the explicit disabled AC-01 path
        # reaches this bare None; enabled lookup failures are recorded above.
        return None
    root, _current_branch, target = usable_ctx

    remote_record: RebaseState | None = None
    identity = seams.observe_conflict_identity(root, target)
    strategy_attempt = state.conflict_strategy_index == 1
    effective_ledger = resolve_failure_ledger(ledger)
    allowed = seams.configured_resolver_allowed(
        state,
        target,
        identity,
        ledger=effective_ledger,
        candidates=candidates,
    )
    effective_resolver = conflict_resolver if allowed else None
    resolver_suppressed = conflict_resolver is not None and not allowed
    if resolver_suppressed:
        strategy_index = min(state.conflict_strategy_index, len(CONFLICT_RESOLUTION_STRATEGIES) - 1)
        logger.warning(
            "auto_integrate: conflict resolution budget exhausted for '{}'; "
            "moving to resolution strategy '{}'",
            target,
            CONFLICT_RESOLUTION_STRATEGIES[strategy_index],
        )

    record: RebaseState | None = None
    prefer_merge = False
    attempts_exhausted = False
    sleep = sleep if sleep is not None else current_clock().sleep
    jitter = jitter if jitter is not None else seams.jitter_value
    try:
        for attempt in range(MAX_INTEGRATION_ATTEMPTS):
            if attempt:
                seams.wait_before_retry(attempt, sleep=sleep, jitter=jitter)
            recovered = seams.recover_before_attempt(
                config,
                workspace_scope,
                record,
                effective_resolver,
                rebase_stop_resolver if allowed else None,
            )
            if recovered is not None:
                record = recovered
                break
            remote_record, refresh = seams.freshen_target(
                config,
                root,
                target,
                attempt=attempt,
                initial_refresh=refresh,
                rebase_stop_resolver=rebase_stop_resolver if allowed else None,
                prior=state,
                force_refresh=strategy_attempt,
                ledger=effective_ledger,
                candidates=candidates,
            )
            if remote_record is not None and not remote_record.freshness_safe:
                return remote_record
            identity = seams.observe_conflict_identity(root, target)
            # Rebase-first on EVERY attempt, including a retry after the conflict
            # ladder escalated: the escalation changes the RESOLUTION strategy,
            # never the order of the integration.
            record, retry_ff = seams.integrate_once(
                config,
                root,
                target,
                effective_resolver,
                prefer_merge=prefer_merge,
                refresh=refresh,
                rebase_stop_resolver=(rebase_stop_resolver if allowed else None),
                display=display,
                clock=current_clock(),
            )
            if not retry_ff:
                break
            prefer_merge = record is not None and record.last_outcome == RebaseOutcomeKind.MERGED
            if attempt + 1 < MAX_INTEGRATION_ATTEMPTS:
                logger.info(
                    "auto_integrate: fast-forward did not land on attempt {}; "
                    "re-integrating onto the moved target",
                    attempt + 1,
                )
                continue
            # Last iteration: the range ends here and NOTHING
            # re-integrates. Promising a retry that never runs, and
            # recording only the last fast-forward skip reason, left the
            # operator with no way to tell "the target moved once" from
            # "the target kept moving until I ran out of attempts".
            attempts_exhausted = True
            logger.warning(
                "auto_integrate: fast-forward did not land after {} integration "
                "attempts; giving up until the next seam",
                MAX_INTEGRATION_ATTEMPTS,
            )
    except TerminalStateViolationError:
        # R6/AC-06: a terminal-state violation is loud, not silent. The
        # durable record is RETAINED so the recovery preamble can reclaim
        # the leaked state using the recorded pre-attempt SHA. Re-raise so
        # the outer guard propagates the failure instead of recording a
        # skip the operator would never correlate with the leak.
        raise
    except Exception as exc:
        # Caught here, not only in the caller's broad guard: this is
        # the first frame knowing the target/identity to scope by.
        logger.warning("auto_integrate: integration attempt failed: {}", exc)
        charged = record_refresh(
            record_skip(reason=f"unexpected failure: {exc}", target=target),
            refresh,
        )
        # Conditional expression, not an if-statement: this handler already
        # sits at the function's PLR0912 branch budget.
        charged = (
            charged.set_recovery_record_retained(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK)
            if seams.read_record_outcome(root).disposition
            in (
                RecordReadOutcomeDisposition.PRESENT,
                RecordReadOutcomeDisposition.UNREADABLE,
                RecordReadOutcomeDisposition.BUSY,
            )
            else charged
        )
        return charge_failed_attempt(
            charged,
            prior=state,
            target=target,
            identity=identity,
        )
    if record is None:
        # The normal local seam can be a no-op while remote sync still has a
        # useful visible outcome (for example a failed fetch). Preserve it.
        return remote_record
    if remote_record is not None:
        record = record.model_copy(
            update={
                "last_remote_sync": remote_record.last_remote_sync,
                "last_remote": remote_record.last_remote,
                "last_refresh": remote_record.last_refresh or record.last_refresh,
                "last_reason": record.last_reason or remote_record.last_reason,
                "freshness_verdict": remote_record.freshness_verdict,
                "freshness_source": remote_record.freshness_source,
                "freshness_safe": remote_record.freshness_safe,
                "freshness_target_sha": remote_record.freshness_target_sha,
            }
        )
    if attempts_exhausted:
        record = record_attempt_budget_spent(record)
    return apply_completed_attempt_budget(
        record,
        prior=state,
        target=target,
        identity=identity,
        resolver_suppressed=resolver_suppressed,
    )
