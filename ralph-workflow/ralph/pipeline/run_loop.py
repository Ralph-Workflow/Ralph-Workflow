from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

from git.exc import InvalidGitRepositoryError, NoSuchPathError
from loguru import logger

import ralph.pipeline.runner as _runner_module
from ralph._build_identity import build_identity
from ralph.agents.timeout_clock import SystemClock
from ralph.config.enums import Verbosity
from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.artifact_reader import plan_artifact_path
from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.display.parallel_display import (
    ParallelDisplay,
    build_default_display_legacy_bridge,
    emit_activity_line,
    phase_style_for_phase,
    status_text,
)
from ralph.display.status_bar import StatusBarModel
from ralph.git.proposal_seal import seal_baseline
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.onboarding import RUN_COMPLETION_STAR_CTA
from ralph.pipeline._auto_integrate_reclaim import target_worktree_lookup
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_executor import default_rebase_continuation_executor
from ralph.pipeline._integration_log_dedupe import IntegrationOutcomeLog, integration_outcome_log
from ralph.pipeline._integration_owner_seam import (
    PlanningSyncVerdict,
    decide_planning_sync,
    decide_seam_action,
)
from ralph.pipeline._integration_record_facts import busy_refusal_outcome
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline._terminal_emitter import critical
from ralph.pipeline._wait_reason import WaitReason
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    fail_open_dead_agent_probe_runner,
    seamed_dead_agent_probe_runner,
)
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.auto_integrate import recovery_retained_record
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
    emit_integration_warn_line,
)
from ralph.pipeline.auto_integrate_catchup import start_catchup_worker_if_enabled
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcomeDisposition,
    read_record,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_recovery import legacy_rebase_startup_block
from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state
from ralph.pipeline.cycle_timing import (
    initialize_legacy_cycle_on_resume,
    initialize_legacy_development_timebox_on_resume,
)
from ralph.pipeline.integration_decision import CLEANUP_LADDER_ORDER
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    ladder_rung_for_record_fact_outcome,
)
from ralph.pipeline.integration_resolution import (
    EXHAUSTED,
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, normalize_verbosity, verbosity_rank
from ralph.pipeline.phase_transition import (
    build_phase_entry_model_from_state,
    emit_final_summary,
)
from ralph.pipeline.rebase_state import (
    HISTORY_AWARE_CONFLICT_STRATEGY_INDEX,
    RebaseState,
    StartupRetentionReason,
)
from ralph.pipeline.retry_diagnostic import RetryDiagnosticHooks
from ralph.pipeline.run_time_report import emit_run_time_report_safely
from ralph.process.manager import get_process_manager
from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityMonitor, ConnectivityState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEvent as _FailureEvent
from ralph.recovery.events import FalloverEvent as _FalloverEvent
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
    WAITING_STATUS_INTERVAL_SECONDS,
)
from ralph.workspace.probe import DefaultWorkspaceChangeProbe, WorkspaceChangeProbe

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from ralph.agents.clock import Clock
    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.pipeline._config_failure_fingerprint import ConfigFileFingerprintWatcher
    from ralph.pipeline._integration_executor import RebaseContinuationExecutor
    from ralph.pipeline.auto_integrate_catchup import AutoIntegrateCatchupWorker
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.integration_cleanup import PlanningBaseline
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.integration_resolution_types import (
        IntegrationResolutionVerdict,
    )
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PipelinePolicy, PolicyBundle
    from ralph.pro_support.heartbeat import ProHeartbeatClient
    from ralph.pro_support.hooks import ProPipelineHooks
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.pro_support.watcher import ProMarkerWatcher
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.workspace.scope import WorkspaceScope

    class _PipelineSubscriberProtocol(Protocol):
        def notify(self, state: PipelineState) -> None: ...

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _RunPipelineStepFn(Protocol):
        def __call__(
            self,
            *,
            state: PipelineState,
            policy_bundle: PolicyBundle,
            workspace_scope: WorkspaceScope,
            config: UnifiedConfig,
            display: ParallelDisplay,
            display_context: DisplayContext,
            verbosity: Verbosity,
            registry: _RegistryLike,
            pipeline_subscriber: _PipelineSubscriberProtocol | None,
            recovery_controller: RecoveryController,
            config_path: Path | None,
            cli_overrides: dict[str, object],
            _monitor_stop_cb: Callable[[], None] | None,
            pipeline_deps: PipelineDeps | None = None,
            _cycle_sample_box: list[float | None] | None = None,
            _development_sample_box: list[float | None] | None = None,
        ) -> PipelineState | int: ...

    class _ConnectivityMonitorLike(Protocol):
        @property
        def current_state(self) -> ConnectivityState: ...

        def add_listener(self, cb: Callable[[object], None]) -> Callable[[], None]: ...

    class _DisplayContextOwner(Protocol):
        _ctx: DisplayContext

    class _PhaseAwareDisplay(Protocol):
        def begin_phase(self, phase: str) -> None: ...

    class _RunEndDisplay(Protocol):
        def emit_run_end(
            self,
            *,
            phase: str,
            total_agent_calls: int,
            pr_url: str | None = None,
            exit_trigger: str | None = None,
            outer_dev_iteration: int | None = None,
        ) -> None: ...


@dataclass
class LoopContext:
    policy_bundle: PolicyBundle
    workspace_scope: WorkspaceScope
    config: UnifiedConfig
    active_display: ParallelDisplay
    display_context: DisplayContext
    effective_verbosity: Verbosity
    registry: _RegistryLike
    effective_pipeline_subscriber: _PipelineSubscriberProtocol | None
    controller: RecoveryController
    config_path: Path | None
    cli_overrides: dict[str, object]
    monitor_stop: Callable[[], None] | None | None
    connectivity_monitor: _ConnectivityMonitorLike
    sleep: Callable[[float], None]
    is_quiet: bool
    heartbeat_client: ProHeartbeatClient | None = None
    pro_watcher: ProMarkerWatcher | None = None
    latest_state: list[PipelineState] = field(default_factory=list)
    catchup_worker: AutoIntegrateCatchupWorker | None = None
    snapshot_registry: SnapshotRegistry | None = None
    pipeline_deps: PipelineDeps | None = None
    last_waiting_state_phase: str | None = None
    last_waiting_state_sig: tuple[str, str] | None = None
    config_failure_watcher: ConfigFileFingerprintWatcher | None = None
    process_teardown: Callable[[], None] | None = None
    _session_handoff_dispatched: bool = False
    cold_capped_escalation_attempted: bool = False
    hooks: RetryDiagnosticHooks = field(default_factory=RetryDiagnosticHooks)


_LoopContext = LoopContext


def _retry_hooks(ctx: LoopContext) -> RetryDiagnosticHooks:
    hooks = _context_hooks(ctx)
    if isinstance(hooks, RetryDiagnosticHooks):
        return hooks
    return RetryDiagnosticHooks()


def _context_hooks(ctx: LoopContext) -> object:
    try:
        return ctx.hooks
    except AttributeError:
        return None


def _recovery_preamble_for(
    ctx: LoopContext,
    backend: FileBackend,
) -> Callable[[WorkspaceScope, UnifiedConfig | None], RebaseState | None]:
    hooks = _retry_hooks(ctx)
    if hooks.recovery_preamble is not None:
        return hooks.recovery_preamble

    def _default(scope: WorkspaceScope, config: UnifiedConfig | None) -> RebaseState | None:
        try:
            ctx_cfg = ctx.config
        except AttributeError:
            ctx_cfg = None
        effective_cfg = config if config is not None else ctx_cfg
        try:
            latest_state = ctx.latest_state
            state = latest_state[0] if latest_state else None
        except AttributeError:
            state = None
        history = (
            state.rebase.conflict_strategies_tried
            if (
                state is not None
                and state.rebase is not None
                and hasattr(state.rebase, "conflict_strategies_tried")
            )
            else ()
        )
        conflict_resolver = None
        rebase_stop_resolver = None
        try:
            policy_bundle = ctx.policy_bundle
            registry = ctx.registry
            active_display = ctx.active_display
            pipeline_deps = ctx.pipeline_deps
            display_context = ctx.display_context
        except AttributeError:
            policy_bundle = None
            registry = None
            active_display = None
            pipeline_deps = None
            display_context = None

        if (
            policy_bundle is not None
            and registry is not None
            and active_display is not None
            and effective_cfg is not None
        ):
            try:
                conflict_resolver = build_agent_conflict_resolver(
                    policy_bundle=policy_bundle,
                    registry=registry,
                    display=active_display,
                    config=effective_cfg,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=scope,
                    display_context=display_context,
                    strategy_history=history,
                )
                rebase_stop_resolver = build_agent_rebase_stop_resolver(
                    policy_bundle=policy_bundle,
                    registry=registry,
                    display=active_display,
                    config=effective_cfg,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=scope,
                    display_context=display_context,
                    strategy_history=history,
                )
            except Exception as resolver_exc:
                logger.debug("Recovery preamble resolver construction skipped: {}", resolver_exc)

        try:
            controller = ctx.controller
        except AttributeError:
            controller = None

        try:
            return _run_auto_integrate_recovery_preamble(
                scope,
                effective_cfg,
                backend=backend,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=rebase_stop_resolver,
                recovery_controller=controller,
            )
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                return _run_auto_integrate_recovery_preamble(
                    scope,
                    effective_cfg,
                    backend=backend,
                )
            raise

    return _default


def _sync_live_display_context(display: _DisplayContextOwner, ctx: DisplayContext) -> None:
    existing = display._ctx
    object.__setattr__(existing, "width", ctx.width)


def _signal_if_now_online(monitor: _ConnectivityMonitorLike, wake: threading.Event) -> None:
    if monitor.current_state != ConnectivityState.OFFLINE:
        wake.set()


_OFFLINE_REPOLL_INTERVAL_SECONDS: Final[float] = 5.0

_OFFLINE_HEARTBEAT_INTERVAL_SECONDS: Final[float] = 60.0


def _wait_for_connectivity(monitor: _ConnectivityMonitorLike, wake: threading.Event) -> None:
    waited = 0.0
    next_heartbeat = _OFFLINE_HEARTBEAT_INTERVAL_SECONDS
    while not wake.wait(timeout=_OFFLINE_REPOLL_INTERVAL_SECONDS):
        if monitor.current_state != ConnectivityState.OFFLINE:
            return
        waited += _OFFLINE_REPOLL_INTERVAL_SECONDS
        if waited >= next_heartbeat:
            next_heartbeat += _OFFLINE_HEARTBEAT_INTERVAL_SECONDS
            logger.bind(recovery=True).warning(
                "Pipeline still paused: network offline for {}s, still waiting", int(waited)
            )


def _apply_connectivity_check(
    state: PipelineState, monitor: _ConnectivityMonitorLike | None
) -> PipelineState:
    if monitor is None:
        return state
    if monitor.current_state == ConnectivityState.ONLINE:
        return state.copy_with(last_connectivity_state=str(ConnectivityState.ONLINE))
    if monitor.current_state != ConnectivityState.OFFLINE:
        return state

    logger.bind(recovery=True).warning(
        "Pipeline paused: network offline, waiting for connectivity to restore..."
    )
    offline_state = state.copy_with(last_connectivity_state=str(ConnectivityState.OFFLINE))
    wake = threading.Event()

    def _on_transition(evt: object) -> None:
        if isinstance(evt, ConnectivityEvent) and evt.state == ConnectivityState.ONLINE:
            wake.set()

    unsub = monitor.add_listener(_on_transition)
    try:
        _signal_if_now_online(monitor, wake)
        _wait_for_connectivity(monitor, wake)
    finally:
        unsub()

    logger.bind(recovery=True).info("Connectivity restored, resuming pipeline")
    return offline_state.copy_with(last_connectivity_state=str(ConnectivityState.ONLINE))


def _setup_connectivity_monitor(
    connectivity_monitor: _ConnectivityMonitorLike | None,
) -> tuple[_ConnectivityMonitorLike, Callable[[], None] | None]:
    if connectivity_monitor is not None:
        return connectivity_monitor, None

    real_monitor = ConnectivityMonitor()
    mon_loop = asyncio.new_event_loop()

    def _run_mon_thread() -> None:
        asyncio.set_event_loop(mon_loop)
        try:
            mon_loop.run_until_complete(real_monitor.start())
            mon_loop.run_forever()
        finally:
            pending_tasks = cast("set[asyncio.Task[object]]", asyncio.all_tasks(mon_loop))
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                mon_loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
            mon_loop.close()

    mon_thread = threading.Thread(target=_run_mon_thread, daemon=True, name="connectivity-probe")
    mon_thread.start()

    def _stop_mon() -> None:
        if not mon_thread.is_alive():
            return
        stop_coro = real_monitor.stop()
        try:
            future = asyncio.run_coroutine_threadsafe(stop_coro, mon_loop)
        except (RuntimeError, OSError):
            stop_coro.close()
            mon_thread.join(timeout=3.0)
            return
        try:
            future.result(timeout=2.0)
        finally:
            with suppress(RuntimeError, OSError):
                mon_loop.call_soon_threadsafe(mon_loop.stop)
            mon_thread.join(timeout=3.0)

    return real_monitor, _stop_mon


def compose_display_stop(
    refresher_stop: Callable[[], None],
    display: ParallelDisplay,
) -> Callable[[], None]:

    def _stop_with_run_end_flush() -> None:
        try:
            refresher_stop()
        finally:
            with contextlib.suppress(Exception):
                display.flush_run_end_writers()

    return _stop_with_run_end_flush


def _setup_active_display(
    display: ParallelDisplay | None,
    is_quiet: bool,
    display_context: DisplayContext | None,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
) -> tuple[ParallelDisplay, DisplayContext, Callable[[], None]]:
    if display is not None:
        resolved_ctx = display._ctx
    elif display_context is not None:
        resolved_ctx = display_context
    else:
        resolved_ctx = _runner_module.make_display_context()

    if display is not None:
        active: ParallelDisplay = display
    else:
        active = build_default_display_legacy_bridge(
            workspace_scope.root,
            resolved_ctx,
            policy_bundle.pipeline,
            is_quiet=is_quiet,
        )

    def _stop() -> None:
        pass

    ctx_holder: list[DisplayContext] | None = None
    if hasattr(active, "_ctx"):
        ctx_holder = [active._ctx]
        _stop = _runner_module.install_width_refresher(
            ctx_holder,
            on_refresh=lambda ctx: _sync_live_display_context(
                cast("_DisplayContextOwner", active),
                ctx,
            ),
        )

    _stop = compose_display_stop(_stop, active)

    live_ctx: DisplayContext = active._ctx if ctx_holder is not None else resolved_ctx
    return active, live_ctx, _stop


def _emit_run_start(
    ctx: LoopContext,
    state: PipelineState,
) -> None:
    if not hasattr(ctx.active_display, "emit_run_start"):
        return
    with suppress(Exception):
        _prompt_path_raw: object = getattr(ctx.effective_pipeline_subscriber, "_prompt_path", None)
        _prompt_path: str | None = cast("str | None", _prompt_path_raw)
        _dev_para = next(
            (
                p.parallelization
                for p in ctx.policy_bundle.pipeline.phases.values()
                if p.parallelization is not None
            ),
            None,
        )
        _parallel_max_workers: int | None = (
            _dev_para.max_parallel_workers if _dev_para is not None else None
        )
        _plan_present = plan_artifact_path(ctx.workspace_scope.root).exists()
        _dev_agent_raw: object = getattr(ctx.config, "developer_agent", None)
        _dev_model_raw: object = getattr(ctx.config, "developer_model", None)
        verbosity_str = (
            str(ctx.effective_verbosity.value)
            if hasattr(ctx.effective_verbosity, "value")
            else str(ctx.effective_verbosity)
        )
        target_worktree_path = _target_worktree_path(ctx)
        _orientation = RunStartOrientation(
            prompt_path=_prompt_path,
            developer_agent=cast("str | None", _dev_agent_raw),
            developer_model=cast("str | None", _dev_model_raw),
            developer_iters=ctx.config.general.developer_iters,
            parallel_max_workers=_parallel_max_workers,
            plan_present=_plan_present,
            verbosity=verbosity_str,
            workspace_root=str(ctx.workspace_scope.root),
            target_worktree_path=target_worktree_path,
        )
        ctx.active_display.emit_run_start(_orientation)


def _target_worktree_path(ctx: LoopContext) -> str | None:
    target: object = getattr(ctx.config.general, "auto_integrate_target", None)
    if not isinstance(target, str) or not target:
        return None
    _verdict, owner = target_worktree_lookup(ctx.workspace_scope.root, target)
    return str(owner) if owner is not None else None


def log_waiting_state(
    state: PipelineState,
    ctx: LoopContext,
    phase_str: str,
    unavail_reason: str,
    delay_ms: int,
) -> None:
    sig = (phase_str, unavail_reason)
    if ctx.last_waiting_state_sig == sig:
        return
    ctx.last_waiting_state_sig = sig
    ctx.last_waiting_state_phase = phase_str
    chain = state.chain_for_phase(state.phase)
    agent_cooldowns: list[tuple[str, int, int]] = []
    if chain is not None:
        agent_cooldowns = ctx.controller.waiting_state_payload(phase_str, chain.agents)

    logger.bind(recovery=True).info(
        "Phase '{phase}' enters WAITING state: all agents unavailable. "
        "Last unavailability reason: {reason}. Cooldowns: {cooldowns}. "
        "Resuming in {wait_ms} ms.",
        phase=phase_str,
        reason=unavail_reason,
        cooldowns=agent_cooldowns,
        wait_ms=delay_ms,
    )


def _log_waiting_state(
    state: PipelineState,
    ctx: LoopContext,
    phase_str: str,
    unavail_reason: str,
    delay_ms: int,
) -> None:
    log_waiting_state(state, ctx, phase_str, unavail_reason, delay_ms)


def _log_resumed_state(
    state: PipelineState,
    ctx: LoopContext,
    phase_str: str,
    unavail_reason: str,
    delay_ms: int,
) -> None:
    ctx.last_waiting_state_sig = None
    ctx.last_waiting_state_phase = None
    chain = state.chain_for_phase(state.phase)
    agents_now_available: list[str] = []
    if chain is not None:
        agents_now_available = ctx.controller.agents_now_available(phase_str, chain.agents)
    logger.bind(recovery=True).info(
        "Phase '{phase}' RESUMED: cooldown expired. "
        "Agents now available: {agents}. "
        "Expired reason: {reason}. "
        "Waited for {waited_seconds:.3f} seconds.",
        phase=phase_str,
        agents=agents_now_available,
        reason=unavail_reason,
        waited_seconds=delay_ms / 1000.0,
    )


def build_status_bar_model(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_root: Path,
    *,
    elapsed_seconds: float | None = None,
    run_started_monotonic: float | None = None,
    attention: str | None = None,
) -> StatusBarModel:
    entry = build_phase_entry_model_from_state(state.phase, state, policy_bundle.pipeline)
    phase_style = phase_style_for_phase(state.phase, policy_bundle.pipeline)
    entry_agent_name: object = getattr(entry, "agent_name", None)
    return StatusBarModel(
        workspace_root=str(workspace_root),
        phase_label=entry.human_label(),
        phase_style=phase_style,
        outer_dev_iteration=entry.outer_dev_iteration,
        outer_dev_cap=entry.outer_dev_cap,
        inner_analysis=entry.inner_analysis,
        inner_analysis_cap=entry.inner_analysis_cap,
        integration_alert=_integration_alert_for_state(state, workspace_root),
        elapsed_seconds=elapsed_seconds,
        agent_name=entry_agent_name if isinstance(entry_agent_name, str) else None,
        run_started_monotonic=run_started_monotonic,
        attention=attention,
        operation_display_state=state.rebase.operation_display_state,
    )


def _preserved_copy_clause_from_reason(reason: str | None) -> str:
    marker = "preserved copy: "
    if reason and marker in reason:
        tail = reason.split(marker, 1)[1]
        return tail.split(";", 1)[0].strip() or "no preserved copy recorded"
    return "no preserved copy recorded"


def _preserved_copy_clause_for_root(root: Path) -> str:
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.integration_ladder import (
        PRESERVED_COPY_LEDGER_KEY,
        QUARANTINE_COPY_LEDGER_KEY,
    )

    try:
        evidence = AttemptLedger(root).read().ladder_evidence
    except Exception:
        return "no preserved copy recorded"
    identity = evidence.get(PRESERVED_COPY_LEDGER_KEY) or evidence.get(QUARANTINE_COPY_LEDGER_KEY)
    return identity or "no preserved copy recorded"


def _config_failure_fix_text(reason: str, root: Path | None) -> str:
    from ralph.pipeline._config_failure_fingerprint import extract_config_path_from_reason
    from ralph.pipeline.config_file_fingerprint_watcher import resolve_config_path_for_workspace

    path = extract_config_path_from_reason(reason)
    if path is None:
        path = resolve_config_path_for_workspace(root if root is not None else Path.cwd())
    return f"fix {path} then run ralph --check-config"


def integration_alert_for_state(state: PipelineState, root: Path | None = None) -> str | None:
    rebase = state.rebase
    if (
        rebase.is_intervention_required
        or rebase.last_decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED.value
        or rebase.operation_display_state == "intervention_required"
    ):
        return (
            "autonomous fallback ladder escalation — preserved copy: "
            f"{_preserved_copy_clause_from_reason(rebase.last_reason)}; "
            "next autonomous rung: preserve the work, then climb the cleanup ladder"
        )
    if rebase.is_diagnostic_action() or rebase.operation_display_state == "evidence_diagnosis":
        return "evidence diagnosis — gathering operation facts"
    if not rebase.is_conflict_resolution() and rebase.last_outcome != RebaseOutcomeKind.CONFLICT:
        return None
    reason = rebase.last_reason or "unresolved"
    if rebase.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE:
        fix_text = reason if "fix " in reason else _config_failure_fix_text(reason, root)
        return (
            f"integration conflict ({rebase.last_failure_cause}: {fix_text}) — resolution required"
        )
    if rebase.last_failure_cause != ResolutionFailureCause.NONE:
        return f"integration conflict ({rebase.last_failure_cause}: {reason}) — resolution required"
    return f"integration conflict ({reason}) — resolution required"


_build_status_bar_model = build_status_bar_model


def _integration_alert_for_state(state: PipelineState, root: Path | None = None) -> str | None:
    return integration_alert_for_state(state, root)


def _attention_state_for_state(state: PipelineState) -> str | None:
    run_outcome: object = getattr(state, "run_outcome", None)
    outcome_attention = {
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "completed": "completed",
        "terminated": "terminated",
    }
    if isinstance(run_outcome, str) and run_outcome in outcome_attention:
        return outcome_attention[run_outcome]
    run_phase: object = getattr(state, "phase", None)
    if isinstance(run_phase, str):
        if run_phase.startswith("waiting"):
            return "waiting"
        if run_phase in ("complete", "failed", "failed_terminal", "cancelled"):
            return "terminated"
        if run_phase in ("", "starting", "startup"):
            return "starting"
    retrying: object = getattr(state, "retrying", None)
    return "retrying" if isinstance(retrying, bool) and retrying else None


def _push_status_bar_if_changed(
    active_display: object,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_root: Path,
    last_sig: tuple[str, int | None, int | None, str | None, str | None, str | None, str | None]
    | None,
) -> tuple[str, int | None, int | None, str | None, str | None, str | None, str | None] | None:
    with suppress(Exception):
        elapsed_seconds = (
            active_display.run_elapsed_seconds
            if isinstance(active_display, ParallelDisplay)
            else None
        )
        run_started_monotonic = (
            active_display.run_started_monotonic
            if isinstance(active_display, ParallelDisplay)
            else None
        )
        attention = _attention_state_for_state(state)
        model = _build_status_bar_model(
            state,
            policy_bundle,
            workspace_root,
            elapsed_seconds=elapsed_seconds,
            run_started_monotonic=run_started_monotonic,
            attention=attention,
        )
        signature = (
            state.phase,
            model.outer_dev_iteration,
            model.inner_analysis,
            model.integration_alert,
            model.outer_label,
            model.agent_name,
            model.attention,
        )
        if signature != last_sig and hasattr(active_display, "update_status_bar"):
            active_display.update_status_bar(model)
            return signature
    return last_sig


def _sweep_orphaned_git_locks(workspace_scope: WorkspaceScope) -> None:
    try:
        from ralph.git._lock_recovery import reap_stale_locks
        from ralph.pipeline._auto_integrate_recovery_git_state import (
            common_git_dir,
            rebase_bookkeeping_dir,
        )

        root = workspace_scope.root
        git_dir = rebase_bookkeeping_dir(root)
        if git_dir is None:
            return
        for lock in reap_stale_locks(git_dir, common_git_dir(root), operation="startup-sweep"):
            logger.info("startup sweep removed orphaned git lock: {}", lock)
    except Exception as sweep_exc:
        logger.warning("startup git-lock sweep failed: {}", sweep_exc)


def run_auto_integrate_recovery_preamble(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig | None = None,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    force_retry: bool = False,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    recovery_controller: RecoveryController | None = None,
) -> RebaseState | None:
    _sweep_orphaned_git_locks(workspace_scope)
    try:
        from ralph.pipeline.auto_integrate import recover_incomplete_integration

        if config is not None and conflict_resolver is None and rebase_stop_resolver is None:
            from ralph.pipeline.auto_integrate_recovery import build_recovery_context

            cr, rs, ctrl = build_recovery_context(workspace_scope, config)
            if cr is not None:
                conflict_resolver = cr
            if rs is not None:
                rebase_stop_resolver = rs
            if recovery_controller is None and ctrl is not None:
                recovery_controller = ctrl

        recovered = recover_incomplete_integration(
            workspace_scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            backend=backend,
            recovery_controller=recovery_controller,
            force_retry=force_retry,
        )
    except Exception as recover_exc:
        logger.warning("auto_integrate recovery preamble failed: {}", recover_exc)
        return None
    if recovered is not None:
        logger.debug("auto_integrate recovery preamble: {}", recovered.last_outcome)
    return recovered


_run_auto_integrate_recovery_preamble = run_auto_integrate_recovery_preamble


def _run_startup_integration(
    ctx: LoopContext, state: RebaseState | None = None
) -> RebaseState | None:
    try:
        resolver = build_agent_conflict_resolver(
            policy_bundle=ctx.policy_bundle,
            registry=ctx.registry,
            display=ctx.active_display,
            config=ctx.config,
            pipeline_deps=ctx.pipeline_deps,
            workspace_scope=ctx.workspace_scope,
            display_context=ctx.display_context,
            strategy_history=(
                state.conflict_strategies_tried
                if state is not None
                and state.conflict_strategy_index == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
                else ()
            ),
        )
        prior_rebase = state if state is not None and type(state) is RebaseState else RebaseState()
        from ralph.pipeline.auto_integrate_planning import integrate_before_planning

        outcome = integrate_before_planning(
            ctx.config,
            ctx.workspace_scope,
            prior_rebase,
            conflict_resolver=resolver,
            rebase_stop_resolver=build_agent_rebase_stop_resolver(
                policy_bundle=ctx.policy_bundle,
                registry=ctx.registry,
                display=ctx.active_display,
                config=ctx.config,
                pipeline_deps=ctx.pipeline_deps,
                workspace_scope=ctx.workspace_scope,
                display_context=ctx.display_context,
                strategy_history=prior_rebase.conflict_strategies_tried,
            ),
            display=ctx.active_display,
        )
    except Exception as startup_exc:
        logger.warning("startup auto-integrate failed: {}", startup_exc)
        return None
    with suppress(Exception):
        if outcome is not None:
            _runner_module._log_auto_integrate_outcome(ctx.active_display, outcome)
        else:
            ctx.active_display.emit(
                "run",
                "[cyan]auto-integrate:[/cyan] startup check: nothing to"
                " integrate (branch in sync with target, worktree busy,"
                " or not a git checkout)",
            )
    return outcome


def run_startup_integration(
    ctx: LoopContext, state: RebaseState | None = None
) -> RebaseState | None:
    if _run_startup_integration is not _run_startup_integration_dispatch:
        return _run_startup_integration(ctx, state)
    return _run_startup_integration_impl(ctx, state)


def _run_startup_integration_dispatch(
    ctx: LoopContext, state: RebaseState | None = None
) -> RebaseState | None:
    if run_startup_integration is not _public_run_startup_integration_dispatch:
        return run_startup_integration(ctx, state)
    return _run_startup_integration_impl(ctx, state)


_run_startup_integration_impl = _run_startup_integration
_run_startup_integration = _run_startup_integration_dispatch
_public_run_startup_integration_dispatch = run_startup_integration


def _save_recovered_rebase_checkpoint(
    state: PipelineState,
    ctx: LoopContext,
) -> None:
    hooks = _retry_hooks(ctx)
    if hooks.save_checkpoint is not None:
        hooks.save_checkpoint(state, ctx)
        return
    try:
        from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state

        reconciled = (
            reconcile_stale_unresolved_state(state.rebase)
            if state.rebase.integration_unresolved
            else state.rebase
        )
        checkpoint_state = (
            state if reconciled is state.rebase else state.copy_with(rebase=reconciled)
        )
        _runner_module.save_checkpoint_or_log(
            checkpoint_state,
            message=("Checkpoint save failed while persisting auto-integrate recovery: {err}"),
            path=_runner_module._checkpoint_path(ctx.workspace_scope),
        )
    except Exception as save_exc:
        logger.warning("auto_integrate recovery checkpoint save failed: {}", save_exc)


def save_recovered_rebase_checkpoint(
    state: PipelineState,
    ctx: LoopContext,
) -> None:
    if _save_recovered_rebase_checkpoint is not _save_recovered_rebase_checkpoint_dispatch:
        _save_recovered_rebase_checkpoint(state, ctx)
        return
    _save_recovered_rebase_checkpoint_impl(state, ctx)


def _save_recovered_rebase_checkpoint_dispatch(
    state: PipelineState,
    ctx: LoopContext,
) -> None:
    if save_recovered_rebase_checkpoint is not _public_save_recovered_rebase_checkpoint_dispatch:
        save_recovered_rebase_checkpoint(state, ctx)
        return
    _save_recovered_rebase_checkpoint_impl(state, ctx)


_save_recovered_rebase_checkpoint_impl = _save_recovered_rebase_checkpoint
_save_recovered_rebase_checkpoint = _save_recovered_rebase_checkpoint_dispatch
_public_save_recovered_rebase_checkpoint_dispatch = save_recovered_rebase_checkpoint


@dataclass(frozen=True)
class _RunCollaborators:
    policy_bundle: PolicyBundle
    registry: _RegistryLike
    state: PipelineState
    effective_verbosity: Verbosity
    is_quiet: bool
    sleep: Callable[[float], None]
    connectivity_monitor: _ConnectivityMonitorLike
    monitor_stop: Callable[[], None] | None
    controller: RecoveryController
    marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None
    snapshot_registry: SnapshotRegistry | None


def _resolve_policy_bundle(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    policy_bundle_factory: Callable[[WorkspaceScope, UnifiedConfig], PolicyBundle] | None,
) -> PolicyBundle:
    if pipeline_deps is not None:
        if pipeline_deps.policy_bundle is not None:
            return pipeline_deps.policy_bundle
        if pipeline_deps.policy_bundle_factory is not None:
            return pipeline_deps.policy_bundle_factory(workspace_scope, config)
    if pro_hooks is not None and pro_hooks.policy_bundle_override is not None:
        return pro_hooks.policy_bundle_override
    if pro_hooks is not None and pro_hooks.policy_bundle_factory is not None:
        return pro_hooks.policy_bundle_factory(workspace_scope, config)
    if policy_bundle_factory is not None:
        return policy_bundle_factory(workspace_scope, config)
    return _runner_module.load_policy_bundle_for_run(workspace_scope, config)


def _resolve_registry(
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    registry_factory: Callable[[UnifiedConfig], _RegistryLike] | None,
) -> _RegistryLike:
    if pipeline_deps is not None and pipeline_deps.registry_factory is not None:
        return cast("_RegistryLike", pipeline_deps.registry_factory(config))
    if pro_hooks is not None and pro_hooks.registry_factory is not None:
        return cast("_RegistryLike", pro_hooks.registry_factory(config))
    if registry_factory is not None:
        return registry_factory(config)
    return _runner_module.AgentRegistry.from_config(config)


def _with_counter_overrides(
    state: PipelineState,
    counter_overrides: dict[str, int] | None,
) -> PipelineState:
    if not counter_overrides:
        return state
    updated = {**state.budget_caps, **counter_overrides}
    if updated == state.budget_caps:
        return state
    logger.info(
        "resume: applying counter override(s) {} over the checkpoint budget caps",
        counter_overrides,
    )
    return state.copy_with(budget_caps=updated)


def _resume_epoch(pipeline_deps: PipelineDeps | None) -> float:
    return pipeline_deps.wall_time() if pipeline_deps is not None else time.time()


def _initialize_resumed_timeboxes(
    state: PipelineState, policy: PipelinePolicy, current_epoch: float
) -> PipelineState:
    cycle_was_active = state.cycle_timebox_active
    state = initialize_legacy_cycle_on_resume(state, policy)
    if not cycle_was_active and state.cycle_timebox_active:
        state = state.copy_with(cycle_timebox_started_at_epoch=current_epoch)
    development_was_active = state.dev_timebox_active
    state = initialize_legacy_development_timebox_on_resume(state, policy)
    if not development_was_active and state.dev_timebox_active:
        state = state.copy_with(dev_timebox_started_at_epoch=current_epoch)
    return state


_BASELINE_REF_NAMESPACE: Final = "refs/ralph/baselines"


def _capture_rollback_baseline(state: PipelineState, root: Path | None) -> PipelineState:
    if root is None or state.rollback_baseline_ref:
        return state
    run_id = uuid.uuid4().hex
    try:
        seal = seal_baseline(root, run_id, force=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return state
    except Exception as exc:
        logger.error("Failed to seal the run-start baseline: {}", exc)
        return state.copy_with(rollback_baseline_ref=f"{_BASELINE_REF_NAMESPACE}/{run_id}")
    if seal is None:
        return state
    logger.info("Recorded the run-start rollback baseline: {}", seal.ref)
    return state.copy_with(rollback_baseline_ref=seal.ref)


def _create_initial_state(
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    state_factory: Callable[
        [UnifiedConfig, AgentsPolicy, PipelinePolicy, dict[str, int] | None],
        PipelineState,
    ]
    | None,
    counter_overrides: dict[str, int] | None,
) -> PipelineState:
    if pipeline_deps is not None and pipeline_deps.state_factory is not None:
        return pipeline_deps.state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    if pro_hooks is not None and pro_hooks.state_factory is not None:
        return pro_hooks.state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    if state_factory is not None:
        return state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    return _runner_module.create_initial_state(
        config,
        agents_policy=policy_bundle.agents,
        pipeline_policy=policy_bundle.pipeline,
        counter_overrides=counter_overrides,
    )


def _resolve_initial_state(
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    initial_state: PipelineState | None,
    counter_overrides: dict[str, int] | None,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    state_factory: Callable[
        [UnifiedConfig, AgentsPolicy, PipelinePolicy, dict[str, int] | None],
        PipelineState,
    ]
    | None,
    *,
    workspace_root: Path | None = None,
) -> PipelineState:
    if initial_state is not None:
        resumed = initial_state
        if (
            isinstance(resumed.rollback_pending, bool)
            and resumed.rollback_pending is True
            and isinstance(resumed.rollback_ref, str)
            and resumed.rollback_ref
        ):
            root = workspace_root if workspace_root is not None else Path()
            resumed = _runner_module.execute_pending_rollback(
                resumed,
                root,
                policy_bundle.pipeline,
            )

        with suppress(AttributeError, TypeError):
            current_epoch = _resume_epoch(pipeline_deps)
            resumed = _initialize_resumed_timeboxes(resumed, policy_bundle.pipeline, current_epoch)
        return _with_counter_overrides(resumed, counter_overrides)
    fresh_state = _create_initial_state(
        config,
        policy_bundle,
        pipeline_deps,
        pro_hooks,
        state_factory,
        counter_overrides,
    )
    return _capture_rollback_baseline(fresh_state, workspace_root)


def _resolve_recovery_sleep(
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    recovery_sleep_kwarg: Callable[[float], None] | None,
) -> Callable[[float], None]:
    if pipeline_deps is not None and pipeline_deps.recovery_sleep is not None:
        return pipeline_deps.recovery_sleep
    if pro_hooks is not None and pro_hooks.recovery_sleep is not None:
        return pro_hooks.recovery_sleep
    if recovery_sleep_kwarg is not None:
        return recovery_sleep_kwarg
    return time.sleep


def _resolve_recovery_controller(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    recovery_controller_factory: Callable[
        [PipelineState, PolicyBundle, UnifiedConfig],
        tuple[RecoveryController, int],
    ]
    | None,
) -> RecoveryController:
    if pipeline_deps is not None and pipeline_deps.recovery_controller_factory is not None:
        controller, _ = pipeline_deps.recovery_controller_factory(state, policy_bundle, config)
        return controller
    if pro_hooks is not None and pro_hooks.recovery_controller_factory is not None:
        controller, _ = pro_hooks.recovery_controller_factory(state, policy_bundle, config)
        return controller
    if recovery_controller_factory is not None:
        controller, _ = recovery_controller_factory(state, policy_bundle, config)
        return controller
    clock = pipeline_deps.recovery_clock if pipeline_deps is not None else None
    builder = _build_recovery_controller
    if builder is not _PRODUCTION_BUILD_RECOVERY_CONTROLLER:
        if clock is not None:
            controller, _ = builder(state, policy_bundle, config, clock=clock)
        else:
            controller, _ = builder(state, policy_bundle, config)
        return controller
    if clock is not None:
        controller, _ = builder(
            state, policy_bundle, config, clock=clock, pipeline_deps=pipeline_deps
        )
    else:
        controller, _ = builder(state, policy_bundle, config, pipeline_deps=pipeline_deps)
    return controller


def _resolve_marker_watcher_factory(
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None,
) -> Callable[[Path], ProMarkerWatcher] | None:
    if pipeline_deps is not None:
        return pipeline_deps.marker_watcher_factory
    if pro_hooks is not None and pro_hooks.marker_watcher_factory is not None:
        return pro_hooks.marker_watcher_factory
    return marker_watcher_factory


def _resolve_snapshot_registry(
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    snapshot_registry: SnapshotRegistry | None,
) -> SnapshotRegistry | None:
    if pipeline_deps is not None:
        return pipeline_deps.snapshot_registry
    if pro_hooks is not None and pro_hooks.snapshot_registry is not None:
        return pro_hooks.snapshot_registry
    return snapshot_registry


def _resolve_run_collaborators(
    *,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    initial_state: PipelineState | None,
    counter_overrides: dict[str, int] | None,
    pipeline_deps: PipelineDeps | None,
    pro_hooks: ProPipelineHooks | None,
    policy_bundle_factory: Callable[[WorkspaceScope, UnifiedConfig], PolicyBundle] | None,
    registry_factory: Callable[[UnifiedConfig], _RegistryLike] | None,
    state_factory: Callable[
        [UnifiedConfig, AgentsPolicy, PipelinePolicy, dict[str, int] | None],
        PipelineState,
    ]
    | None,
    recovery_controller_factory: Callable[
        [PipelineState, PolicyBundle, UnifiedConfig],
        tuple[RecoveryController, int],
    ]
    | None,
    marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None,
    snapshot_registry: SnapshotRegistry | None,
    _recovery_sleep: Callable[[float], None] | None,
    verbosity: Verbosity | None = None,
    connectivity_monitor: _ConnectivityMonitorLike | None = None,
) -> _RunCollaborators:
    policy_bundle = _resolve_policy_bundle(
        workspace_scope, config, pipeline_deps, pro_hooks, policy_bundle_factory
    )
    registry = _resolve_registry(config, pipeline_deps, pro_hooks, registry_factory)
    state = _resolve_initial_state(
        config,
        policy_bundle,
        initial_state,
        counter_overrides,
        pipeline_deps,
        pro_hooks,
        state_factory,
        workspace_root=workspace_scope.root,
    )
    effective_verbosity = _normalize_run_verbosity(config, verbosity=verbosity)
    is_quiet = verbosity_rank(effective_verbosity) <= VERBOSITY_RANK[Verbosity.QUIET]
    sleep = _resolve_recovery_sleep(pipeline_deps, pro_hooks, _recovery_sleep)
    injected_monitor = (
        pipeline_deps.connectivity_monitor
        if pipeline_deps is not None and pipeline_deps.connectivity_monitor is not None
        else connectivity_monitor
    )
    resolved_monitor, monitor_stop = _setup_connectivity_monitor(injected_monitor)
    controller = _resolve_recovery_controller(
        state,
        policy_bundle,
        config,
        pipeline_deps,
        pro_hooks,
        recovery_controller_factory,
    )
    return _RunCollaborators(
        policy_bundle=policy_bundle,
        registry=registry,
        state=state,
        effective_verbosity=effective_verbosity,
        is_quiet=is_quiet,
        sleep=sleep,
        connectivity_monitor=resolved_monitor,
        monitor_stop=monitor_stop,
        controller=controller,
        marker_watcher_factory=_resolve_marker_watcher_factory(
            pipeline_deps, pro_hooks, marker_watcher_factory
        ),
        snapshot_registry=_resolve_snapshot_registry(pipeline_deps, pro_hooks, snapshot_registry),
    )


def _normalize_run_verbosity(
    config: UnifiedConfig,
    *,
    verbosity: Verbosity | None,
) -> Verbosity:
    if verbosity is not None:
        return normalize_verbosity(verbosity)
    return normalize_verbosity(config.general.verbosity)


def _apply_legacy_heartbeat_override(
    workspace_root: Path,
    default_client: ProHeartbeatClient | None,
) -> ProHeartbeatClient | None:
    _module_legacy_obj: object
    _self_dict: dict[str, object] = sys.modules[__name__].__dict__
    try:
        _module_legacy_obj = _self_dict["_start_pro_heartbeat_if_active"]
    except KeyError:
        return default_client
    if _module_legacy_obj is _start_pro_marker_watcher or not callable(_module_legacy_obj):
        return default_client
    _module_legacy = cast(
        "Callable[[Path], ProHeartbeatClient | None]",
        _module_legacy_obj,
    )
    patched_heartbeat: ProHeartbeatClient | None = _module_legacy(workspace_root)
    return patched_heartbeat if patched_heartbeat is not None else default_client


def _resolve_recovery_display_interval(config: UnifiedConfig) -> float:
    raw: object = getattr(
        config.general,
        "agent_waiting_status_interval_seconds",
        WAITING_STATUS_INTERVAL_SECONDS,
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and float(raw) > 0.0:
        return float(raw)
    return WAITING_STATUS_INTERVAL_SECONDS


def _announce_deferred_startup_integration(ctx: LoopContext, recovered: RebaseState) -> None:
    with suppress(Exception):
        emit_integration_warn_line(
            ctx.active_display,
            "startup catch-up deferred: crash recovery still owns the"
            f" durable integration record ({recovered.last_reason})",
        )


def _apply_startup_rebase_outcomes(
    state: PipelineState,
    ctx: LoopContext,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState:
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.state import PipelineState as RuntimePipelineState

    try:
        candidate_deps = ctx.pipeline_deps
    except AttributeError:
        candidate_deps = None
    pipeline_deps = candidate_deps if isinstance(candidate_deps, PipelineDeps) else None
    if candidate_deps is not None and not isinstance(cast("object", state), RuntimePipelineState):
        return state
    if pipeline_deps is not None and pipeline_deps.startup_rebase_resolver is not None:
        injected_rebase = pipeline_deps.startup_rebase_resolver(ctx.config, ctx.workspace_scope)
        if injected_rebase is None or injected_rebase == state.rebase:
            return state
        return state.copy_with(rebase=injected_rebase)

    recovered_rebase = _recovery_preamble_for(ctx, backend)(ctx.workspace_scope, ctx.config)
    if recovered_rebase is not None:
        state = state.copy_with(rebase=recovered_rebase)
        verdict = inspect_integration_resolution(ctx.workspace_scope.root, state.rebase)
        if verdict.dispatch_allowed and state.rebase.integration_unresolved:
            state = state.copy_with(rebase=reconcile_stale_unresolved_state(state.rebase))
        _save_recovered_rebase_checkpoint(state, ctx)
        if recovery_retained_record(state.rebase):
            _announce_deferred_startup_integration(ctx, recovered_rebase)
            return state
    startup_rebase = _run_startup_integration(ctx, state.rebase)
    if startup_rebase is not None and startup_rebase != state.rebase:
        state = state.copy_with(rebase=startup_rebase)
        if not startup_rebase.is_skipped():
            _save_recovered_rebase_checkpoint(state, ctx)
    return state


def _reselect_preferred_agent(
    state: PipelineState,
    ctx: LoopContext,
    *,
    allow_wait: bool = True,
) -> PipelineState:
    chain = state.chain_for_phase(state.phase)
    if chain is not None and hasattr(ctx.controller, "preferred_agent_index"):
        selection = ctx.controller.preferred_agent_index(str(state.phase), chain.agents)
        if selection.index is not None and selection.index != chain.current_index:
            chain = AgentChainState(
                agents=chain.agents,
                current_index=selection.index,
                retries=0,
            )
            state = state.with_phase_chain(state.phase, chain).copy_with(
                last_agent_session_id=None,
                agent_retry_intent=cleared_agent_retry_intent(),
            )
        elif (
            allow_wait
            and selection.index is None
            and not ctx.controller.agents_now_available(str(state.phase), chain.agents)
        ):
            state = state.copy_with(
                last_error="all agents unavailable; waiting for cooldown expiry",
                last_retry_delay_ms=ctx.controller.earliest_available_wait_ms(
                    str(state.phase), chain.agents
                ),
            ).set_waiting(WaitReason.ALL_AGENTS_UNAVAILABLE, None)
    return state


def _resume_after_cooldown_wait(
    state: PipelineState,
    ctx: LoopContext,
    phase: str,
    unavailability_reason: str,
    delay_ms: int,
) -> PipelineState:
    emit_activity_line(
        ctx.active_display,
        None,
        status_text("RESUMED", "cooldown expired; retrying", "green"),
    )
    verdict = inspect_integration_resolution(ctx.workspace_scope.root, state.rebase)
    if verdict.dispatch_allowed:
        state = _reselect_preferred_agent(state, ctx, allow_wait=False)
    state = state.clear_waiting()
    _log_resumed_state(state, ctx, phase, unavailability_reason, delay_ms)
    return state


def _announce_conflict_resolution_entry(
    ctx: LoopContext,
    verdict: IntegrationResolutionVerdict,
) -> None:
    detail = "; ".join(reason for reason in verdict.reasons if isinstance(reason, str))
    with suppress(Exception):
        emit_integration_warn_line(
            ctx.active_display,
            "unresolved integration detected; entering conflict resolution"
            f" ({detail or verdict.status})",
        )


def announce_conflict_resolution_entry(
    ctx: LoopContext,
    verdict: IntegrationResolutionVerdict,
) -> None:
    if _announce_conflict_resolution_entry is not _announce_conflict_resolution_entry_dispatch:
        _announce_conflict_resolution_entry(ctx, verdict)
        return
    _announce_conflict_resolution_entry_impl(ctx, verdict)


def _announce_conflict_resolution_entry_dispatch(
    ctx: LoopContext,
    verdict: IntegrationResolutionVerdict,
) -> None:
    if (
        announce_conflict_resolution_entry
        is not _public_announce_conflict_resolution_entry_dispatch
    ):
        announce_conflict_resolution_entry(ctx, verdict)
        return
    _announce_conflict_resolution_entry_impl(ctx, verdict)


_announce_conflict_resolution_entry_impl = _announce_conflict_resolution_entry
_announce_conflict_resolution_entry = _announce_conflict_resolution_entry_dispatch
_public_announce_conflict_resolution_entry_dispatch = announce_conflict_resolution_entry


def _integration_target_or_none(ctx: LoopContext) -> str | None:
    from ralph.pipeline.auto_integrate import resolve_integration_target

    try:
        return resolve_integration_target(ctx.config, ctx.workspace_scope.root)
    except Exception as target_exc:
        logger.warning("integration resolution: unable to resolve target: {}", target_exc)
        return None


def _run_integration_conflict_resolution(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    from ralph.pipeline.auto_integrate_transaction import integration_transaction

    hooks = _retry_hooks(ctx)
    if hooks.conflict_resolution is not None:
        return hooks.conflict_resolution(ctx, rebase)
    with integration_transaction(ctx.workspace_scope.root) as outcome:
        if not outcome.acquired:
            emit_integration_warn_line(
                ctx.active_display,
                outcome.describe("integration recovery retained; another owner is active"),
            )
            return False
        from ralph.pipeline.auto_integrate_record import read_record

        retained = read_record(ctx.workspace_scope.root)
        if retained is not None and classify_git_operation(retained) in (
            GitOperationKind.AWAITING_CONTINUE,
            GitOperationKind.AWAITING_MERGE_COMMIT,
        ):
            return False
        return _run_owned_integration_conflict_resolution(ctx, rebase)


def run_integration_conflict_resolution(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    if _run_integration_conflict_resolution is not _run_integration_conflict_resolution_dispatch:
        return _run_integration_conflict_resolution(ctx, rebase)
    return _run_integration_conflict_resolution_impl(ctx, rebase)


def _run_integration_conflict_resolution_dispatch(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    if (
        run_integration_conflict_resolution
        is not _public_run_integration_conflict_resolution_dispatch
    ):
        return run_integration_conflict_resolution(ctx, rebase)
    return _run_integration_conflict_resolution_impl(ctx, rebase)


_run_integration_conflict_resolution_impl = _run_integration_conflict_resolution
_run_integration_conflict_resolution = _run_integration_conflict_resolution_dispatch
_public_run_integration_conflict_resolution_dispatch = run_integration_conflict_resolution


def _conflict_identity_or_default(ctx: LoopContext, target: str) -> ConflictIdentity:
    from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    try:
        return observe_conflict_identity(ctx.workspace_scope.root, target)
    except Exception as identity_exc:
        logger.debug("integration resolution: conflict identity unreadable: {}", identity_exc)
        return ConflictIdentity()


def _cold_capped_escalation_due(
    ctx: LoopContext,
    rebase: RebaseState,
    target: str,
    identity: ConflictIdentity,
    *,
    ledger: AgentFailureLedger | None = None,
    candidates: tuple[str, ...] = (),
) -> bool:
    from ralph.pipeline.auto_integrate_conflict_budget import resolver_allowed

    if not rebase.resolution_exhausted:
        return False
    if resolver_allowed(
        rebase,
        target,
        identity,
        ledger=ledger,
        candidates=candidates,
    ):
        return False
    session_attempted: bool = getattr(ctx, "cold_capped_escalation_attempted", False)
    if session_attempted:
        return False
    ctx.cold_capped_escalation_attempted = True
    logger.warning(
        "integration resolution: cold-capped escalation for target '{}'; "
        "re-deriving live Git state",
        target,
    )
    with suppress(Exception):
        emit_integration_warn_line(
            ctx.active_display,
            "recorded conflict exhaustion reached; attempting cold-capped "
            "escalation that re-derives live Git state",
        )
    return True


def _resolve_blocking_conflict(
    ctx: LoopContext,
    target: str,
    rebase: RebaseState | None,
) -> bool:
    paused_probe = _retry_hooks(ctx).paused_rebase_at or _paused_rebase_at
    if paused_probe(ctx.workspace_scope.root):
        return _resolve_paused_rebase_with_history(ctx, target, rebase)
    try:
        resolver = build_agent_conflict_resolver(
            policy_bundle=ctx.policy_bundle,
            registry=ctx.registry,
            display=ctx.active_display,
            config=ctx.config,
            pipeline_deps=ctx.pipeline_deps,
            workspace_scope=ctx.workspace_scope,
            display_context=ctx.display_context,
            strategy_history=(
                rebase.conflict_strategies_tried
                if rebase is not None
                and rebase.conflict_strategy_index == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
                else ()
            ),
        )
    except Exception as build_exc:
        logger.warning("integration resolution executor failed: {}", build_exc)
        return False
    complete_merge = _retry_hooks(ctx).complete_in_progress_merge or _complete_in_progress_merge
    if complete_merge(ctx.workspace_scope.root, target, resolver):
        return True
    return _resolve_orphaned_unmerged_index(ctx, target)


def _run_owned_integration_conflict_resolution(
    ctx: LoopContext,
    rebase: RebaseState | None = None,
) -> bool:
    from ralph.pipeline.auto_integrate_conflict_budget import resolver_allowed

    target = _integration_target_or_none(ctx)
    if not target:
        return False
    identity = _conflict_identity_or_default(ctx, target)
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

    installed = tuple(
        agent
        for agent in resolution_chain_agents(ctx.policy_bundle)
        if ctx.registry.get(agent) is not None
    )
    controller_candidate: object = getattr(ctx, "controller", None)
    ledger: AgentFailureLedger | None = (
        controller_candidate.ledger
        if isinstance(controller_candidate, RecoveryController)
        else None
    )
    if rebase is not None and _cold_capped_escalation_due(
        ctx,
        rebase,
        target,
        identity,
        ledger=ledger,
        candidates=installed,
    ):
        return _run_cold_capped_escalation(ctx, target, rebase)
    if rebase is not None and not resolver_allowed(
        rebase,
        target,
        identity,
        ledger=ledger,
        candidates=installed,
    ):
        logger.warning(
            "integration resolution: no eligible conflict resolver for target '{}'; escalating",
            target,
        )
        with suppress(Exception):
            emit_integration_warn_line(
                ctx.active_display,
                "no eligible conflict resolver for this conflict;"
                " escalating instead of re-invoking the resolver",
            )
        return False
    return _resolve_blocking_conflict(ctx, target, rebase)


def _resolve_orphaned_unmerged_index(ctx: LoopContext, target: str) -> bool:
    hooks = _retry_hooks(ctx)
    if hooks.resolve_orphaned_unmerged_index is not None:
        return bool(hooks.resolve_orphaned_unmerged_index(ctx, target))
    return _resolve_orphaned_unmerged_index_in_place(ctx, target)


def _resolve_orphaned_unmerged_index_in_place(ctx: LoopContext, target: str) -> bool:
    from ralph.git.merge import stage_paths, unmerged_paths
    from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline

    root = ctx.workspace_scope.root
    try:
        paths = tuple(
            path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>"
        )
        if not paths:
            return False
        logger.warning(
            "integration resolution: {} unmerged path(s) belong to no rebase or merge; "
            "resolving them in place",
            len(paths),
        )
        if ctx.pipeline_deps is None:
            logger.warning(
                "integration resolution: no pipeline deps threaded to this seam; "
                "cannot resolve the orphaned conflicted index"
            )
            return False
        if not run_conflict_resolution_pipeline(
            root=root,
            target=target,
            config=ctx.config,
            pipeline_deps=ctx.pipeline_deps,
            workspace_scope=ctx.workspace_scope,
            policy_bundle=ctx.policy_bundle,
            display=ctx.active_display,
            display_context=ctx.display_context,
        ):
            return False
        if not stage_paths(root, paths):
            logger.warning("integration resolution: could not stage the resolved paths")
            return False
        return not [path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>"]
    except Exception as exc:
        logger.warning("integration resolution: orphaned index resolution failed: {}", exc)
        return False


def _rederive_live_conflict_state_or_none(
    ctx: LoopContext,
    root: Path,
    target: str,
    rebase: RebaseState,
) -> RebaseState | None:
    try:
        derive = _retry_hooks(ctx).rederive_live_conflict_state or _rederive_live_conflict_state
        return derive(root, target, rebase)
    except Exception as derive_exc:
        logger.warning("integration resolution: live re-derivation failed: {}", derive_exc)
        return None


def _adopt_live_rebase(ctx: LoopContext, live_rebase: RebaseState) -> None:
    if not ctx.latest_state:
        return
    ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=live_rebase)]
    _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)


def _run_cold_capped_escalation(
    ctx: LoopContext,
    target: str,
    rebase: RebaseState,
) -> bool:
    live_rebase = _rederive_live_conflict_state_or_none(
        ctx, ctx.workspace_scope.root, target, rebase
    )
    if live_rebase is not None and not live_rebase.is_intervention_required:
        _adopt_live_rebase(ctx, live_rebase)
        return True
    try:
        return _resolve_blocking_conflict(ctx, target, rebase)
    except Exception as resolve_exc:
        logger.warning(
            "integration resolution: cold-capped escalation resolver failed: {}",
            resolve_exc,
        )
        return False


def _rederive_live_conflict_state(
    root: Path,
    target: str,
    rebase: RebaseState,
) -> RebaseState | None:
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    from ralph.pipeline.integration_resolution import inspect_integration_resolution

    try:
        verdict = inspect_integration_resolution(root, rebase)
    except Exception as exc:
        logger.debug(
            "integration resolution: live re-derivation verdict unreadable: {}",
            exc,
        )
        return None
    if not verdict.dispatch_allowed:
        return None
    return (
        rebase.clear_resolution_exhausted()
        .record_consecutive_conflicts(0, cap=0)
        .model_copy(
            update={
                "last_outcome": RebaseOutcomeKind.COMPLETED,
                "last_reason": ("live Git re-derivation cleared the cold-capped state"),
            }
        )
    )


def _paused_rebase_at(root: Path) -> bool:
    try:
        from ralph.git.rebase.rebase_continuation import rebase_in_progress_at

        return bool(rebase_in_progress_at(root))
    except Exception as exc:
        logger.warning(
            "integration resolution: rebase state unreadable ({}); "
            "falling back to git's on-disk rebase markers",
            exc,
        )
        return _rebase_markers_on_disk(root)


def _rebase_markers_on_disk(root: Path) -> bool:
    try:
        from ralph.git.rebase.rebase_continuation import REBASE_STATE_DIRS

        git_dir = Path(root) / ".git"
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            _, _, path = pointer.partition("gitdir:")
            git_dir = Path(path.strip() or str(git_dir))
        return any((git_dir / name).exists() for name in REBASE_STATE_DIRS)
    except Exception as exc:
        logger.warning("integration resolution: rebase markers unreadable: {}", exc)
        return False


def _resolve_paused_rebase_with_history(
    ctx: LoopContext,
    target: str,
    rebase: RebaseState | None,
    *,
    executor: RebaseContinuationExecutor | None = None,
) -> bool:
    if executor is None:
        if rebase is None:
            return _resolve_paused_rebase(ctx, target)
        return _resolve_paused_rebase(ctx, target, rebase)
    if rebase is None:
        return _resolve_paused_rebase(ctx, target, executor=executor)
    return _resolve_paused_rebase(ctx, target, rebase, executor=executor)


def resolve_paused_rebase(
    ctx: LoopContext,
    target: str,
    rebase: RebaseState | None = None,
    *,
    executor: RebaseContinuationExecutor | None = None,
) -> bool:
    from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver

    try:
        stop_resolver = build_agent_rebase_stop_resolver(
            policy_bundle=ctx.policy_bundle,
            registry=ctx.registry,
            display=ctx.active_display,
            config=ctx.config,
            pipeline_deps=ctx.pipeline_deps,
            workspace_scope=ctx.workspace_scope,
            display_context=ctx.display_context,
            strategy_history=(
                rebase.conflict_strategies_tried
                if rebase is not None
                and rebase.conflict_strategy_index == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
                else ()
            ),
        )
        active_executor = (
            executor if executor is not None else default_rebase_continuation_executor()
        )
        res = active_executor.continue_rebase(
            ctx.workspace_scope.root,
            target,
            stop_resolver,
            ctx.config.conflict_resolution,
        )
        resolved, reason, failure_cause = res
    except Exception as resolve_exc:
        logger.warning("integration resolution: paused rebase resolution failed: {}", resolve_exc)
        return False
    if not resolved:
        logger.warning(
            "integration resolution: the paused rebase for '{}' was not resolved: {} (cause: {})",
            target,
            reason or "no terminal evidence",
            failure_cause.value,
        )
    return bool(resolved)


def _resolve_paused_rebase(
    ctx: LoopContext,
    target: str,
    rebase: RebaseState | None = None,
    *,
    executor: RebaseContinuationExecutor | None = None,
) -> bool:
    return resolve_paused_rebase(ctx, target, rebase, executor=executor)


def _complete_in_progress_merge(root: Path, target: str, resolver: ConflictResolver) -> bool:
    from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
    from ralph.pipeline.auto_integrate_resolve import _resolve_and_commit

    try:
        if merge_state(root) != MERGE_STATE_IN_PROGRESS:
            return False
        return _resolve_and_commit(root, target, resolver)
    except Exception as resolve_exc:
        logger.warning("integration resolution executor failed: {}", resolve_exc)
        return False


def _announce_blocked_dispatch(
    ctx: LoopContext,
    verdict: IntegrationResolutionVerdict,
) -> None:
    detail = "; ".join(reason for reason in verdict.reasons if isinstance(reason, str))
    executor = verdict.recovery_executor or "no recovery executor"
    with suppress(Exception):
        emit_integration_warn_line(
            ctx.active_display,
            f"dispatch blocked ({verdict.status}): {detail or 'no reason recorded'}"
            f" -- {executor} could not clear it",
        )


def _is_planning_entry(state: PipelineState, ctx: LoopContext) -> bool:
    pipeline = ctx.policy_bundle.pipeline
    phase = str(state.phase)
    if pipeline.entry_phase == phase:
        return True
    definition = pipeline.phases.get(phase)
    return definition is not None and definition.drain == "planning"


def _configured_sync_target(ctx: LoopContext) -> tuple[Path, str] | None:
    config: object = getattr(ctx, "config", None)
    general: object = getattr(config, "general", None)
    if general is None:
        return None
    enabled: object = getattr(general, "auto_integrate_enabled", True)
    target: object = getattr(general, "auto_integrate_target", None)
    if not enabled or not isinstance(target, str) or not target:
        return None
    root = Path(ctx.workspace_scope.root)
    if not (root / ".git").exists():
        return None
    return root, target


def _capture_planning_baseline_if_entry(
    state: PipelineState, ctx: LoopContext
) -> PlanningBaseline | None:
    if not _is_planning_entry(state, ctx):
        return None
    from ralph.pipeline.auto_integrate_planning import capture_pre_planning_baseline

    with suppress(Exception):
        return capture_pre_planning_baseline(Path(ctx.workspace_scope.root))
    return None


def _resolve_planning_residue_after_dispatch(
    ctx: LoopContext,
    baseline: PlanningBaseline,
) -> None:
    from ralph.pipeline.auto_integrate_planning import resolve_planning_residue

    configured = _configured_sync_target(ctx)
    target = configured[1] if configured is not None else None
    outcome = resolve_planning_residue(Path(ctx.workspace_scope.root), baseline, target=target)
    if outcome is not None and (outcome.operation_blocked or outcome.operation_unknown):
        label = (
            "unknown-operation"
            if outcome.operation_kind is None
            else outcome.operation_kind.name.lower()
        )
        with suppress(Exception):
            emit_integration_warn_line(
                ctx.active_display,
                f"planning residue cleanup deferred: git {label} in progress; "
                "the operation ladder (continue/abort/rebuild) owns this state",
            )


def _planning_sync_gap(ctx: LoopContext) -> str | None:
    from ralph.git.branch_relation import BranchRelation, branch_relation
    from ralph.git.merge import observe_branch_sha

    configured = _configured_sync_target(ctx)
    if configured is None:
        return None
    root, target = configured
    sha, query_ok = observe_branch_sha(root, target)
    if query_ok and sha is None:
        return (
            f"configured integration target '{target}' does not exist; set"
            " general.auto_integrate_target to the branch this work integrates into"
            " (or disable general.auto_integrate_enabled)"
        )
    relation = branch_relation(root, target)
    if relation is BranchRelation.SYNCED:
        return None
    if relation is BranchRelation.FEATURE_AHEAD:
        return f"feature commits not landed on '{target}' (fast-forward not proven)"
    if relation is BranchRelation.UNKNOWN:
        return f"cannot prove the branch contains '{target}' (ancestry query failed)"
    return f"branch does not contain the tip of '{target}'"


def _reconcile_dispatch_state(state: PipelineState) -> PipelineState:
    if not (
        state.rebase.integration_unresolved
        or state.rebase.resolution_exhausted
        or state.rebase.recovery_record_retained
    ):
        return state
    reconciled = reconcile_stale_unresolved_state(state.rebase)
    return state if reconciled is state.rebase else state.copy_with(rebase=reconciled)


def _route_planning_commit(state: PipelineState, ctx: LoopContext) -> PipelineState:
    from ralph.git.operations import has_uncommitted_changes
    from ralph.pipeline._planning_obstruction import (
        PlanningObstructionKind,
        identify_planning_obstruction,
    )
    from ralph.pipeline.progress import advance_phase

    kind_str = state.rebase.last_planning_obstruction
    if kind_str:
        try:
            obstruction_kind = PlanningObstructionKind(kind_str)
        except ValueError:
            obstruction_kind = PlanningObstructionKind.NONE
    else:
        obstruction_kind = identify_planning_obstruction(state.rebase.last_reason or "")
    if (
        obstruction_kind is PlanningObstructionKind.NONE
        or not inspect_integration_resolution(
            ctx.workspace_scope.root, state.rebase
        ).dispatch_allowed
    ):
        return state
    pipeline = ctx.policy_bundle.pipeline
    cleanup = next(
        (
            name
            for name, definition in pipeline.phases.items()
            if definition.role == "commit_review"
            and (commit := pipeline.phases.get(definition.transitions.on_success)) is not None
            and commit.role == "commit"
        ),
        None,
    )
    if cleanup is None or not has_uncommitted_changes(ctx.workspace_scope.root):
        return state
    routed = advance_phase(state, cleanup, policy=pipeline).copy_with(
        integration_commit_resume_phase=state.phase,
        rebase=reconcile_stale_unresolved_state(state.rebase),
    )
    with suppress(Exception):
        emit_integration_warn_line(
            ctx.active_display,
            "planning synchronization requires committing pending work; "
            f"running normal {cleanup} cleanup and commit workflow",
        )
    _save_recovered_rebase_checkpoint(routed, ctx)
    return routed


def _resume_planning_after_commit(
    state: PipelineState | int,
    ctx: LoopContext,
    previous: PipelineState,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState | int:
    from ralph.git.operations import has_uncommitted_changes
    from ralph.pipeline.progress import advance_phase

    resume = previous.integration_commit_resume_phase
    if not isinstance(resume, str):
        return state
    if isinstance(state, int):
        return _wait_for_integration_retry(previous, ctx, backend=backend)
    definition = ctx.policy_bundle.pipeline.phases.get(str(state.phase))
    if definition is not None and definition.role in {"commit_review", "commit"}:
        return state
    state = advance_phase(state, resume, policy=ctx.policy_bundle.pipeline).copy_with(
        integration_commit_resume_phase=None,
    )
    _save_recovered_rebase_checkpoint(state, ctx)
    if has_uncommitted_changes(ctx.workspace_scope.root):
        state = _wait_for_integration_retry(state, ctx, backend=backend)
    return state


def _execute_owner_ladder_rung(
    ctx: LoopContext,
    root: Path,
    sync_verdict: PlanningSyncVerdict,
    target: str | None,
) -> None:
    from ralph.git.merge import observe_branch_sha
    from ralph.pipeline._integration_owner_seam import execute_planning_ladder_rung
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger

    expected_sha: str | None = None
    if target is not None:
        with suppress(Exception):
            sha, query_ok = observe_branch_sha(root, target)
            expected_sha = sha if query_ok else None
    try:
        result = execute_planning_ladder_rung(
            root,
            sync_verdict,
            ledger=AttemptLedger(root),
            target=target,
            expected_target_sha=expected_sha,
        )
    except Exception as rung_exc:
        logger.warning("cleanup-ladder rung dispatch failed: {}", rung_exc)
        return
    if result is None:
        return
    message = f"cleanup-ladder rung {result.rung.name.lower()} {result.outcome}: {result.reason}"
    if result.outcome in ("failed", "terminal"):
        logger.warning("{}", message)
    else:
        logger.info("{}", message)
    with suppress(Exception):
        emit_integration_warn_line(ctx.active_display, message)


def execute_owner_ladder_rung(
    ctx: LoopContext,
    root: Path,
    sync_verdict: PlanningSyncVerdict,
    target: str | None,
) -> None:
    if _execute_owner_ladder_rung is not _execute_owner_ladder_rung_dispatch:
        _execute_owner_ladder_rung(ctx, root, sync_verdict, target)
        return
    _execute_owner_ladder_rung_impl(ctx, root, sync_verdict, target)


def _execute_owner_ladder_rung_dispatch(
    ctx: LoopContext,
    root: Path,
    sync_verdict: PlanningSyncVerdict,
    target: str | None,
) -> None:
    if execute_owner_ladder_rung is not _public_execute_owner_ladder_rung_dispatch:
        execute_owner_ladder_rung(ctx, root, sync_verdict, target)
        return
    _execute_owner_ladder_rung_impl(ctx, root, sync_verdict, target)


_execute_owner_ladder_rung_impl = _execute_owner_ladder_rung
_execute_owner_ladder_rung = _execute_owner_ladder_rung_dispatch
_public_execute_owner_ladder_rung_dispatch = execute_owner_ladder_rung


def _dispatch_owner_ladder_rung(
    ctx: LoopContext,
    root: Path,
    target: str | None,
    sync_verdict: PlanningSyncVerdict | None,
    executed_rung: CleanupLadderRung | None,
    previous_action: IntegrationAction | None,
) -> tuple[
    CleanupLadderRung | None, IntegrationAction | None, Literal["continue", "break", "fall"]
]:
    if sync_verdict is None or sync_verdict.ladder_rung is None:
        return executed_rung, previous_action, "fall"
    if sync_verdict.ladder_rung is executed_rung:
        return executed_rung, previous_action, "break"
    _execute_owner_ladder_rung(ctx, root, sync_verdict, target)
    return sync_verdict.ladder_rung, sync_verdict.action, "continue"


def dispatch_owner_ladder_rung(
    ctx: LoopContext,
    root: Path,
    target: str | None,
    sync_verdict: PlanningSyncVerdict | None,
    executed_rung: CleanupLadderRung | None,
    previous_action: IntegrationAction | None,
) -> tuple[
    CleanupLadderRung | None, IntegrationAction | None, Literal["continue", "break", "fall"]
]:
    if _dispatch_owner_ladder_rung is not _dispatch_owner_ladder_rung_dispatch:
        return _dispatch_owner_ladder_rung(
            ctx, root, target, sync_verdict, executed_rung, previous_action
        )
    return _dispatch_owner_ladder_rung_impl(
        ctx, root, target, sync_verdict, executed_rung, previous_action
    )


def _dispatch_owner_ladder_rung_dispatch(
    ctx: LoopContext,
    root: Path,
    target: str | None,
    sync_verdict: PlanningSyncVerdict | None,
    executed_rung: CleanupLadderRung | None,
    previous_action: IntegrationAction | None,
) -> tuple[
    CleanupLadderRung | None, IntegrationAction | None, Literal["continue", "break", "fall"]
]:
    if dispatch_owner_ladder_rung is not _public_dispatch_owner_ladder_rung_dispatch:
        return dispatch_owner_ladder_rung(
            ctx, root, target, sync_verdict, executed_rung, previous_action
        )
    return _dispatch_owner_ladder_rung_impl(
        ctx, root, target, sync_verdict, executed_rung, previous_action
    )


_dispatch_owner_ladder_rung_impl = _dispatch_owner_ladder_rung
_dispatch_owner_ladder_rung = _dispatch_owner_ladder_rung_dispatch
_public_dispatch_owner_ladder_rung_dispatch = dispatch_owner_ladder_rung


def _configured_sync_remote(ctx: LoopContext) -> str | None:
    config_obj: object = getattr(ctx, "config", None)
    general_obj: object = getattr(config_obj, "general", None)
    remote_obj: object = getattr(general_obj, "auto_integrate_remote", None)
    return remote_obj if isinstance(remote_obj, str) and remote_obj.strip() else None


def _ask_owner_planning_sync(
    root: Path,
    *,
    record: IntegrationRecord | None,
    target: str | None,
    remote: str | None,
) -> PlanningSyncVerdict | None:
    try:
        return decide_planning_sync(
            root,
            record=record,
            target=target,
            remote=remote,
        )
    except Exception as seam_exc:
        logger.debug("owner seam unreadable: {}", seam_exc)
        return None


def _ensure_planning_in_sync(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, tuple[PipelineState, str, int] | None]:
    if not _is_planning_entry(state, ctx):
        return state, None
    gap = _planning_sync_gap(ctx)
    if gap is None:
        return state, None
    root = Path(ctx.workspace_scope.root)
    configured = _configured_sync_target(ctx)
    target = configured[1] if configured is not None else None
    remote = _configured_sync_remote(ctx)
    record = read_record(root)
    attempts = 3
    previous_failure: tuple[str, str | None, str | None] | None = None
    previous_action: IntegrationAction | None = None
    executed_rung: CleanupLadderRung | None = None
    for attempt in range(1, attempts + 1):
        held_line = (
            f"planning held until the branch is in sync ({gap});"
            f" integrating now (attempt {attempt}/{attempts})"
        )

        def _emit_held_line(message: str) -> None:
            emit_integration_warn_line(ctx.active_display, message)

        with suppress(Exception):
            integration_outcome_log().record(_emit_held_line, held_line)
        integrated = _run_startup_integration(ctx, state.rebase)
        if integrated is not None:
            state = state.copy_with(rebase=integrated)
            _save_recovered_rebase_checkpoint(state, ctx)
        gap = _planning_sync_gap(ctx)
        if gap is None:
            return state, None
        routed = _route_planning_commit(state, ctx)
        if routed is not state:
            return routed, None
        sync_verdict = _ask_owner_planning_sync(root, record=record, target=target, remote=remote)
        executed_rung, previous_action, rung_step = _dispatch_owner_ladder_rung(
            ctx, root, target, sync_verdict, executed_rung, previous_action
        )
        if rung_step == "break":
            break
        if rung_step == "continue":
            continue
        failure = (gap, state.rebase.last_outcome, state.rebase.last_reason)
        if failure == previous_failure:
            break
        previous_failure = failure
        if sync_verdict is not None:
            if previous_action is not None and sync_verdict.action is not previous_action:
                break
            previous_action = sync_verdict.action
    outcome = (
        state.rebase.last_reason
        or (state.rebase.last_outcome.value if state.rebase.last_outcome is not None else None)
        or "no integration outcome"
    )
    detail = f"{gap}; {attempt} integration attempt(s) with the conflict resolver did not land it"
    detail += f" (last: {outcome})"
    alarm = f"CRITICAL: integration did not land; planning blocked: {detail}"

    def _emit_planning_blocked(message: str) -> None:
        critical("{}", message)
        with suppress(Exception):
            emit_integration_warn_line(ctx.active_display, message)

    integration_outcome_log().record(_emit_planning_blocked, alarm)
    state = state.copy_with(last_error=alarm)
    _save_recovered_rebase_checkpoint(state, ctx)
    return state, (state, prev_phase, 0)


def ensure_planning_in_sync(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, tuple[PipelineState, str, int] | None]:
    if _ensure_planning_in_sync is not _ensure_planning_in_sync_dispatch:
        return _ensure_planning_in_sync(state, ctx, prev_phase)
    return _ensure_planning_in_sync_impl(state, ctx, prev_phase)


def _ensure_planning_in_sync_dispatch(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, tuple[PipelineState, str, int] | None]:
    if ensure_planning_in_sync is not _public_ensure_planning_in_sync_dispatch:
        return ensure_planning_in_sync(state, ctx, prev_phase)
    return _ensure_planning_in_sync_impl(state, ctx, prev_phase)


_ensure_planning_in_sync_impl = _ensure_planning_in_sync
_ensure_planning_in_sync = _ensure_planning_in_sync_dispatch
_public_ensure_planning_in_sync_dispatch = ensure_planning_in_sync


def _exhaustion_still_binds(ctx: LoopContext, rebase: RebaseState | None) -> bool:
    from ralph.pipeline.auto_integrate import resolve_integration_target
    from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity, resolver_allowed

    if rebase is None:
        return True
    try:
        target = resolve_integration_target(ctx.config, ctx.workspace_scope.root)
    except Exception as exc:
        logger.debug("integration resolution: unable to resolve target: {}", exc)
        return True
    if not target:
        return True
    try:
        identity = observe_conflict_identity(ctx.workspace_scope.root, target)
    except Exception as exc:
        logger.debug("integration resolution: conflict identity unreadable: {}", exc)
        identity = ConflictIdentity()
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

    installed = tuple(
        agent
        for agent in resolution_chain_agents(ctx.policy_bundle)
        if ctx.registry.get(agent) is not None
    )
    controller_candidate: object = getattr(ctx, "controller", None)
    ledger: AgentFailureLedger | None = (
        controller_candidate.ledger
        if isinstance(controller_candidate, RecoveryController)
        else None
    )
    if resolver_allowed(
        rebase,
        target,
        identity,
        ledger=ledger,
        candidates=installed,
    ):
        logger.info(
            "integration resolution: the recorded exhaustion does not bind the conflict "
            "now on disk for '{}'; giving the resolver another attempt",
            target,
        )
        return False
    return True


def _repair_pending_merge_commit(ctx: LoopContext, failure: str) -> None:
    from ralph.pipeline._pending_merge_repair import repair_pending_merge
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

    if ctx.pipeline_deps is None:
        emit_integration_warn_line(
            ctx.active_display, "merge commit retained; recovery agent dependencies unavailable"
        )
        return
    try:
        repair_pending_merge(
            workspace_scope=ctx.workspace_scope,
            config=ctx.config,
            pipeline_deps=ctx.pipeline_deps,
            policy_bundle=ctx.policy_bundle,
            display=ctx.active_display,
            display_context=ctx.display_context,
            agents=tuple(
                agent
                for agent in resolution_chain_agents(ctx.policy_bundle)
                if ctx.registry.get(agent) is not None
            ),
            failure=failure,
            controller=ctx.controller,
        )
    except Exception as exc:
        critical("Merge commit retained; repair agent unavailable: {}", exc)


def _diagnose_retained_integration(ctx: LoopContext, failure: str) -> None:
    from ralph.pipeline._integration_diagnostic import diagnose_retained_integration

    diagnose_retained_integration(ctx, failure)


def _conflict_resolution_warranted(ctx: LoopContext) -> bool:
    from ralph.pipeline._integration_resolution_gate import conflict_resolution_warranted

    try:
        return conflict_resolution_warranted(ctx.workspace_scope.root)
    except Exception:
        return True


def _resolve_retained_conflict(ctx: LoopContext) -> bool:
    if not _conflict_resolution_warranted(ctx):
        return False
    return _run_integration_conflict_resolution(
        ctx,
        ctx.latest_state[0].rebase if ctx.latest_state else None,
    )


def _handoff_retained_failure(
    ctx: LoopContext,
    failure: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    hooks = _retry_hooks(ctx)
    if hooks.handoff_retained_failure is not None:
        hooks.handoff_retained_failure(ctx, failure)
        return
    from ralph.pipeline._integration_diagnostic import diagnostic_needed
    from ralph.pipeline._stop_resolution_entry import retained_round_is_new
    from ralph.pipeline.auto_integrate_record import (
        RecordReadOutcomeDisposition,
        read_record_outcome,
    )

    if not retained_round_is_new(ctx.workspace_scope.root, _retained_round_fingerprint(ctx)):
        return
    outcome = read_record_outcome(ctx.workspace_scope.root, backend=backend)
    if outcome.disposition is RecordReadOutcomeDisposition.PRESENT and outcome.record is not None:
        latest = outcome.record
        if diagnostic_needed(ctx.workspace_scope.root, latest):
            _diagnose_retained_integration(ctx, failure)
        elif classify_git_operation(latest) in (
            GitOperationKind.AWAITING_CONTINUE,
            GitOperationKind.AWAITING_MERGE_COMMIT,
        ):
            _repair_pending_merge_commit(ctx, failure)
        else:
            config = cast(
                "UnifiedConfig | None",
                getattr(ctx, "config", None),
            )
            resolved = _resolve_retained_conflict(ctx)
            recovered = _recovery_preamble_for(ctx, backend)(ctx.workspace_scope, config)
            if (
                recovered is not None
                and not recovered.is_intervention_required
                and not recovered.integration_unresolved
                and ctx.latest_state
            ):
                ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=recovered)]
                _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)
            elif resolved and ctx.latest_state and ctx.latest_state[0].rebase is not None:
                rebase_resolved = ctx.latest_state[0].rebase.model_copy(
                    update={
                        "is_intervention_required": False,
                        "last_outcome": RebaseOutcomeKind.COMPLETED,
                        "last_reason": "conflict resolved by automated resolver",
                    }
                )
                ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=rebase_resolved)]
                _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)
    elif outcome.disposition in (
        RecordReadOutcomeDisposition.ABSENT,
        RecordReadOutcomeDisposition.UNREADABLE,
    ):
        config_absent = cast(
            "UnifiedConfig | None",
            getattr(ctx, "config", None),
        )
        resolved = _resolve_retained_conflict(ctx)
        recovered = _recovery_preamble_for(ctx, backend)(ctx.workspace_scope, config_absent)
        if (
            recovered is not None
            and not recovered.is_intervention_required
            and not recovered.integration_unresolved
        ):
            if ctx.latest_state:
                ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=recovered)]
                _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)
        elif resolved and ctx.latest_state and ctx.latest_state[0].rebase is not None:
            rebase_resolved = ctx.latest_state[0].rebase.model_copy(
                update={
                    "is_intervention_required": False,
                    "last_outcome": RebaseOutcomeKind.COMPLETED,
                    "last_reason": "conflict resolved by automated resolver",
                }
            )
            ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=rebase_resolved)]
            _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)
        elif outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            rung = ladder_rung_for_record_fact_outcome(RecordFactOutcome.STALE_INTENT)
            if recovered is not None and ctx.latest_state:
                ctx.latest_state[:] = [ctx.latest_state[0].copy_with(rebase=recovered)]
                _save_recovered_rebase_checkpoint(ctx.latest_state[0], ctx)
            message = (
                f"durable integration record unreadable "
                f"({outcome.unreadable_reason or 'unknown reason'}); "
                f"corrupt bytes preserved at the record path; escalating through "
                f"cleanup-ladder rung {rung.name.lower()}"
            )

            def _emit_unreadable_line(line: str) -> None:
                logger.warning("{}", line)
                with suppress(Exception):
                    emit_integration_warn_line(ctx.active_display, line)

            with suppress(Exception):
                integration_outcome_log().record(_emit_unreadable_line, message)


def _dispatch_retained_handoff(
    ctx: LoopContext,
    detail: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    if backend is DEFAULT_FILE_BACKEND:
        _handoff_retained_failure(ctx, detail)
        return
    try:
        _handoff_retained_failure(ctx, detail, backend=backend)
    except TypeError:
        _handoff_retained_failure(ctx, detail)


def dispatch_retained_handoff(
    ctx: LoopContext,
    detail: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    _dispatch_retained_handoff(ctx, detail, backend=backend)


def diagnostic_repair_attempt_cap() -> int:
    from ralph.pipeline._integration_diagnostic import DIAGNOSTIC_REPAIR_ATTEMPT_CAP

    return DIAGNOSTIC_REPAIR_ATTEMPT_CAP


def new_integration_outcome_log() -> IntegrationOutcomeLog:
    return IntegrationOutcomeLog()


def _handle_retained_integration(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, tuple[PipelineState, str, int] | None]:
    if retained_integration_reason(ctx.workspace_scope.root) is None:
        return state, None
    continued = _run_startup_integration(ctx, state.rebase)
    if continued is not None:
        state = state.copy_with(rebase=continued)
        _save_recovered_rebase_checkpoint(state, ctx)
    disk_after = retained_integration_reason(ctx.workspace_scope.root)
    if not disk_after:
        return state, None
    remaining = disk_after or state.rebase.last_reason or "durable integration record retained"
    if not state.rebase.recovery_record_retained:
        state = state.copy_with(
            rebase=state.rebase.set_recovery_record_retained(
                StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
            ).model_copy(update={"last_reason": remaining})
        )
        _save_recovered_rebase_checkpoint(state, ctx)
    state = state.copy_with(
        rebase=state.rebase.set_operation_display_state(OperationDisplayState.EVIDENCE_DIAGNOSIS)
    )
    _handoff_retained_failure(ctx, state.rebase.last_reason or remaining)
    return state, (state, prev_phase, 0)


def _block_unresolved_integration(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, str, int] | None:
    from ralph.pipeline.integration_resolution_types import (
        IntegrationResolutionVerdict,
    )

    record, busy_expired = _blocking_record(ctx)
    if busy_expired:
        state = state.copy_with(
            rebase=state.rebase.set_operation_display_state(OperationDisplayState.WAITING)
        )
    if record is not None:
        target = ctx.config.general.auto_integrate_target
        remote = ctx.config.general.auto_integrate_remote
        record_action = decide_seam_action(
            ctx.workspace_scope.root,
            record=record,
            target=target,
            remote=remote,
        )
        if isinstance(record_action, RecordFactOutcome):
            rung = ladder_rung_for_record_fact_outcome(record_action)
            message = (
                f"integration record facts failed closed ({record_action.value}); "
                f"preserving the work to backup refs and escalating through "
                f"cleanup-ladder rung {rung.name.lower()}"
            )

            def _emit_record_fact_line(line: str) -> None:
                logger.warning("{}", line)
                with suppress(Exception):
                    emit_integration_warn_line(ctx.active_display, line)

            with suppress(Exception):
                integration_outcome_log().record(_emit_record_fact_line, message)
            state = state.copy_with(
                rebase=state.rebase.set_operation_display_state(OperationDisplayState.WAITING)
            )

    state, retained_result = _handle_retained_integration(state, ctx, prev_phase)
    if retained_result is not None:
        return retained_result

    corrected: list[RebaseState] = []
    verdict = inspect_integration_resolution(
        ctx.workspace_scope.root,
        state.rebase,
        write_back=corrected.append,
    )
    if verdict.dispatch_allowed:
        reconciled = reconcile_stale_unresolved_state(state.rebase)
        if corrected:
            reconciled = corrected[0]
        if reconciled != state.rebase:
            state = state.copy_with(rebase=reconciled)
            _save_recovered_rebase_checkpoint(state, ctx)
        return None
    _save_recovered_rebase_checkpoint(state, ctx)

    exhausted_still_binds = verdict.status is EXHAUSTED and _exhaustion_still_binds(
        ctx, state.rebase
    )
    if exhausted_still_binds:
        _announce_blocked_dispatch(ctx, verdict)
        _announce_deferred_startup_integration(ctx, state.rebase)
        return state, prev_phase, 1

    post = _post_recovery_verdict(ctx, state, prev_phase, verdict)
    if not isinstance(post, IntegrationResolutionVerdict):
        return post

    _announce_blocked_dispatch(ctx, post)
    _announce_deferred_startup_integration(ctx, state.rebase)
    return state, prev_phase, 1


def _blocking_record(ctx: LoopContext) -> tuple[IntegrationRecord | None, bool]:
    outcome = read_record_outcome(ctx.workspace_scope.root)
    if outcome.disposition is RecordReadOutcomeDisposition.BUSY:
        expired = isinstance(busy_refusal_outcome(outcome.busy_deadline), RecordFactOutcome)
        if expired:
            rung = ladder_rung_for_record_fact_outcome(RecordFactOutcome.STALE_INTENT)
            message = (
                "durable integration record busy past its bounded retry deadline; "
                "preserving the work to backup refs and escalating through "
                f"cleanup-ladder rung {rung.name.lower()}"
            )

            def _emit_busy_expired_line(line: str) -> None:
                logger.warning("{}", line)
                with suppress(Exception):
                    emit_integration_warn_line(ctx.active_display, line)

            with suppress(Exception):
                integration_outcome_log().record(_emit_busy_expired_line, message)
        return None, expired
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        return None, False
    return outcome.record, False


def _boundary_ladder_recovery(
    ctx: LoopContext,
    state: PipelineState,
) -> tuple[PipelineState, IntegrationResolutionVerdict | None]:
    configured = _configured_sync_target(ctx)
    if configured is None:
        return state, None
    root, target = configured
    remote = _configured_sync_remote(ctx)
    try:
        record = read_record(root)
    except Exception:
        record = None
    executed_rung: CleanupLadderRung | None = None
    previous_action: IntegrationAction | None = None
    for _rung_slot in CLEANUP_LADDER_ORDER:
        sync_verdict = _ask_owner_planning_sync(root, record=record, target=target, remote=remote)
        executed_rung, previous_action, rung_step = _dispatch_owner_ladder_rung(
            ctx, root, target, sync_verdict, executed_rung, previous_action
        )
        if rung_step != "continue":
            break
        recovered = _run_startup_integration(ctx, state.rebase)
        if recovered is not None:
            state = state.copy_with(rebase=recovered)
            _save_recovered_rebase_checkpoint(state, ctx)
        post_dispatch = inspect_integration_resolution(ctx.workspace_scope.root, state.rebase)
        if post_dispatch.dispatch_allowed:
            return state, post_dispatch
    return state, None


def _post_recovery_verdict(
    ctx: LoopContext,
    state: PipelineState,
    prev_phase: str,
    verdict: IntegrationResolutionVerdict,
) -> IntegrationResolutionVerdict | tuple[PipelineState, str, int] | None:
    _announce_conflict_resolution_entry(ctx, verdict)
    state, ladder_verdict = _boundary_ladder_recovery(ctx, state)
    post_recovery: IntegrationResolutionVerdict
    if ladder_verdict is None:
        _run_integration_conflict_resolution(ctx, state.rebase)
        recovered = _run_startup_integration(ctx, state.rebase)
        if recovered is not None:
            state = state.copy_with(rebase=recovered)
            _save_recovered_rebase_checkpoint(state, ctx)
        post_recovery = inspect_integration_resolution(ctx.workspace_scope.root, state.rebase)
    else:
        post_recovery = ladder_verdict
    if post_recovery.dispatch_allowed:
        reconciled = reconcile_stale_unresolved_state(state.rebase)
        if reconciled != state.rebase:
            state = state.copy_with(rebase=reconciled)
            _save_recovered_rebase_checkpoint(state, ctx)
        ctx.latest_state[:] = [state]
        return None
    ctx.latest_state[:] = [state]
    return post_recovery


def post_recovery_verdict(
    ctx: LoopContext,
    state: PipelineState,
    prev_phase: str,
    verdict: IntegrationResolutionVerdict,
) -> IntegrationResolutionVerdict | tuple[PipelineState, str, int] | None:
    if _post_recovery_verdict is not _post_recovery_verdict_dispatch:
        return _post_recovery_verdict(ctx, state, prev_phase, verdict)
    return _post_recovery_verdict_impl(ctx, state, prev_phase, verdict)


def _post_recovery_verdict_dispatch(
    ctx: LoopContext,
    state: PipelineState,
    prev_phase: str,
    verdict: IntegrationResolutionVerdict,
) -> IntegrationResolutionVerdict | tuple[PipelineState, str, int] | None:
    if post_recovery_verdict is not _public_post_recovery_verdict_dispatch:
        return post_recovery_verdict(ctx, state, prev_phase, verdict)
    return _post_recovery_verdict_impl(ctx, state, prev_phase, verdict)


_post_recovery_verdict_impl = _post_recovery_verdict
_post_recovery_verdict = _post_recovery_verdict_dispatch
_public_post_recovery_verdict_dispatch = post_recovery_verdict


def _run_inner_loop(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> tuple[PipelineState, str, int | None]:
    workspace_root_raw: object = getattr(ctx, "workspace_root", ctx.workspace_scope.root)
    if isinstance(workspace_root_raw, Path):
        legacy_block = legacy_rebase_startup_block(workspace_root_raw)
        if legacy_block is not None:
            state = state.copy_with(rebase=legacy_block)
    state = _apply_startup_rebase_outcomes(state, ctx, backend=backend)
    blocked_integration = _block_unresolved_integration(state, ctx, prev_phase)
    return _continue_after_startup_integration(
        state,
        ctx,
        prev_phase,
        backend=backend,
        blocked_integration=blocked_integration,
    )


def _continue_after_startup_integration(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    blocked_integration: tuple[PipelineState, str, int] | None = None,
) -> tuple[PipelineState, str, int | None]:
    if blocked_integration is None:
        blocked_integration = _block_unresolved_integration(state, ctx, prev_phase)
    if blocked_integration is not None:
        state = _wait_for_integration_retry(blocked_integration[0], ctx, backend=backend)
    return _run_inner_loop_after_startup(state, ctx, prev_phase)


def _with_run_ledger(
    pipeline_deps: PipelineDeps | None,
    controller: RecoveryController,
) -> PipelineDeps | None:
    if pipeline_deps is None:
        return None
    return dataclasses.replace(
        pipeline_deps,
        unavailability_store=controller.unavailability_store,
        ledger=controller.ledger,
        recovery_controller=controller,
    )


def _iteration_pipeline_deps(
    ctx: LoopContext,
    connectivity_provider: Callable[[], str | None],
    waiting_provider: Callable[[], bool],
) -> PipelineDeps | None:
    if ctx.pipeline_deps is None:
        return None
    return dataclasses.replace(
        ctx.pipeline_deps,
        connectivity_state_provider=connectivity_provider,
        is_waiting_state_provider=waiting_provider,
    )


def _initialize_loop_timeboxes(state: PipelineState, ctx: LoopContext) -> PipelineState:
    pipeline_deps = ctx.pipeline_deps if isinstance(ctx, LoopContext) else None
    return _initialize_resumed_timeboxes(
        state,
        ctx.policy_bundle.pipeline,
        _resume_epoch(pipeline_deps),
    )


def _publish_state_snapshot(ctx: LoopContext, state: PipelineState) -> None:
    if ctx.snapshot_registry is None:
        return
    from ralph.pro_support.state_query import build_pipeline_state_snapshot

    ctx.snapshot_registry.publish(build_pipeline_state_snapshot(state, ctx.workspace_scope.root))


def _render_current_observation_detail(rebase: RebaseState) -> str:
    from ralph.pipeline._publication_state_machine import PublicationDecision

    decision = rebase.last_publication_decision
    if decision is None and rebase.last_reason:
        return rebase.last_reason
    if decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value:
        return "configured remote currently unreachable"
    if decision == PublicationDecision.PUBLISH_DEFERRED_FRESHNESS.value:
        return "freshness deferral pending"
    if decision == PublicationDecision.PUBLICATION_PENDING.value:
        return "publication pending"
    if decision == PublicationDecision.PUBLISH_NOW.value:
        return "publication scheduled; recovery will retry"
    return "target synchronization remains unproven"


_REPEAT_ESCALATION_RETRY_SECONDS = 5.0


def _retained_round_fingerprint(ctx: LoopContext) -> str:
    from ralph.pipeline._integration_resolution_gate import retained_round_fingerprint

    try:
        return retained_round_fingerprint(ctx.workspace_scope.root)
    except Exception:
        return "unreadable"


def _ladder_escalation_reason(detail: str, preserved_copy: str) -> str:
    return (
        f"{detail}; preserved copy: {preserved_copy}; "
        "next autonomous rung: preserve the work, then climb the cleanup ladder"
    )


def _wait_for_integration_retry(
    state: PipelineState,
    ctx: LoopContext,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PipelineState:
    rebase = state.rebase
    detail = _render_current_observation_detail(rebase)
    alarm = f"CRITICAL: integration has not landed; recovery will retry: {detail}"

    def _emit_retry_alarm(message: str) -> None:
        critical("{}", message)
        with suppress(Exception):
            emit_integration_warn_line(ctx.active_display, message)

    integration_outcome_log().record(_emit_retry_alarm, alarm)
    rebase = rebase.set_operation_display_state(OperationDisplayState.WAITING)

    from ralph.pipeline._publication_state_machine import (
        PublicationDecision as _PubDec,
    )

    is_publication_deferred = (
        rebase.last_publication_decision
        in (
            _PubDec.PUBLISH_DEFERRED_REMOTE,
            _PubDec.PUBLISH_DEFERRED_FRESHNESS,
            _PubDec.PUBLICATION_PENDING,
            _PubDec.PUBLISH_NOW,
        )
        and not rebase.integration_unresolved
    )
    if not is_publication_deferred and rebase.resolution_exhausted:
        session_attempted: bool = getattr(ctx, "_session_handoff_dispatched", False)
        if not session_attempted:
            rebase_before = (
                ctx.latest_state[0].rebase
                if (ctx.latest_state and ctx.latest_state[0].rebase is not None)
                else None
            )
            _dispatch_retained_handoff(ctx, detail, backend=backend)
            ctx._session_handoff_dispatched = True
            handoff_rebase = (
                ctx.latest_state[0].rebase
                if (ctx.latest_state and ctx.latest_state[0].rebase is not None)
                else None
            )
            if (
                handoff_rebase is not None
                and handoff_rebase != rebase_before
                and not handoff_rebase.is_intervention_required
                and not handoff_rebase.integration_unresolved
                and not handoff_rebase.resolution_exhausted
            ):
                state = state.copy_with(rebase=handoff_rebase).clear_waiting()
                _save_recovered_rebase_checkpoint(state, ctx)
                ctx.latest_state[:] = [state]
                return state
            if handoff_rebase is not None and handoff_rebase != rebase_before:
                rebase = handoff_rebase
        rebase = rebase.record_consecutive_conflicts(rebase.consecutive_conflicts)
        rebase = rebase.set_ladder_escalation(
            _ladder_escalation_reason(
                detail, _preserved_copy_clause_for_root(ctx.workspace_scope.root)
            )
        )
        state = state.copy_with(rebase=rebase, last_error=alarm).set_waiting(
            WaitReason.EXHAUSTED_RETRY, None
        )
        _save_recovered_rebase_checkpoint(state, ctx)
        ctx.latest_state[:] = [state]
        if session_attempted:
            ctx.sleep(_REPEAT_ESCALATION_RETRY_SECONDS)
        return state.clear_waiting()

    if rebase.is_intervention_required:
        return _handle_intervention_required_retry(state, ctx, rebase, alarm, detail, backend)

    state = state.copy_with(rebase=rebase, last_error=alarm).set_waiting(
        WaitReason.EXHAUSTED_RETRY, None
    )
    _save_recovered_rebase_checkpoint(state, ctx)
    ctx.latest_state[:] = [state]
    return _handle_pending_retry(state, ctx, rebase, detail, backend)


def _handle_intervention_required_retry(
    state: PipelineState,
    ctx: LoopContext,
    rebase: RebaseState,
    alarm: str,
    detail: str,
    backend: FileBackend,
) -> PipelineState:
    recovered = _recovery_preamble_for(ctx, backend)(ctx.workspace_scope, ctx.config)
    if (
        recovered is not None
        and not recovered.is_intervention_required
        and not recovered.integration_unresolved
    ):
        state = state.copy_with(rebase=recovered, last_error=alarm).set_waiting(
            WaitReason.EXHAUSTED_RETRY, None
        )
        _save_recovered_rebase_checkpoint(state, ctx)
        ctx.latest_state[:] = [state]
        return state.clear_waiting()
    bumped_conflicts = rebase.consecutive_conflicts + 1
    session_attempted: bool = getattr(ctx, "_session_handoff_dispatched", False)
    if not session_attempted:
        rebase_before = (
            ctx.latest_state[0].rebase
            if (ctx.latest_state and ctx.latest_state[0].rebase is not None)
            else None
        )
        _dispatch_retained_handoff(ctx, detail, backend=backend)
        ctx._session_handoff_dispatched = True
        handoff_rebase = (
            ctx.latest_state[0].rebase
            if (ctx.latest_state and ctx.latest_state[0].rebase is not None)
            else None
        )
        if (
            handoff_rebase is not None
            and handoff_rebase != rebase_before
            and not handoff_rebase.is_intervention_required
            and not handoff_rebase.integration_unresolved
            and not handoff_rebase.resolution_exhausted
        ):
            state = state.copy_with(rebase=handoff_rebase, last_error=alarm).clear_waiting()
            _save_recovered_rebase_checkpoint(state, ctx)
            ctx.latest_state[:] = [state]
            return state
        if handoff_rebase is not None and handoff_rebase != rebase_before:
            rebase = handoff_rebase
    rebase = rebase.record_consecutive_conflicts(bumped_conflicts)
    rebase = rebase.set_ladder_escalation(
        _ladder_escalation_reason(detail, _preserved_copy_clause_for_root(ctx.workspace_scope.root))
    )
    repeat_escalation = session_attempted
    state = state.copy_with(rebase=rebase, last_error=alarm).set_waiting(
        WaitReason.EXHAUSTED_RETRY, None
    )
    _save_recovered_rebase_checkpoint(state, ctx)
    ctx.latest_state[:] = [state]
    if repeat_escalation:
        ctx.sleep(_REPEAT_ESCALATION_RETRY_SECONDS)
    return state.clear_waiting()


_MAX_COOLDOWN_SLEEP_SECONDS = 30.0


def _resolver_cooldown_wait_ms(ctx: LoopContext) -> int:
    from ralph.pipeline.conflict_resolution.resolver_health import choose_resolver
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

    installed = tuple(
        agent
        for agent in resolution_chain_agents(ctx.policy_bundle)
        if ctx.registry.get(agent) is not None
    )
    choice = choose_resolver(ctx.controller, installed)
    return choice.wait_ms if choice.agent is None else 0


def _config_failure_watch_paths(
    ctx: LoopContext,
    watcher: ConfigFileFingerprintWatcher,
    reason: str | None,
) -> tuple[Path, ...]:
    from ralph.pipeline._config_failure_fingerprint import extract_config_path_from_reason
    from ralph.pipeline.config_file_fingerprint_watcher import resolve_config_path_for_workspace

    bad_path = extract_config_path_from_reason(reason)
    if bad_path is not None:
        return (bad_path,)
    candidates: list[Path] = []
    scope: object = getattr(ctx, "workspace_scope", None)
    root: object = getattr(scope, "root", None)
    if isinstance(root, str | Path):
        candidates.append(resolve_config_path_for_workspace(root))
    candidates.extend(watcher.tracked_paths)
    return tuple(candidates)


def _repaired_config_path(
    ctx: LoopContext,
    watcher: ConfigFileFingerprintWatcher,
    reason: str | None,
) -> Path | None:
    for candidate in _config_failure_watch_paths(ctx, watcher, reason):
        if watcher.should_retry(candidate):
            return candidate
    return None


def handle_pending_retry(
    state: PipelineState,
    ctx: LoopContext,
    rebase: RebaseState,
    detail: str,
    backend: FileBackend,
) -> PipelineState:
    cooldown_ms = _resolver_cooldown_wait_ms(ctx) if rebase.integration_unresolved else 0
    if cooldown_ms > 0:
        waiting = rebase.set_operation_display_state(OperationDisplayState.WAITING)
        state = state.copy_with(rebase=waiting).set_waiting(WaitReason.EXHAUSTED_RETRY, None)
        _save_recovered_rebase_checkpoint(state, ctx)
        ctx.latest_state[:] = [state]
        ctx.sleep(min(cooldown_ms / 1000.0, _MAX_COOLDOWN_SLEEP_SECONDS))
        return state.clear_waiting()
    recovered = _recovery_preamble_for(ctx, backend)(ctx.workspace_scope, ctx.config)
    if recovered is not None and not recovered.integration_unresolved:
        from ralph.pipeline._publication_state_machine import (
            PublicationDecision as _PubDec,
        )

        if recovered.last_publication_decision in (
            None,
            _PubDec.PUBLICATION_TERMINAL,
        ):
            state = state.copy_with(rebase=recovered).clear_waiting()
            _save_recovered_rebase_checkpoint(state, ctx)
            ctx.latest_state[:] = [state]
            return state

        if recovered.last_publication_decision in (
            _PubDec.PUBLISH_DEFERRED_REMOTE,
            _PubDec.PUBLISH_DEFERRED_FRESHNESS,
            _PubDec.PUBLICATION_PENDING,
            _PubDec.PUBLISH_NOW,
        ):
            rebase = recovered.set_operation_display_state(OperationDisplayState.WAITING)
            state = state.copy_with(rebase=rebase).set_waiting(WaitReason.EXHAUSTED_RETRY, None)
            _save_recovered_rebase_checkpoint(state, ctx)
            ctx.latest_state[:] = [state]
            ctx.sleep(5.0)
            return state.clear_waiting()

    active_rebase = recovered or rebase
    if active_rebase.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE:
        rebase = active_rebase.set_operation_display_state(OperationDisplayState.WAITING)
        from ralph.pipeline._config_failure_fingerprint import ConfigFileFingerprintWatcher

        watcher = ctx.config_failure_watcher
        if watcher is None:
            watcher = ConfigFileFingerprintWatcher()
            ctx.config_failure_watcher = watcher
        repaired_path = _repaired_config_path(ctx, watcher, active_rebase.last_reason)
        if repaired_path is not None:
            logger.info(
                "configuration failure self-recovery: {} repaired; retrying {}",
                repaired_path,
                "continuation" if recovered is not None else "paused rebase",
            )
            state = state.copy_with(rebase=active_rebase).clear_waiting()
            _save_recovered_rebase_checkpoint(state, ctx)
            ctx.latest_state[:] = [state]
            return state
    else:
        bumped_conflicts = (
            max(rebase.consecutive_conflicts, (recovered.consecutive_conflicts if recovered else 0))
            + 1
        )
        rebase = active_rebase.record_consecutive_conflicts(bumped_conflicts)
        rebase = rebase.set_operation_display_state(OperationDisplayState.WAITING)
    state = state.copy_with(rebase=rebase).set_waiting(WaitReason.EXHAUSTED_RETRY, None)
    _save_recovered_rebase_checkpoint(state, ctx)
    ctx.latest_state[:] = [state]
    ctx.sleep(5.0)
    return state.clear_waiting()


def _handle_pending_retry(
    state: PipelineState,
    ctx: LoopContext,
    rebase: RebaseState,
    detail: str,
    backend: FileBackend,
) -> PipelineState:
    return handle_pending_retry(state, ctx, rebase, detail, backend)


def _handle_cooldown_delay(state: PipelineState, ctx: LoopContext) -> PipelineState:
    delay_ms = state.last_retry_delay_ms
    if not isinstance(delay_ms, int) or delay_ms <= 0:
        return state
    is_all_unavailable = state.is_waiting_state
    if not is_all_unavailable:
        ctx.last_waiting_state_phase = None
        ctx.last_waiting_state_sig = None
    try:
        current_phase_str = str(state.phase)
        unavail_reason = state.last_unavailability_reason or "unknown"
        waiting_sig = (current_phase_str, unavail_reason)
        if is_all_unavailable and ctx.last_waiting_state_sig != waiting_sig:
            emit_activity_line(
                ctx.active_display,
                None,
                status_text(
                    "WAITING",
                    f"all agents unavailable (last reason: {unavail_reason});"
                    f" resuming in {int(delay_ms / 1000.0)} seconds "
                    f"(next agent attempt: {current_phase_str})",
                    "yellow",
                ),
            )
            _log_waiting_state(state, ctx, current_phase_str, unavail_reason, delay_ms)

        if is_all_unavailable:
            state = state.copy_with(
                rebase=state.rebase.set_operation_display_state(OperationDisplayState.WAITING),
                last_retry_delay_ms=0,
            )
        else:
            state = state.copy_with(last_retry_delay_ms=0)
        if is_all_unavailable:
            logger.bind(recovery=True).debug(
                "Starting cooldown sleep for {delay_seconds:.3f} seconds in phase '{phase}'.",
                delay_seconds=delay_ms / 1000.0,
                phase=current_phase_str,
            )

        ctx.sleep(delay_ms / 1000.0)

        if is_all_unavailable:
            state = _resume_after_cooldown_wait(
                state, ctx, current_phase_str, unavail_reason, delay_ms
            )
        return state
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        chain = state.chain_for_phase(state.phase)
        current_idx = chain.current_index if chain is not None else None
        logger.bind(recovery=True).error(
            "Error during cooldown sleep: {} (type={}) last_error={} "
            "chain_index={} phase={} wait_ms={} is_all_unavailable={}",
            e,
            type(e).__name__,
            state.last_error,
            current_idx,
            current_phase_str,
            delay_ms,
            is_all_unavailable,
        )
        return state.copy_with(last_retry_delay_ms=1000)


handle_cooldown_delay = _handle_cooldown_delay


_MAX_RECOVERY_EPOCHS: int = 3


def ledger_requires_intervention(state: PipelineState, controller: RecoveryController) -> bool:
    target_phase = state.previous_phase or state.phase
    chain = state.chain_for_phase(target_phase)
    agents = chain.agents if chain is not None and chain.agents else ()
    ledger = controller.ledger
    if agents:
        from ralph.recovery.agent_failure_ledger import Cooldown, Spent

        statuses = tuple(ledger.status(target_phase, agent) for agent in agents)
        if any(isinstance(status, Cooldown) for status in statuses):
            return False
        if all(isinstance(status, Spent) for status in statuses):
            return True
    else:
        snap = state.ledger_snapshot or ledger.snapshot()
        raw_entries = snap.get("entries") if isinstance(snap, Mapping) else None
        if isinstance(raw_entries, Mapping) and raw_entries:
            entries = tuple(item for item in raw_entries.values() if isinstance(item, Mapping))
            if any(item.get("cooldown_until_ms") is not None for item in entries):
                return False
            if any(item.get("is_spent") for item in entries):
                return True
    return (
        state.recovery_epoch >= _MAX_RECOVERY_EPOCHS
        or state.last_execution_result_status == "failed"
    )


def _cycle_cap_exhausted(state: PipelineState, ctx: LoopContext) -> bool:
    count = state.recovery_cycle_count
    return isinstance(count, int) and count >= ctx.controller.cycle_cap


def _run_inner_loop_after_startup(
    state: PipelineState,
    ctx: LoopContext,
    prev_phase: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> tuple[PipelineState, str, int | None]:
    state_holder: list[PipelineState] = [state]

    def _live_connectivity() -> str | None:
        return str(ctx.connectivity_monitor.current_state.value)

    def _live_is_waiting() -> bool:
        return bool(state_holder[0].is_waiting_state)

    last_status_sig: (
        tuple[str, int | None, int | None, str | None, str | None, str | None, str | None] | None
    ) = None
    cycle_sample_box: list[float | None] = [None]
    development_sample_box: list[float | None] = [None]
    state = _initialize_loop_timeboxes(state, ctx)
    while (
        (
            state.phase != ctx.policy_bundle.pipeline.terminal_phase
            and state.phase not in ctx.policy_bundle.pipeline.terminal_states()
        )
        or (
            state.phase == ctx.policy_bundle.pipeline.recovery.failed_route
            and not ledger_requires_intervention(state, ctx.controller)
            and not _cycle_cap_exhausted(state, ctx)
        )
        or (
            ctx.policy_bundle.pipeline.terminal_phase == state.phase
            and (
                retained_integration_reason(ctx.workspace_scope.root) is not None
                or not is_terminal_success_eligible(
                    state,
                    read_record(ctx.workspace_scope.root),
                    state.rebase,
                )
            )
        )
    ):
        captured_phase = str(state.phase)
        blocked_integration = _block_unresolved_integration(state, ctx, prev_phase)
        if blocked_integration is not None:
            state = _wait_for_integration_retry(blocked_integration[0], ctx, backend=backend)
            continue
        state = _reconcile_dispatch_state(state)
        state, blocked_sync = _ensure_planning_in_sync(state, ctx, prev_phase)
        if blocked_sync is not None:
            state = _wait_for_integration_retry(state, ctx, backend=backend)
            continue
        if (
            ctx.policy_bundle.pipeline.terminal_phase == state.phase
            and not is_terminal_success_eligible(
                state,
                read_record(ctx.workspace_scope.root),
                state.rebase,
            )
        ):
            from ralph.pipeline._fan_out_authority import (
                FanOutDecision,
                fan_out_decision,
            )

            _canonical_fan_out = fan_out_decision(state)
            if (
                _canonical_fan_out is not None
                and _canonical_fan_out != FanOutDecision.JOIN_COMPLETE
            ):
                return state, prev_phase, 1
            state = _wait_for_integration_retry(state, ctx, backend=backend)
            continue
        state = _apply_connectivity_check(state, ctx.connectivity_monitor)
        state_holder[:] = ctx.latest_state[:] = [state]
        iter_pipeline_deps = _iteration_pipeline_deps(ctx, _live_connectivity, _live_is_waiting)
        runner_step = cast("_RunPipelineStepFn", _runner_module.run_pipeline_step)
        planning_baseline = _capture_planning_baseline_if_entry(state, ctx)
        step_result = runner_step(
            state=state,
            policy_bundle=ctx.policy_bundle,
            workspace_scope=ctx.workspace_scope,
            config=ctx.config,
            display=ctx.active_display,
            display_context=ctx.display_context,
            verbosity=ctx.effective_verbosity,
            registry=ctx.registry,
            pipeline_subscriber=ctx.effective_pipeline_subscriber,
            recovery_controller=ctx.controller,
            config_path=ctx.config_path,
            cli_overrides=ctx.cli_overrides,
            _monitor_stop_cb=ctx.monitor_stop,
            pipeline_deps=iter_pipeline_deps,
            _cycle_sample_box=cycle_sample_box,
            _development_sample_box=development_sample_box,
        )
        if planning_baseline is not None and not isinstance(step_result, int):
            with suppress(Exception):
                _resolve_planning_residue_after_dispatch(ctx, planning_baseline)
        step_result = _resume_planning_after_commit(step_result, ctx, state, backend=backend)
        if isinstance(step_result, int):
            return state, prev_phase, step_result
        state = step_result
        last_status_sig = _push_status_bar_if_changed(
            ctx.active_display,
            state,
            ctx.policy_bundle,
            ctx.workspace_scope.root,
            last_status_sig,
        )
        _publish_state_snapshot(ctx, state)
        state = _handle_cooldown_delay(state, ctx)
        if str(state.phase) != captured_phase:
            state = _reselect_preferred_agent(state, ctx)
        prev_phase = _runner_module.emit_phase_transition_if_changed(
            ctx.active_display,
            prev_phase,
            state,
            verbosity=ctx.effective_verbosity,
            pipeline_policy=ctx.policy_bundle.pipeline,
        )
        if hasattr(ctx.active_display, "begin_phase"):
            with suppress(Exception):
                cast("_PhaseAwareDisplay", ctx.active_display).begin_phase(state.phase)
    return state, prev_phase, None


def _emit_post_loop_result(
    state: PipelineState,
    active_display: ParallelDisplay,
    is_quiet: bool,
    exit_code: int,
    policy_bundle: PolicyBundle,
) -> None:
    if not is_quiet and hasattr(active_display, "emit_run_end"):
        with suppress(Exception):
            total_agent_calls = cast("int", getattr(state.metrics, "total_agent_calls", 0))
            _exit_trigger = "completed" if exit_code == 0 else "failed"
            _outer_dev = next(
                (
                    state.get_outer_progress(bp_name)
                    for bp_name, bp_cfg in policy_bundle.pipeline.budget_counters.items()
                    if bp_cfg.tracks_budget and state.get_outer_progress(bp_name) > 0
                ),
                None,
            )
            cast("_RunEndDisplay", active_display).emit_run_end(
                phase=state.phase,
                total_agent_calls=total_agent_calls,
                pr_url=state.pr_url,
                exit_trigger=_exit_trigger,
                outer_dev_iteration=_outer_dev,
            )
            if exit_code == 0:
                with suppress(Exception):
                    active_display.emit(
                        unit_id="run",
                        line=f"\n{RUN_COMPLETION_STAR_CTA}",
                    )


def _effect_executor_seam_injected() -> bool:
    return (
        _runner_module.execute_effect is not _runner_module._execute_effect
        or _runner_module.execute_effect_with_optional_display
        is not _runner_module._production_execute_effect_with_optional_display
        or _runner_module.invoke_execute_effect_with_optional_display
        is not _runner_module._invoke_execute_effect_with_optional_display
    )


def _seamed_loop_probe_runner(
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None,
) -> Callable[[str, str], ProbeOutcome | None] | None:
    injected = pipeline_deps.invoke_agent if pipeline_deps is not None else None
    probe_runner = seamed_dead_agent_probe_runner(config, injected)
    if probe_runner is None and _effect_executor_seam_injected():
        return fail_open_dead_agent_probe_runner()
    return probe_runner


def _build_recovery_controller(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    config: UnifiedConfig,
    clock: Clock | None = None,
    workspace_probe: WorkspaceChangeProbe | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> tuple[RecoveryController, int]:
    _cycle_cap: int = 200
    _raw_cycle_cap: object = getattr(state, "recovery_cycle_cap", 200)
    if isinstance(_raw_cycle_cap, int) and _raw_cycle_cap >= 1:
        _cycle_cap = _raw_cycle_cap

    raw_consecutive: object = getattr(
        config.general,
        "agent_max_consecutive_unproductive",
        None,
    )
    consecutive_limit = (
        raw_consecutive
        if isinstance(raw_consecutive, int) and raw_consecutive >= 1
        else AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
    )
    raw_total: object = getattr(
        config.general,
        "agent_max_total_unproductive",
        None,
    )
    total_limit = (
        raw_total
        if isinstance(raw_total, int) and raw_total >= 1
        else AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT
    )
    effective_clock = clock or SystemClock()
    effective_probe = (
        workspace_probe if workspace_probe is not None else DefaultWorkspaceChangeProbe()
    )
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=config,
        policy_bundle=policy_bundle,
        deps=AttemptRunnerDeps(
            clock=effective_clock,
            invoke=PRODUCTION_INVOKE,
            probe_runner=_seamed_loop_probe_runner(config, pipeline_deps),
            workspace_probe=effective_probe,
            connectivity=None,
        ),
        ledger_snapshot=state.ledger_snapshot,
        false_bench_snapshot=state.false_bench_feedback,
        cycle_cap=_cycle_cap,
        agent_max_consecutive_unproductive=consecutive_limit,
        agent_max_total_unproductive=total_limit,
    )
    return controller, _cycle_cap


_PRODUCTION_BUILD_RECOVERY_CONTROLLER = _build_recovery_controller


def build_recovery_controller(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    config: UnifiedConfig,
    clock: Clock | None = None,
    workspace_probe: WorkspaceChangeProbe | None = None,
) -> tuple[RecoveryController, int]:
    return _build_recovery_controller(
        state, policy_bundle, config, clock=clock, workspace_probe=workspace_probe
    )


def _subscribe_recovery_logger(controller: RecoveryController) -> Callable[[], None]:

    def _log_recovery_event(evt: object) -> None:
        if isinstance(evt, _FailureEvent):
            log = logger.bind(recovery=True)
            message = (
                "VALIDATION FAILURE phase={} agent={} category={} counted={}"
                " chain_cap={} cycle={} delay_ms={}"
                " unavailability_reason={}"
                if evt.category == "artifact_validation"
                else "FAILURE phase={} agent={} category={} counted={}"
                " chain_cap={} cycle={} delay_ms={}"
                " unavailability_reason={}"
            )
            if evt.category == "artifact_validation":
                log.error(
                    message,
                    evt.phase,
                    evt.agent,
                    evt.category,
                    evt.counted_against_budget,
                    evt.chain_capacity_remaining,
                    evt.recovery_cycle,
                    evt.retry_delay_ms,
                    evt.unavailability_reason,
                )
            else:
                log.info(
                    message,
                    evt.phase,
                    evt.agent,
                    evt.category,
                    evt.counted_against_budget,
                    evt.chain_capacity_remaining,
                    evt.recovery_cycle,
                    evt.retry_delay_ms,
                    evt.unavailability_reason,
                )
        elif isinstance(evt, _FalloverEvent):
            logger.bind(recovery=True).info(
                "FALLOVER phase={} from={} to={} reason={}"
                " watchdog_reason={} unavailability_reason={}",
                evt.phase,
                evt.from_agent,
                evt.to_agent,
                evt.reason,
                evt.watchdog_reason,
                evt.unavailability_reason,
            )

    return controller.event_bus.subscribe(_log_recovery_event)


def _subscribe_recovery_display(
    controller: RecoveryController,
    display: ParallelDisplay,
    interval_seconds: float,
    now: Callable[[], float],
) -> Callable[[], None]:
    cadence_map: dict[str, float] = {}

    def _maybe_emit(tag: str, now_ts: float, *, build: Callable[[], str]) -> None:
        last = cadence_map.get(tag, now_ts - interval_seconds)
        if now_ts - last < interval_seconds:
            return
        cadence_map[tag] = now_ts
        emit_activity_line(display, None, build())

    def _display_recovery_event(evt: object) -> None:
        try:
            now_ts = now()
            if isinstance(evt, _FalloverEvent):
                _maybe_emit(
                    "fallover",
                    now_ts,
                    build=lambda: status_text(
                        "RECOVERING",
                        f"falling over from {evt.from_agent} to {evt.to_agent} ({evt.reason})",
                        "yellow",
                    ),
                )
            elif isinstance(evt, _FailureEvent):
                if evt.chain_capacity_remaining <= 0:
                    label = "STOPPING"
                    if evt.watchdog_reason is not None:
                        value = (
                            f"agent stalled: {evt.watchdog_reason}; "
                            f"chain exhausted ({evt.category}: "
                            f"{evt.reason or 'no detail'})"
                        )
                    else:
                        value = f"chain exhausted ({evt.category}: {evt.reason or 'no detail'})"
                    style = "red"
                    tag = "terminal"
                elif evt.category == "artifact_validation":
                    label = "VALIDATION FAILURE"
                    value = f"phase={evt.phase}; {evt.reason or 'artifact validation failed'}"
                    style = "red"
                    tag = "validation_recoverable"
                elif evt.watchdog_reason is not None:
                    label = "RECOVERING"
                    value = f"agent stalled: {evt.watchdog_reason}; resuming"
                    style = "yellow"
                    tag = "watchdog_recoverable"
                elif evt.unavailability_reason is not None:
                    label = "RECOVERING"
                    value = (
                        f"agent unavailable: {evt.unavailability_reason}; "
                        f"cooldown {evt.retry_delay_ms}ms; selecting fallback"
                    )
                    style = "yellow"
                    tag = "unavailable_recoverable"
                else:
                    return
                _maybe_emit(
                    tag,
                    now_ts,
                    build=lambda: status_text(label, value, style),
                )
        except Exception:
            logger.bind(recovery=True).debug(
                "Recovery display subscriber raised; swallowing",
                exc_info=True,
            )

    return controller.event_bus.subscribe(_display_recovery_event)


def _resolve_effective_subscriber(
    dashboard_subscriber: _PipelineSubscriberProtocol | None,
    pipeline_subscriber: _PipelineSubscriberProtocol | None,
    active_display: ParallelDisplay,
) -> _PipelineSubscriberProtocol | None:
    effective = dashboard_subscriber or pipeline_subscriber
    if effective is None and hasattr(active_display, "subscriber"):
        effective = cast(
            "_PipelineSubscriberProtocol | None",
            getattr(active_display, "subscriber", None),
        )
    return effective


def _handle_keyboard_interrupt(
    state: PipelineState,
    loop_ctx: LoopContext,
) -> int:
    logger.warning("Interrupted by user; shutting down tracked processes.")
    emit_activity_line(
        loop_ctx.active_display,
        None,
        status_text(
            "Interrupted",
            "Stopping gracefully. Press Ctrl+C again to force kill child processes.",
            "yellow",
        ),
    )
    _runner_module.handle_keyboard_interrupt(loop_ctx.monitor_stop)
    loop_ctx.monitor_stop = None
    interrupted_state = state.copy_with(interrupted_by_user=True)
    _runner_module.save_checkpoint_or_log(
        interrupted_state,
        message="Checkpoint save failed while handling interrupt in phase={phase}: {err}",
        path=_runner_module._checkpoint_path(loop_ctx.workspace_scope),
    )
    return 130


def _run_cleanup_step(resource: str, stage: str, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:
        logger.exception(
            "pipeline cleanup failed resource={} stage={}",
            resource,
            stage,
        )


def _cleanup_pipeline(
    loop_ctx: LoopContext,
    unsubscribe_bus: Callable[[], None],
    unsubscribe_display: Callable[[], None],
    display_stop: Callable[[], None],
    state: PipelineState,
) -> None:
    _run_cleanup_step("unsubscribe_bus", "unsubscribe", unsubscribe_bus)
    _run_cleanup_step("unsubscribe_display", "unsubscribe", unsubscribe_display)
    active_display_obj = loop_ctx.active_display
    if active_display_obj is not None and hasattr(active_display_obj, "set_watchdog_attention"):

        def _clear_watchdog_attention() -> None:
            active_display_obj.set_watchdog_attention(None)

        _run_cleanup_step("watchdog_attention", "clear", _clear_watchdog_attention)
    _run_cleanup_step("display", "stop", display_stop)
    if loop_ctx.monitor_stop is not None:
        _run_cleanup_step("connectivity_monitor", "stop", loop_ctx.monitor_stop)
    if loop_ctx.pro_watcher is not None:
        _run_cleanup_step("pro_watcher", "stop", loop_ctx.pro_watcher.stop)
    if loop_ctx.heartbeat_client is not None:
        _run_cleanup_step("heartbeat_client", "stop", loop_ctx.heartbeat_client.stop)
    if loop_ctx.catchup_worker is not None:
        _run_cleanup_step("catchup_worker", "stop", loop_ctx.catchup_worker.stop)

    def _emit_summary() -> None:
        emit_final_summary(
            state,
            loop_ctx.workspace_scope.root,
            subscriber=cast("PipelineSubscriber | None", loop_ctx.effective_pipeline_subscriber),
            display=loop_ctx.active_display,
            display_context=loop_ctx.display_context,
        )

    _run_cleanup_step("final_summary", "emit", _emit_summary)
    _run_cleanup_step(
        "cycle_baseline",
        "clear",
        lambda: _runner_module.clear_cycle_baseline(loop_ctx.workspace_scope.root),
    )
    teardown = loop_ctx.process_teardown
    if teardown is None:

        def teardown() -> None:
            get_process_manager().shutdown_all(grace_period_s=0.5)

    _run_cleanup_step("process_teardown", "teardown", teardown)


def _report_pending_remote_publication(state: PipelineState, ctx: LoopContext) -> PipelineState:
    pending_status = state.rebase.last_push_status
    if pending_status in (None, "pushed", "created"):
        return state
    from ralph.pipeline.auto_integrate import resolve_integration_target

    target = resolve_integration_target(ctx.config, ctx.workspace_scope.root)
    if target is None:
        return state
    from ralph.pipeline.auto_integrate_remote_sync import remote_target_name

    remote = remote_target_name(ctx.config)
    reason = (
        _render_current_observation_detail(state.rebase) or state.rebase.last_push or pending_status
    )
    return state.copy_with(
        rebase=state.rebase.model_copy(
            update={
                "last_reason": (
                    f"local work landed on {target}; not published to {remote}: {reason}"
                )
            }
        )
    )


def _execute_with_cleanup(
    initial_state: PipelineState,
    loop_ctx: LoopContext,
    prev_phase: str,
    unsubscribe_bus: Callable[[], None],
    unsubscribe_display: Callable[[], None],
    display_stop: Callable[[], None],
    *,
    display_is_active: bool = False,
) -> int:
    exit_code = 0
    state = initial_state
    started_at = time.monotonic()
    try:
        display_scope = (
            nullcontext(loop_ctx.active_display) if display_is_active else loop_ctx.active_display
        )
        with display_scope:
            _emit_run_start(loop_ctx, state)
            if hasattr(loop_ctx.active_display, "begin_phase"):
                with suppress(Exception):
                    cast("_PhaseAwareDisplay", loop_ctx.active_display).begin_phase(state.phase)
            _push_status_bar_if_changed(
                loop_ctx.active_display,
                state,
                loop_ctx.policy_bundle,
                loop_ctx.workspace_scope.root,
                last_sig=None,
            )
            _runner_module.notify_pipeline_subscriber(loop_ctx.effective_pipeline_subscriber, state)
            loop_ctx.latest_state[:] = [state]
            try:
                state, prev_phase, early_exit = _run_inner_loop(state, loop_ctx, prev_phase)
                if early_exit is not None:
                    exit_code = early_exit
                    return exit_code
            except KeyboardInterrupt:
                state = loop_ctx.latest_state[0]
                exit_code = _handle_keyboard_interrupt(state, loop_ctx)
                return exit_code
            except BaseException:
                state = loop_ctx.latest_state[0]
                exit_code = 1
                raise
            record = read_record(loop_ctx.workspace_scope.root)
            decision = decide_phase_transition(
                state,
                loop_ctx.policy_bundle.pipeline,
                integration_record=record,
                rebase_state=state.rebase,
            )
            if (
                loop_ctx.policy_bundle.pipeline.terminal_phase == state.phase
                and decision == PhaseDecision.REPORT_TERMINAL_SUCCESS
                and retained_integration_reason(loop_ctx.workspace_scope.root) is None
                and is_terminal_success_eligible(state, record, state.rebase)
            ):
                state = state.copy_with(
                    rebase=state.rebase.set_operation_display_state(OperationDisplayState.TERMINAL)
                )
                loop_ctx.latest_state[:] = [state]
                try:
                    state = _report_pending_remote_publication(state, loop_ctx)
                except KeyboardInterrupt:
                    exit_code = _handle_keyboard_interrupt(state, loop_ctx)
                    return exit_code
                loop_ctx.active_display.emit(
                    "run", "[green]Pipeline completed successfully.[/green]"
                )
            else:
                state = state.copy_with(
                    rebase=state.rebase.set_operation_display_state(OperationDisplayState.TERMINAL)
                )
                loop_ctx.latest_state[:] = [state]
                emit_activity_line(
                    loop_ctx.active_display,
                    None,
                    status_text("Pipeline failed", state.last_error or "Unknown error", "red"),
                )
                exit_code = 1
            _emit_post_loop_result(
                state, loop_ctx.active_display, loop_ctx.is_quiet, exit_code, loop_ctx.policy_bundle
            )
            final_status_state = state.copy_with(run_outcome="terminated")
            _push_status_bar_if_changed(
                loop_ctx.active_display,
                final_status_state,
                loop_ctx.policy_bundle,
                loop_ctx.workspace_scope.root,
                last_sig=None,
            )
    finally:
        emit_run_time_report_safely(
            loop_ctx.workspace_scope.root,
            state=state,
            outcome="completed" if exit_code == 0 else "failed",
            elapsed_seconds=time.monotonic() - started_at,
            cycle_timebox=loop_ctx.policy_bundle.pipeline.cycle_timebox,
            development_timebox=loop_ctx.policy_bundle.pipeline.development_timebox,
        )
        _cleanup_pipeline(loop_ctx, unsubscribe_bus, unsubscribe_display, display_stop, state)
    return exit_code


def _run_startup_sequence(pipeline_deps: PipelineDeps | None) -> WorkspaceScope | None:
    if pipeline_deps is None:
        workspace_scope = _runner_module.resolve_workspace_scope()
        _runner_module.write_start_commit_if_absent(workspace_scope.root)
        validator: Callable[[Path], int] = _runner_module.validate_custom_mcp_servers
    elif (
        pipeline_deps.resolve_workspace_scope_fn is None
        and pipeline_deps.write_start_commit_fn is None
        and pipeline_deps.validate_custom_mcp_servers_fn is None
    ):
        workspace_scope = _runner_module.resolve_workspace_scope()
        _runner_module.write_start_commit_if_absent(workspace_scope.root)
        validator = _runner_module.validate_custom_mcp_servers
    else:
        resolve_scope = (
            pipeline_deps.resolve_workspace_scope_fn
            if pipeline_deps.resolve_workspace_scope_fn is not None
            else _runner_module.resolve_workspace_scope
        )
        write_start_commit = (
            pipeline_deps.write_start_commit_fn
            if pipeline_deps.write_start_commit_fn is not None
            else _runner_module.write_start_commit_if_absent
        )
        validator = (
            pipeline_deps.validate_custom_mcp_servers_fn
            if pipeline_deps.validate_custom_mcp_servers_fn is not None
            else _runner_module.validate_custom_mcp_servers
        )
        if pipeline_deps.resolve_workspace_scope_fn is not None:
            workspace_scope = resolve_scope(None)
        else:
            workspace_scope = _runner_module.resolve_workspace_scope()
        write_start_commit(workspace_scope.root)
    if validator(workspace_scope.root) != 0:
        return None
    return workspace_scope


def run(
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    display: ParallelDisplay | None = None,
    display_is_active: bool = False,
    pipeline_subscriber: _PipelineSubscriberProtocol | None = None,
    *,
    dashboard_subscriber: _PipelineSubscriberProtocol | None = None,
    verbosity: Verbosity | None = None,
    connectivity_monitor: _ConnectivityMonitorLike | None = None,
    display_context: DisplayContext | None = None,
    counter_overrides: dict[str, int] | None = None,
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    _recovery_sleep: Callable[[float], None] | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    policy_bundle_factory: Callable[[WorkspaceScope, UnifiedConfig], PolicyBundle] | None = None,
    registry_factory: Callable[[UnifiedConfig], _RegistryLike] | None = None,
    state_factory: Callable[
        [UnifiedConfig, AgentsPolicy, PipelinePolicy, dict[str, int] | None],
        PipelineState,
    ]
    | None = None,
    recovery_controller_factory: Callable[
        [PipelineState, PolicyBundle, UnifiedConfig],
        tuple[RecoveryController, int],
    ]
    | None = None,
    marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None = None,
    snapshot_registry: SnapshotRegistry | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> int:
    workspace_scope = _run_startup_sequence(pipeline_deps)
    if workspace_scope is None:
        return 1

    if pipeline_deps is not None and (
        policy_bundle_factory is not None
        or registry_factory is not None
        or state_factory is not None
        or recovery_controller_factory is not None
        or marker_watcher_factory is not None
        or snapshot_registry is not None
        or _recovery_sleep is not None
    ):
        raise ValueError(
            "Passing factory kwargs alongside pipeline_deps is not supported. "
            "Use build_default_pipeline_deps() to construct a PipelineDeps bundle "
            "with your overrides, then pass only pipeline_deps."
        )

    if pipeline_deps is not None and display_context is None:
        display_context = pipeline_deps.display_context

    collaborators = _resolve_run_collaborators(
        config=config,
        workspace_scope=workspace_scope,
        initial_state=initial_state,
        counter_overrides=counter_overrides,
        pipeline_deps=pipeline_deps,
        pro_hooks=pro_hooks,
        policy_bundle_factory=policy_bundle_factory,
        registry_factory=registry_factory,
        state_factory=state_factory,
        recovery_controller_factory=recovery_controller_factory,
        marker_watcher_factory=marker_watcher_factory,
        snapshot_registry=snapshot_registry,
        _recovery_sleep=_recovery_sleep,
        verbosity=verbosity,
        connectivity_monitor=connectivity_monitor,
    )
    policy_bundle = collaborators.policy_bundle
    registry = collaborators.registry
    state = collaborators.state
    effective_verbosity = collaborators.effective_verbosity
    is_quiet = collaborators.is_quiet
    _sleep = collaborators.sleep
    connectivity_monitor = collaborators.connectivity_monitor
    _monitor_stop = collaborators.monitor_stop
    _controller = collaborators.controller

    _runner_module.register_role_handlers(policy_bundle.pipeline)
    _unsubscribe_bus = _subscribe_recovery_logger(_controller)
    logger.info(
        "Starting pipeline: phase={}, budget_caps={} [build={}]",
        state.phase,
        state.budget_caps,
        build_identity(),
    )
    if pipeline_subscriber is None:
        pipeline_subscriber = dashboard_subscriber
    active_display, display_context, _display_stop = _setup_active_display(
        display, is_quiet, display_context, workspace_scope, policy_bundle
    )
    effective_pipeline_subscriber = _resolve_effective_subscriber(
        dashboard_subscriber, pipeline_subscriber, active_display
    )

    _pro_watcher, _heartbeat_client = _start_pro_marker_watcher(
        workspace_scope.root,
        watcher_factory=collaborators.marker_watcher_factory,
    )
    _heartbeat_client = _apply_legacy_heartbeat_override(workspace_scope.root, _heartbeat_client)
    _unsubscribe_display = _subscribe_recovery_display(
        _controller,
        active_display,
        _resolve_recovery_display_interval(config),
        now=time.monotonic,
    )
    loop_ctx = LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
        config=config,
        active_display=active_display,
        display_context=display_context,
        effective_verbosity=effective_verbosity,
        registry=registry,
        effective_pipeline_subscriber=effective_pipeline_subscriber,
        controller=_controller,
        config_path=config_path,
        cli_overrides=dict(cli_overrides or {}),
        monitor_stop=_monitor_stop,
        connectivity_monitor=connectivity_monitor,
        sleep=_sleep,
        is_quiet=is_quiet,
        heartbeat_client=_heartbeat_client,
        pro_watcher=_pro_watcher,
        snapshot_registry=collaborators.snapshot_registry,
        pipeline_deps=_with_run_ledger(pipeline_deps, _controller),
        catchup_worker=(
            pipeline_deps.catchup_worker_factory(config, workspace_scope.root)
            if pipeline_deps is not None and pipeline_deps.catchup_worker_factory is not None
            else start_catchup_worker_if_enabled(config, workspace_scope.root)
        ),
        process_teardown=pipeline_deps.process_teardown if pipeline_deps is not None else None,
    )
    return _execute_with_cleanup(
        state,
        loop_ctx,
        state.phase,
        _unsubscribe_bus,
        _unsubscribe_display,
        _display_stop,
        display_is_active=display_is_active,
    )


def _start_pro_marker_watcher(
    workspace_root: Path,
    *,
    watcher_factory: Callable[[Path], ProMarkerWatcher] | None = None,
) -> tuple[ProMarkerWatcher | None, ProHeartbeatClient | None]:
    from ralph.pro_support.watcher import ProMarkerWatcher

    def _default_factory(ws_root: Path) -> ProMarkerWatcher:
        return ProMarkerWatcher(workspace_root=ws_root)

    factory = watcher_factory or _default_factory
    watcher = factory(workspace_root)
    watcher.start()
    return watcher, watcher.heartbeat_client


def _start_pro_heartbeat_if_active(
    workspace_root: Path,
) -> ProHeartbeatClient | None:
    _watcher, client = _start_pro_marker_watcher(workspace_root)
    _ = _watcher
    return client


resolve_initial_state = _resolve_initial_state
build_status_bar_model = _build_status_bar_model
