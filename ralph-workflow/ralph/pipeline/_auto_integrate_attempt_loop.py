
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

MAX_INTEGRATION_ATTEMPTS = 3


class _ResolveContext(Protocol):

    def __call__(
        self,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
    ) -> tuple[Path, str | None, str | None, str | None] | None:
        ...


class _EarlySkips(Protocol):

    def __call__(
        self,
        ctx: tuple[Path, str | None, str | None, str | None] | None,
        *,
        missing_target: str,
    ) -> tuple[RebaseState | None, tuple[Path, str, str] | None]:
        ...


class _ObserveConflictIdentity(Protocol):

    def __call__(self, root: Path, target: str) -> ConflictIdentity:
        ...


class _ConfiguredResolverAllowed(Protocol):

    def __call__(
        self,
        state: RebaseState,
        target: str,
        identity: ConflictIdentity,
        *,
        ledger: AgentFailureLedger | None = None,
        candidates: Sequence[str] = (),
    ) -> bool:
        ...


class _RecoverBeforeAttempt(Protocol):

    def __call__(
        self,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
        record: RebaseState | None,
        conflict_resolver: ConflictResolver | None,
        rebase_stop_resolver: RebaseStopResolver | None,
    ) -> RebaseState | None:
        ...


class _FreshenAttemptTarget(Protocol):

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
        ...


class _IntegrateOnce(Protocol):

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
        ...


class _WaitBeforeRetry(Protocol):

    def __call__(
        self,
        attempt: int,
        *,
        sleep: Callable[[float], None],
        jitter: Callable[[], float],
    ) -> None:
        ...


class _ReadRecordOutcome(Protocol):

    def __call__(self, workspace_root: Path) -> RecordReadOutcome:
        ...


@dataclass(frozen=True, slots=True)
class AttemptLoopSeams:

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
    ctx = seams.resolve_context(config, workspace_scope)
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
            attempts_exhausted = True
            logger.warning(
                "auto_integrate: fast-forward did not land after {} integration "
                "attempts; giving up until the next seam",
                MAX_INTEGRATION_ATTEMPTS,
            )
    except TerminalStateViolationError:
        raise
    except Exception as exc:
        logger.warning("auto_integrate: integration attempt failed: {}", exc)
        charged = record_refresh(
            record_skip(reason=f"unexpected failure: {exc}", target=target),
            refresh,
        )
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
