
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, abort_merge, branch_sha, merge_state
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
)
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_diagnostic import (
    busy_refusal_outcome as _canonical_busy_refusal_outcome,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline._recovery_fast_forward_landing import (
    _land_and_reconcile,
    _record_skip,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_recovery_legacy import (
    _reclaim_unowned_stale_rebase,
    legacy_rebase_startup_block,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    _continue_fast_forward_from_record,
    _maybe_release_diagnostic,
    _read_outcome_or_skip,
    _recover_pending_merge,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    TerminalStateViolationError,
    _head_matches_sha,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_remote_publication,
    remote_sync_enabled,
    remote_target_name,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_director import DEFAULT_REMOTE
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.state import PipelineState
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "TerminalStateViolationError",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "post_attempt_verify",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction | RecordFactOutcome:
    return _canonical_busy_refusal_outcome(retry_deadline, clock=clock)


class _StartupRecoveryRoute(StrEnum):

    RECOVERY_OWNS_OPERATION = "recovery_owns_operation"
    LANDING_CONTINUATION = "landing_continuation"
    DEFER_INTERVENTION = "defer_intervention"
    DEFER_RETRY = "defer_retry"


def _startup_recovery_route(
    action: IntegrationAction | RecordFactOutcome,
    record: IntegrationRecord,
) -> _StartupRecoveryRoute:
    if (
        classify_git_operation(record) is not GitOperationKind.NONE
        or record.operation_kind == "target_reconcile"
        or record.reintegrate_pending
    ):
        return _StartupRecoveryRoute.RECOVERY_OWNS_OPERATION
    if record.phase == "integrated":
        return _StartupRecoveryRoute.LANDING_CONTINUATION
    if isinstance(action, RecordFactOutcome):
        return _StartupRecoveryRoute.DEFER_INTERVENTION
    if action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY:
        return _StartupRecoveryRoute.DEFER_RETRY
    if action.value == "require_intervention":
        return _StartupRecoveryRoute.DEFER_INTERVENTION
    return _StartupRecoveryRoute.RECOVERY_OWNS_OPERATION


def recovery_retained_record(state: RebaseState | None) -> bool:
    return state is not None and state.recovery_record_retained


def build_recovery_context(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None,
    *,
    display_context: DisplayContext | None = None,
    state: PipelineState | None = None,
) -> tuple[ConflictResolver | None, RebaseStopResolver | None, RecoveryController | None]:
    if config is None:
        return None, None, None
    try:
        from ralph.agents.registry import AgentRegistry
        from ralph.display.context import make_display_context
        from ralph.display.parallel_display import resolve_active_display
        from ralph.pipeline.auto_integrate_agent import (
            build_agent_conflict_resolver,
            build_agent_rebase_stop_resolver,
        )
        from ralph.pipeline.factory import DefaultPipelineFactory
        from ralph.pipeline.run_loop import build_recovery_controller
        from ralph.pipeline.runner import load_policy_bundle_for_run

        display_ctx = display_context or make_display_context()
        active_display = resolve_active_display(None, display_ctx)
        pipeline_deps = DefaultPipelineFactory().build(config, display_ctx)
        policy_bundle = load_policy_bundle_for_run(workspace_scope, config)
        registry = AgentRegistry.from_config(config)

        if state is None:
            checkpoint_file = workspace_scope.root / ".agent" / "checkpoint.json"
            if checkpoint_file.exists():
                try:
                    from ralph.pipeline.checkpoint import load as load_checkpoint

                    state = load_checkpoint(checkpoint_file)
                except Exception:
                    state = None
            if state is None:
                from ralph.pipeline.state import PipelineState

                state = PipelineState.from_policy(policy_bundle.pipeline)

        controller, _ = build_recovery_controller(state, policy_bundle, config)
        strategy_history = (
            state.rebase.conflict_strategies_tried
            if (
                state is not None
                and state.rebase is not None
                and hasattr(state.rebase, "conflict_strategies_tried")
            )
            else ()
        )

        conflict_resolver = build_agent_conflict_resolver(
            policy_bundle=policy_bundle,
            registry=registry,
            display=active_display,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            display_context=display_ctx,
            strategy_history=strategy_history,
        )
        rebase_stop_resolver = build_agent_rebase_stop_resolver(
            policy_bundle=policy_bundle,
            registry=registry,
            display=active_display,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            display_context=display_ctx,
            strategy_history=strategy_history,
        )
        return conflict_resolver, rebase_stop_resolver, controller
    except Exception as exc:
        logger.debug("Failed building recovery context: {}", exc)
        return None, None, None


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    recovery_controller: RecoveryController | None = None,
    force_retry: bool = False,
) -> RebaseState | None:
    with integration_transaction(Path(workspace_scope.root)) as outcome:
        if not outcome.acquired:
            return _record_skip(
                reason=outcome.describe("integration worktree busy; recovery will retry"),
                target=None,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        startup_publication: RebaseState | None = None
        if config is not None and remote_sync_enabled(config):
            target_obj = _configured_target(config)
            if isinstance(target_obj, str) and target_obj:
                startup_publication = reconcile_remote_publication(
                    config,
                    Path(workspace_scope.root),
                    target_obj,
                    remote=remote_target_name(config),
                    rebase_stop_resolver=rebase_stop_resolver,
                )

        record_outcome = read_record_outcome(Path(workspace_scope.root), backend=backend)
        if record_outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            reason = (
                "durable integration record unreadable; escalating through the "
                "autonomous fallback ladder; record retained, bytes preserved"
            ) + (
                f" ({record_outcome.unreadable_reason})" if record_outcome.unreadable_reason else ""
            )
            return RebaseState(
                last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
                last_reason=reason,
                last_target=None,
                recovery_record_retained=True,
                recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            )

        early = _read_outcome_or_skip(Path(workspace_scope.root), backend)
        if early is not None:
            return early
        record = _maybe_release_diagnostic(Path(workspace_scope.root), workspace_scope, backend)
        if isinstance(record, RebaseState):
            return record
        recovered = _recover_incomplete_integration_owned(
            workspace_scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            backend=backend,
            recovery_controller=recovery_controller,
            force_retry=force_retry,
        )
        if (
            recovered is None
            and startup_publication is not None
            and (
                startup_publication.last_push_status == "pushed"
                or (
                    startup_publication.last_publication_decision is not None
                    and startup_publication.last_publication_decision
                    != PublicationDecision.PUBLICATION_TERMINAL
                )
            )
        ):
            return startup_publication
        return recovered


def _recover_incomplete_integration_owned(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    recovery_controller: RecoveryController | None = None,
    force_retry: bool = False,
) -> RebaseState | None:
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root, backend=backend)
        if record is None:
            return _recover_without_durable_record(root)

        action = decide_seam_action(
            root,
            record=record,
            agent_active=False,
            ledger_path=None,
            retained=True,
            target=_configured_target(config) if config is not None else record.target,
            remote=remote_target_name(config) if config is not None else DEFAULT_REMOTE,
        )

        route = _startup_recovery_route(action, record)

        if route is _StartupRecoveryRoute.LANDING_CONTINUATION:
            return _recover_landing_from_record(
                root, record, config, conflict_resolver, rebase_stop_resolver
            )

        if route is _StartupRecoveryRoute.DEFER_INTERVENTION:
            deferred: RebaseState | None = RebaseState(
                last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
                last_reason=(
                    "recovery: durable record requires autonomous ladder escalation; "
                    "record retained, escalating through the fallback ladder"
                ),
                last_target=record.target,
                recovery_record_retained=True,
                recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            )
        elif route is _StartupRecoveryRoute.DEFER_RETRY:
            deferred = _record_skip(
                reason="recovery: integration ladder deferred; durable record retained for retry",
                target=record.target,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        else:
            deferred = None
        if deferred is not None:
            return deferred

        return _recover_pending_merge(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
            recovery_controller=recovery_controller,
            force_retry=force_retry,
        )

    except Exception as exc:
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}",
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )


def _recover_without_durable_record(root: Path) -> RebaseState | None:
    retained = retained_integration_reason(root)
    if retained is not None:
        return _record_skip(
            reason=retained,
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )
    return _reclaim_unowned_stale_rebase(root)


def _recover_landing_from_record(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    if rebase_in_progress(root) or merge_state(root) != MERGE_STATE_NONE:
        return _record_skip(
            reason="recovery: later Git operation is not owned by completed landing; finish it before retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
            record_retained=True,
        )
    return _continue_fast_forward_from_record(
        root,
        record,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )


__all__ = [
    "MERGE_STATE_NONE",
    "REFRESH_UNREACHABLE",
    "IntegrationRecord",
    "TerminalStateViolationError",
    "_clear_record",
    "_head_matches_sha",
    "_land_and_reconcile",
    "_read_record",
    "_refresh_target",
    "abort_merge",
    "abort_rebase_discarding_progress",
    "ancestry_state",
    "apply_record_transition",
    "branch_sha",
    "build_recovery_context",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "merge_state",
    "post_attempt_verify",
    "read_record_outcome",
    "rebase_in_progress",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


