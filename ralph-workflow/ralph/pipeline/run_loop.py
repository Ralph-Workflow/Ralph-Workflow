"""Pipeline event loop: the run() entry point and connectivity helpers."""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

import ralph.pipeline.runner as _runner_module
from ralph.config.enums import Verbosity
from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.parallel_display import (
    ParallelDisplay,
    build_default_display_legacy_bridge,
    emit_activity_line,
    phase_style_for_phase,
    status_text,
)
from ralph.display.status_bar import StatusBarModel
from ralph.onboarding import RUN_COMPLETION_STAR_CTA
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, normalize_verbosity, verbosity_rank
from ralph.pipeline.phase_transition import (
    build_phase_entry_model_from_state,
    emit_final_summary,
)
from ralph.process.manager import get_process_manager
from ralph.recovery.budget import seed_budget_registry as _seed_budget_registry
from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityMonitor, ConnectivityState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent as _FailureEvent
from ralph.recovery.events import FalloverEvent as _FalloverEvent
from ralph.timeout_defaults import WAITING_STATUS_INTERVAL_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Protocol

    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PipelinePolicy, PolicyBundle
    from ralph.pro_support.heartbeat import ProHeartbeatClient
    from ralph.pro_support.hooks import ProPipelineHooks
    from ralph.pro_support.state_query import SnapshotRegistry
    from ralph.pro_support.watcher import ProMarkerWatcher
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
class _LoopContext:
    """Execution context for the inner pipeline loop."""

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
    monitor_stop: Callable[[], None] | None
    connectivity_monitor: _ConnectivityMonitorLike
    sleep: Callable[[float], None]
    is_quiet: bool
    heartbeat_client: ProHeartbeatClient | None = None
    pro_watcher: ProMarkerWatcher | None = None
    snapshot_registry: SnapshotRegistry | None = None
    pipeline_deps: PipelineDeps | None = None
    last_waiting_state_phase: str | None = None
    # Session-wide process teardown. Defaults to
    # ``ProcessManager.shutdown_all`` from the run-loop construction
    # site so non-phase-labeled children are reaped on every exit
    # (normal, error, SIGINT, SIGTERM). Wired from
    # ``pipeline_deps.process_teardown`` when provided.
    process_teardown: Callable[[], None] | None = None


def _sync_live_display_context(display: _DisplayContextOwner, ctx: DisplayContext) -> None:
    """Keep the runner's active display on the same context as the runner.

    Mutates the existing ``display._ctx`` in place (via
    ``object.__setattr__`` to bypass :class:`DisplayContext`'s
    ``frozen=True`` constraint) so the *identity* of the context object
    is preserved across width refreshes. Callers holding a reference
    to the original context (e.g. ``_LoopContext.display_context``)
    therefore see the updated width automatically, with no need to
    re-fetch ``active._ctx`` after every refresh. Mutating in place
    avoids the staleness bug where the refresher would otherwise
    replace ``display._ctx`` with a new object, leaving the caller's
    separate reference pointing at the stale original.
    """
    existing = display._ctx
    object.__setattr__(existing, "width", ctx.width)


def _signal_if_now_online(monitor: _ConnectivityMonitorLike, wake: threading.Event) -> None:
    """Wake the event if connectivity has been restored (race-condition guard)."""
    if monitor.current_state != ConnectivityState.OFFLINE:
        wake.set()


def _apply_connectivity_check(
    state: PipelineState, monitor: _ConnectivityMonitorLike
) -> PipelineState:
    """Block synchronously if offline; return updated state when online."""
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
        if not wake.is_set():
            wake.wait()
    finally:
        unsub()

    logger.bind(recovery=True).info("Connectivity restored, resuming pipeline")
    return offline_state.copy_with(last_connectivity_state=str(ConnectivityState.ONLINE))


def _setup_connectivity_monitor(
    connectivity_monitor: _ConnectivityMonitorLike | None,
) -> tuple[_ConnectivityMonitorLike, Callable[[], None] | None]:
    """Start connectivity monitor if one was not provided; return (monitor, stop_fn)."""
    if connectivity_monitor is not None:
        return connectivity_monitor, None

    real_monitor = ConnectivityMonitor()
    mon_loop = asyncio.new_event_loop()

    def _run_mon_thread() -> None:
        asyncio.set_event_loop(mon_loop)
        mon_loop.run_until_complete(real_monitor.start())
        mon_loop.run_forever()
        mon_loop.close()

    mon_thread = threading.Thread(target=_run_mon_thread, daemon=True, name="connectivity-probe")
    mon_thread.start()

    def _stop_mon() -> None:
        future = asyncio.run_coroutine_threadsafe(real_monitor.stop(), mon_loop)
        with suppress(Exception):
            future.result(timeout=2.0)
        mon_loop.call_soon_threadsafe(mon_loop.stop)
        mon_thread.join(timeout=3.0)

    return real_monitor, _stop_mon


def _setup_active_display(
    display: ParallelDisplay | None,
    is_quiet: bool,
    display_context: DisplayContext | None,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
) -> tuple[ParallelDisplay, DisplayContext, Callable[[], None]]:
    """Resolve active display and display context; return (display, ctx, stop_fn).

    The returned ``DisplayContext`` is ``active._ctx`` itself (the same
    Python object, not a snapshot copy). The width refresher mutates
    that object in place via
    :func:`_sync_live_display_context` so callers holding the
    returned reference automatically see the post-refresh width —
    there is no divergence between ``_LoopContext.display_context``
    and ``active._ctx`` after a SIGWINCH or poll-refresh tick.
    """
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
                cast("_DisplayContextOwner", active), ctx
            ),
        )

    # Return ``active._ctx`` so the caller holds the SAME Python object
    # the refresher mutates in place. This way, after a refresh tick
    # both ``_LoopContext.display_context`` and ``active._ctx`` observe
    # the same updated width (the in-place mutation preserves object
    # identity across refreshes; see ``_sync_live_display_context``).
    live_ctx: DisplayContext = active._ctx if ctx_holder is not None else resolved_ctx
    return active, live_ctx, _stop


def _emit_run_start(
    ctx: _LoopContext,
    state: PipelineState,
) -> None:
    """Emit run-start banner to display if it supports it."""
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
        _plan_present = (ctx.workspace_scope.root / ".agent" / "artifacts" / "plan.json").exists()
        _dev_agent_raw: object = getattr(ctx.config, "developer_agent", None)
        _dev_model_raw: object = getattr(ctx.config, "developer_model", None)
        verbosity_str = (
            str(ctx.effective_verbosity.value)
            if hasattr(ctx.effective_verbosity, "value")
            else str(ctx.effective_verbosity)
        )
        _orientation = RunStartOrientation(
            prompt_path=_prompt_path,
            developer_agent=cast("str | None", _dev_agent_raw),
            developer_model=cast("str | None", _dev_model_raw),
            developer_iters=ctx.config.general.developer_iters,
            parallel_max_workers=_parallel_max_workers,
            plan_present=_plan_present,
            verbosity=verbosity_str,
            workspace_root=str(ctx.workspace_scope.root),
        )
        ctx.active_display.emit_run_start(_orientation)


def _log_waiting_state(
    state: PipelineState,
    ctx: _LoopContext,
    phase_str: str,
    unavail_reason: str,
    delay_ms: int,
) -> None:
    chain = state.chain_for_phase(state.phase)
    agent_cooldowns: list[tuple[str, int, int]] = []
    if chain is not None:
        # Public surface only: the controller exposes a structured payload
        # that wraps the unavailability-store access so the run loop does
        # not reach through to the private ``_unavailability_tracker`` or
        # the tracker's ``_clock``. This is the seam for a future
        # persistent (sqlite, redis, file) store.
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


def _log_resumed_state(
    state: PipelineState,
    ctx: _LoopContext,
    phase_str: str,
    unavail_reason: str,
    delay_ms: int,
) -> None:
    chain = state.chain_for_phase(state.phase)
    agents_now_available: list[str] = []
    if chain is not None:
        # Public surface only: the controller exposes
        # ``agents_now_available`` so the run loop does not reach through
        # to the private ``_unavailability_tracker``. This is the seam
        # for a future persistent (sqlite, redis, file) store.
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


def _build_status_bar_model(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_root: Path,
) -> StatusBarModel:
    """Build a :class:`StatusBarModel` from the live pipeline state.

    Display-only: does not change workflow routing, phase semantics, or
    iteration budgets. The 1-indexed ``outer_dev_iteration`` comes from
    :func:`build_phase_entry_model_from_state` (completed+1) and is the
    same value the phase-start banner surfaces; ``inner_analysis`` comes from
    ``AnalysisLoopCounter.display_iteration``. The status bar at the bottom
    of the terminal reads from this model so operators can see the active
    working directory, phase, and applicable cycle counts without scrolling.
    """
    entry = build_phase_entry_model_from_state(
        state.phase, state, policy_bundle.pipeline
    )
    phase_style = phase_style_for_phase(state.phase, policy_bundle.pipeline)
    return StatusBarModel(
        workspace_root=str(workspace_root),
        phase_label=entry.human_label(),
        phase_style=phase_style,
        outer_dev_iteration=entry.outer_dev_iteration,
        outer_dev_cap=entry.outer_dev_cap,
        inner_analysis=entry.inner_analysis,
        inner_analysis_cap=entry.inner_analysis_cap,
    )


def _push_status_bar_if_changed(
    active_display: object,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_root: Path,
    last_sig: tuple[str, int | None, int | None] | None,
) -> tuple[str, int | None, int | None] | None:
    """Push a fresh :class:`StatusBarModel` only when the (phase, cycle) signature changes.

    Returns the new signature so the caller's closure-local ``last_status_sig``
    stays current. Defensive: any failure is swallowed. Pass ``last_sig=None``
    for an unconditional initial push.
    """
    with suppress(Exception):
        model = _build_status_bar_model(state, policy_bundle, workspace_root)
        signature = (state.phase, model.outer_dev_iteration, model.inner_analysis)
        if signature != last_sig and hasattr(active_display, "update_status_bar"):
            active_display.update_status_bar(model)
            return signature
    return last_sig


def _run_inner_loop(
    state: PipelineState,
    ctx: _LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, str, int | None]:
    """Run main pipeline while loop; return (state, prev_phase, exit_code_if_interrupted)."""
    # State holder so the providers captured by run_pipeline_step can
    # read the LIVE PipelineState / ConnectivityMonitor on every agent
    # invocation. The list is rebound every loop iteration so the
    # is_waiting_state_provider returns the current state's value, not
    # a snapshot from when the loop was entered.
    state_holder: list[PipelineState] = [state]

    def _live_connectivity() -> str | None:
        return str(ctx.connectivity_monitor.current_state.value)

    def _live_is_waiting() -> bool:
        return bool(state_holder[0].is_waiting_state)

    last_status_sig: tuple[str, int | None, int | None] | None = None
    while state.phase != ctx.policy_bundle.pipeline.terminal_phase:
        state = _apply_connectivity_check(state, ctx.connectivity_monitor)
        state_holder[0] = state
        # Per-iteration pipeline_deps with the live providers so the
        # watchdog inside the agent invocation can consult the
        # classifier on every evaluate() call.
        iter_pipeline_deps: PipelineDeps | None
        if ctx.pipeline_deps is not None:
            iter_pipeline_deps = dataclasses.replace(
                ctx.pipeline_deps,
                connectivity_state_provider=_live_connectivity,
                is_waiting_state_provider=_live_is_waiting,
            )
        else:
            iter_pipeline_deps = ctx.pipeline_deps
        runner_step = cast("_RunPipelineStepFn", _runner_module.run_pipeline_step)
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
        )
        if isinstance(step_result, int):
            return state, prev_phase, step_result
        state = step_result
        # Push a fresh status-bar model when the (phase, cycle) signature
        # changes. The dedupe var is a closure-local scalar tuple (NOT
        # module-level state, NOT a self.X slot) so this stays compliant
        # with the resource-accumulator audit.
        last_status_sig = _push_status_bar_if_changed(
            ctx.active_display,
            state,
            ctx.policy_bundle,
            ctx.workspace_scope.root,
            last_status_sig,
        )
        if ctx.snapshot_registry is not None:
            from ralph.pro_support.state_query import (
                build_pipeline_state_snapshot,
            )

            ctx.snapshot_registry.publish(
                build_pipeline_state_snapshot(state, ctx.workspace_scope.root)
            )
        delay_ms = state.last_retry_delay_ms
        if isinstance(delay_ms, int) and delay_ms > 0:
            # Structured wait-state detection: the controller sets
            # ``state.is_waiting_state`` to True when it enters the
            # all-agents-unavailable wait branch. The previous text
            # parser on ``state.last_error`` was brittle (the controller
            # and the run loop could disagree about the exact string) so
            # it was replaced with this boolean. The ``last_error`` text
            # remains as operator-readable context only.
            is_all_unavailable = state.is_waiting_state
            if not is_all_unavailable:
                ctx.last_waiting_state_phase = None
            try:
                current_phase_str = str(state.phase)
                unavail_reason = state.last_unavailability_reason or "unknown"
                if is_all_unavailable and ctx.last_waiting_state_phase != current_phase_str:
                    ctx.last_waiting_state_phase = current_phase_str
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

                state = state.copy_with(last_retry_delay_ms=0)
                if is_all_unavailable:
                    logger.bind(recovery=True).debug(
                        "Starting cooldown sleep for {delay_seconds:.3f} "
                        "seconds in phase '{phase}'.",
                        delay_seconds=delay_ms / 1000.0,
                        phase=current_phase_str,
                    )

                ctx.sleep(delay_ms / 1000.0)

                if is_all_unavailable:
                    emit_activity_line(
                        ctx.active_display,
                        None,
                        status_text(
                            "RESUMED",
                            "cooldown expired; retrying",
                            "green",
                        ),
                    )
                    _log_resumed_state(state, ctx, current_phase_str, unavail_reason, delay_ms)
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
                state = state.copy_with(last_retry_delay_ms=1000)
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
    """Emit run-end summary after the pipeline loop finishes."""
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


def _build_recovery_controller(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    config: UnifiedConfig,
) -> tuple[RecoveryController, int]:
    """Build recovery controller from policy bundle; return (controller, cycle_cap)."""
    _cycle_cap: int = 200
    _raw_cycle_cap: object = getattr(state, "recovery_cycle_cap", 200)
    if isinstance(_raw_cycle_cap, int) and _raw_cycle_cap >= 1:
        _cycle_cap = _raw_cycle_cap

    raw_technical_retry_cap: object = getattr(
        config.general,
        "max_same_agent_retries",
        10,
    )
    technical_retry_cap = (
        raw_technical_retry_cap
        if isinstance(raw_technical_retry_cap, int) and raw_technical_retry_cap >= 0
        else 10
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=_cycle_cap,
            policy_bundle=policy_bundle,
            budget_registry=_seed_budget_registry(policy_bundle),
            technical_retry_cap=technical_retry_cap,
        )
    )
    return controller, _cycle_cap


def _subscribe_recovery_logger(controller: RecoveryController) -> Callable[[], None]:
    """Subscribe a recovery event logger to controller; return the unsubscribe callable."""

    def _log_recovery_event(evt: object) -> None:
        if isinstance(evt, _FailureEvent):
            remaining: int | None = None
            if evt.agent:
                snap = controller.snapshot()
                budgets = snap.get("budgets")
                if isinstance(budgets, dict):
                    key = f"{evt.phase}:{evt.agent}"
                    budget_info = budgets.get(key)
                    if isinstance(budget_info, dict):
                        remaining = budget_info.get("remaining")
            logger.bind(recovery=True).info(
                "FAILURE phase={} agent={} category={} counted={}"
                " chain_cap={} cycle={} delay_ms={} remaining={}"
                " unavailability_reason={}",
                evt.phase,
                evt.agent,
                evt.category,
                evt.counted_against_budget,
                evt.chain_capacity_remaining,
                evt.recovery_cycle,
                evt.retry_delay_ms,
                remaining,
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
    """Subscribe a cadenced recovery/stopping display emitter to the bus.

    Returns the unsubscribe callable. The subscriber is read-only and
    defensive:

    * ``isinstance`` checks reuse the module-imported ``_FailureEvent``
      and ``_FalloverEvent`` aliases (do NOT add a new event-class import).
    * The callback body is fully wrapped in ``try/except`` so a display
      rendering exception (a buggy renderer, a closed Rich console, etc.)
      is swallowed to a debug log and never escapes the
      ``FailureEventBus.publish`` dispatch. This mirrors the existing
      ``_log_recovery_event`` precedent at ``_subscribe_recovery_logger``.
    * The cadence gate reads time via the injected ``now`` callable so
      tests drive it with ``FakeClock.monotonic``. Production passes
      ``time.monotonic``. Hardcoded ``time.monotonic()`` inside the
      callback would make the AC-03 cadence test non-deterministic and
      is therefore forbidden.
    * The cadence map is a closure-local dict keyed by event-kind tag,
      not a module-level or ``self.X`` accumulator, so it falls outside
      the ``audit_resource_lifecycle`` 4th-contract scope.

    The caller (``run()``) MUST register this subscriber AFTER
    ``active_display`` is built (so a non-``None`` display is available)
    and MUST call the returned unsubscribe in ``_cleanup_pipeline`` so a
    long-running daemon mode does not accumulate listeners across runs.

    Args:
        controller: ``RecoveryController`` whose ``event_bus`` is
            subscribed. Must expose a public ``event_bus`` property.
        display: ``ParallelDisplay`` to route through ``emit_activity_line``.
        interval_seconds: Cadence window per event-kind tag in seconds.
        now: Callable returning a monotonic float. Production passes
            ``time.monotonic``; tests inject ``FakeClock.monotonic``.
    """
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
                        f"falling over from {evt.from_agent} to "
                        f"{evt.to_agent} ({evt.reason})",
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
                        value = (
                            f"chain exhausted ({evt.category}: "
                            f"{evt.reason or 'no detail'})"
                        )
                    style = "red"
                    tag = "terminal"
                elif evt.watchdog_reason is not None:
                    label = "RECOVERING"
                    value = f"agent stalled: {evt.watchdog_reason}; resuming"
                    style = "yellow"
                    tag = "watchdog_recoverable"
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
    """Resolve which subscriber receives pipeline state change notifications."""
    effective = dashboard_subscriber or pipeline_subscriber
    if effective is None and hasattr(active_display, "subscriber"):
        effective = cast(
            "_PipelineSubscriberProtocol | None",
            getattr(active_display, "subscriber", None),
        )
    return effective


def _handle_keyboard_interrupt(
    state: PipelineState,
    loop_ctx: _LoopContext,
) -> int:
    """Handle KeyboardInterrupt during pipeline execution; return exit_code 130."""
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


def _cleanup_pipeline(
    loop_ctx: _LoopContext,
    unsubscribe_bus: Callable[[], None],
    unsubscribe_display: Callable[[], None],
    display_stop: Callable[[], None],
    state: PipelineState,
) -> None:
    """Run all cleanup steps regardless of how the pipeline exited.

    The session-wide ``process_teardown`` (defaulting to
    ``get_process_manager().shutdown_all``) runs LAST so every
    spawned child is reaped on every exit (normal, error, SIGINT,
    SIGTERM). It is wrapped in ``suppress(Exception)`` so a
    refusing-to-die process cannot break the suite.
    """
    with suppress(Exception):
        unsubscribe_bus()
    with suppress(Exception):
        unsubscribe_display()
    with suppress(Exception):
        display_stop()
    if loop_ctx.monitor_stop is not None:
        with suppress(Exception):
            loop_ctx.monitor_stop()
    if loop_ctx.pro_watcher is not None:
        with suppress(Exception):
            loop_ctx.pro_watcher.stop()
    if loop_ctx.heartbeat_client is not None:
        with suppress(Exception):
            loop_ctx.heartbeat_client.stop()
    emit_final_summary(
        state,
        loop_ctx.workspace_scope.root,
        subscriber=cast("PipelineSubscriber | None", loop_ctx.effective_pipeline_subscriber),
        display=loop_ctx.active_display,
        display_context=loop_ctx.display_context,
    )
    with suppress(Exception):
        _runner_module.clear_cycle_baseline(loop_ctx.workspace_scope.root)
    # Session-wide process teardown: run last so non-phase-labeled
    # children (invoke:/agent:) are reaped on every exit path. The
    # teardown is wired through ``loop_ctx.process_teardown`` so
    # tests can inject a recording callable. When unset, fall back
    # to ``get_process_manager().shutdown_all`` so production
    # behavior is unchanged.
    teardown = loop_ctx.process_teardown
    if teardown is None:
        def teardown() -> None:
            get_process_manager().shutdown_all(grace_period_s=0.5)
    with suppress(Exception):
        teardown()


def _execute_with_cleanup(
    initial_state: PipelineState,
    loop_ctx: _LoopContext,
    prev_phase: str,
    unsubscribe_bus: Callable[[], None],
    unsubscribe_display: Callable[[], None],
    display_stop: Callable[[], None],
) -> int:
    """Run the display block and guarantee cleanup; return exit_code."""
    exit_code = 0
    state = initial_state
    try:
        with loop_ctx.active_display:
            _emit_run_start(loop_ctx, state)
            if hasattr(loop_ctx.active_display, "begin_phase"):
                with suppress(Exception):
                    cast("_PhaseAwareDisplay", loop_ctx.active_display).begin_phase(state.phase)
            # Seed the persistent bottom Status Bar with the active directory +
            # phase + iteration context for the initial phase. Defensive push
            # (matches _emit_run_start / emit_activity_line precedent). Pass
            # last_sig=None so the first push is unconditional.
            _push_status_bar_if_changed(
                loop_ctx.active_display,
                state,
                loop_ctx.policy_bundle,
                loop_ctx.workspace_scope.root,
                last_sig=None,
            )
            _runner_module.notify_pipeline_subscriber(loop_ctx.effective_pipeline_subscriber, state)
            try:
                state, prev_phase, early_exit = _run_inner_loop(state, loop_ctx, prev_phase)
                if early_exit is not None:
                    return early_exit
            except KeyboardInterrupt:
                return _handle_keyboard_interrupt(state, loop_ctx)
            if state.phase == loop_ctx.policy_bundle.pipeline.terminal_phase:
                loop_ctx.active_display.emit(
                    "run", "[green]Pipeline completed successfully.[/green]"
                )
            else:
                emit_activity_line(
                    loop_ctx.active_display,
                    None,
                    status_text("Pipeline failed", state.last_error or "Unknown error", "red"),
                )
                exit_code = 1
            _emit_post_loop_result(
                state, loop_ctx.active_display, loop_ctx.is_quiet, exit_code, loop_ctx.policy_bundle
            )
    finally:
        _cleanup_pipeline(loop_ctx, unsubscribe_bus, unsubscribe_display, display_stop, state)
    return exit_code


def run(  # noqa: PLR0912, PLR0915 - DI-seam run loop with many factory branches
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    display: ParallelDisplay | None = None,
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
    """Execute the pipeline event loop.

    Args:
        config: Unified configuration for the pipeline.
        initial_state: Optional initial state (for resume from checkpoint).
        display: Optional pre-built display. When omitted, a ParallelDisplay
            is constructed by default unless ``verbosity`` is QUIET.
        pipeline_subscriber: Optional subscriber that will receive notify(state)
            calls after each reduce.
        verbosity: Optional explicit verbosity. Defaults to the configured
            value in ``config.general.verbosity`` (mapped from int rank).
        pro_hooks: Optional ``ProPipelineHooks`` carrying Pro overrides.
            Ignored when ``pipeline_deps`` is provided; prefer passing
            ``pro_hooks`` to :func:`build_default_pipeline_deps` instead.
        pipeline_deps: Optional ``PipelineDeps`` carrying injected
            collaborators. This is the single authoritative injection
            surface for the run loop. When provided, its values take
            precedence over ``pro_hooks`` and production defaults.
        policy_bundle_factory: (DEPRECATED) Use ``pipeline_deps``.
        registry_factory: (DEPRECATED) Use ``pipeline_deps``.
        state_factory: (DEPRECATED) Use ``pipeline_deps``.
        recovery_controller_factory: (DEPRECATED) Use ``pipeline_deps``.
        marker_watcher_factory: (DEPRECATED) Use ``pipeline_deps``.
        snapshot_registry: (DEPRECATED) Use ``pipeline_deps``.
        _recovery_sleep: (DEPRECATED) Use ``pipeline_deps.recovery_sleep``
            (or pass ``recovery_sleep`` to :func:`build_default_pipeline_deps`).

    Migration Notes:
        ``run()`` previously accepted individual factory kwargs such as
        ``policy_bundle_factory``, ``registry_factory``, etc. These are now
        deprecated. Construct a :class:`PipelineDeps` bundle via
        :func:`build_default_pipeline_deps` (optionally passing
        ``pro_hooks`` or ``recovery_sleep`` to it) and pass only
        ``pipeline_deps``. Passing any deprecated factory kwarg alongside
        ``pipeline_deps`` raises ``ValueError``. Callers using only the old
        factory kwargs (without ``pipeline_deps``) continue to work for
        backward compatibility.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    workspace_scope = _runner_module.resolve_workspace_scope()
    _runner_module.write_start_commit_if_absent(workspace_scope.root)
    if _runner_module.validate_custom_mcp_servers(workspace_scope.root) != 0:
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

    # Resolve collaborators with precedence: pipeline_deps > pro_hooks > defaults.
    # When pipeline_deps is provided it is the authoritative composed bundle;
    # deprecated factory kwargs are rejected above, so the two injection paths
    # are never mixed.
    if pipeline_deps is not None:
        if pipeline_deps.policy_bundle is not None:
            policy_bundle = pipeline_deps.policy_bundle
        elif pipeline_deps.policy_bundle_factory is not None:
            policy_bundle = pipeline_deps.policy_bundle_factory(workspace_scope, config)
        else:
            policy_bundle = _runner_module.load_policy_bundle_for_run(workspace_scope, config)
    elif pro_hooks is not None and pro_hooks.policy_bundle_override is not None:
        policy_bundle = pro_hooks.policy_bundle_override
    elif pro_hooks is not None and pro_hooks.policy_bundle_factory is not None:
        policy_bundle = pro_hooks.policy_bundle_factory(workspace_scope, config)
    elif policy_bundle_factory is not None:
        policy_bundle = policy_bundle_factory(workspace_scope, config)
    else:
        policy_bundle = _runner_module.load_policy_bundle_for_run(workspace_scope, config)
    _runner_module.register_role_handlers(policy_bundle.pipeline)

    registry: _RegistryLike
    if pipeline_deps is not None:
        if pipeline_deps.registry_factory is not None:
            registry = cast("_RegistryLike", pipeline_deps.registry_factory(config))
        else:
            registry = _runner_module.AgentRegistry.from_config(config)
    elif pro_hooks is not None and pro_hooks.registry_factory is not None:
        registry = cast("_RegistryLike", pro_hooks.registry_factory(config))
    elif registry_factory is not None:
        registry = registry_factory(config)
    else:
        registry = _runner_module.AgentRegistry.from_config(config)

    if initial_state is not None:
        state = initial_state
    elif pipeline_deps is not None:
        if pipeline_deps.state_factory is not None:
            state = pipeline_deps.state_factory(
                config,
                policy_bundle.agents,
                policy_bundle.pipeline,
                counter_overrides,
            )
        else:
            state = _runner_module.create_initial_state(
                config,
                agents_policy=policy_bundle.agents,
                pipeline_policy=policy_bundle.pipeline,
                counter_overrides=counter_overrides,
            )
    elif pro_hooks is not None and pro_hooks.state_factory is not None:
        state = pro_hooks.state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    elif state_factory is not None:
        state = state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    else:
        state = _runner_module.create_initial_state(
            config,
            agents_policy=policy_bundle.agents,
            pipeline_policy=policy_bundle.pipeline,
            counter_overrides=counter_overrides,
        )

    effective_verbosity = normalize_verbosity(
        verbosity if verbosity is not None else config.general.verbosity
    )
    is_quiet = verbosity_rank(effective_verbosity) <= VERBOSITY_RANK[Verbosity.QUIET]
    if pipeline_deps is not None and pipeline_deps.recovery_sleep is not None:
        _sleep = pipeline_deps.recovery_sleep
    elif pro_hooks is not None and pro_hooks.recovery_sleep is not None:
        _sleep = pro_hooks.recovery_sleep
    elif _recovery_sleep is not None:
        _sleep = _recovery_sleep
    else:
        _sleep = time.sleep
    connectivity_monitor, _monitor_stop = _setup_connectivity_monitor(connectivity_monitor)

    if pipeline_deps is not None:
        if pipeline_deps.recovery_controller_factory is not None:
            _controller, _ = pipeline_deps.recovery_controller_factory(state, policy_bundle, config)
        else:
            _controller, _ = _build_recovery_controller(state, policy_bundle, config)
    elif pro_hooks is not None and pro_hooks.recovery_controller_factory is not None:
        _controller, _ = pro_hooks.recovery_controller_factory(state, policy_bundle, config)
    elif recovery_controller_factory is not None:
        _controller, _ = recovery_controller_factory(state, policy_bundle, config)
    else:
        _controller, _ = _build_recovery_controller(state, policy_bundle, config)
    _unsubscribe_bus = _subscribe_recovery_logger(_controller)
    logger.info("Starting pipeline: phase={}, budget_caps={}", state.phase, state.budget_caps)
    if pipeline_subscriber is None:
        pipeline_subscriber = dashboard_subscriber
    active_display, display_context, _display_stop = _setup_active_display(
        display, is_quiet, display_context, workspace_scope, policy_bundle
    )
    effective_pipeline_subscriber = _resolve_effective_subscriber(
        dashboard_subscriber, pipeline_subscriber, active_display
    )

    _effective_marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None = None
    if pipeline_deps is not None:
        _effective_marker_watcher_factory = pipeline_deps.marker_watcher_factory
    elif pro_hooks is not None and pro_hooks.marker_watcher_factory is not None:
        _effective_marker_watcher_factory = pro_hooks.marker_watcher_factory
    else:
        _effective_marker_watcher_factory = marker_watcher_factory

    _effective_snapshot_registry: SnapshotRegistry | None = None
    if pipeline_deps is not None:
        _effective_snapshot_registry = pipeline_deps.snapshot_registry
    elif pro_hooks is not None and pro_hooks.snapshot_registry is not None:
        _effective_snapshot_registry = pro_hooks.snapshot_registry
    else:
        _effective_snapshot_registry = snapshot_registry
    _pro_watcher, _heartbeat_client = _start_pro_marker_watcher(
        workspace_scope.root,
        watcher_factory=_effective_marker_watcher_factory,
    )
    # The legacy public helper ``_start_pro_heartbeat_if_active`` is
    # monkey-patched in many tests to inject a recording heartbeat.
    # If the user (or a test) replaced it, honour that override here
    # so the run loop's heartbeat_client matches what the test
    # observed when the monkey-patch was applied. The watcher above
    # is kept running so late-marker adoption still works when no
    # override is supplied.
    _module_legacy_obj: object
    _self_module = sys.modules[__name__]
    _self_dict: dict[str, object] = _self_module.__dict__
    try:
        _module_legacy_obj = _self_dict["_start_pro_heartbeat_if_active"]
    except KeyError:
        _module_legacy_obj = _start_pro_marker_watcher
    if _module_legacy_obj is not _start_pro_marker_watcher and callable(_module_legacy_obj):
        _module_legacy = cast(
            "Callable[[Path], ProHeartbeatClient | None]",
            _module_legacy_obj,
        )
        _patched_heartbeat: ProHeartbeatClient | None = _module_legacy(workspace_scope.root)
        if _patched_heartbeat is not None:
            _heartbeat_client = _patched_heartbeat
    loop_ctx = _LoopContext(
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
        snapshot_registry=_effective_snapshot_registry,
        pipeline_deps=pipeline_deps,
        process_teardown=pipeline_deps.process_teardown if pipeline_deps is not None else None,
    )
    _recovery_display_interval: float
    _recovery_display_interval_raw: object = getattr(
        config.general, "agent_waiting_status_interval_seconds", WAITING_STATUS_INTERVAL_SECONDS
    )
    if (
        isinstance(_recovery_display_interval_raw, (int, float))
        and not isinstance(_recovery_display_interval_raw, bool)
        and float(_recovery_display_interval_raw) > 0.0
    ):
        _recovery_display_interval = float(_recovery_display_interval_raw)
    else:
        _recovery_display_interval = WAITING_STATUS_INTERVAL_SECONDS
    _unsubscribe_display = _subscribe_recovery_display(
        _controller,
        active_display,
        _recovery_display_interval,
        now=time.monotonic,
    )
    return _execute_with_cleanup(
        state,
        loop_ctx,
        state.phase,
        _unsubscribe_bus,
        _unsubscribe_display,
        _display_stop,
    )


def _start_pro_marker_watcher(
    workspace_root: Path,
    *,
    watcher_factory: Callable[[Path], ProMarkerWatcher] | None = None,
) -> tuple[ProMarkerWatcher | None, ProHeartbeatClient | None]:
    """Construct and start a Pro marker watcher (with its embedded heartbeat).

    The watcher polls for the marker and adopts it on first
    appearance. The heartbeat client (if any) is the one created
    by the watcher's heartbeat_factory on adoption. Both are
    returned so the run loop can attach them to _LoopContext.

    When the marker is missing at engine start time, the watcher
    is started in daemon mode and will adopt the marker later.
    The heartbeat client is None until adoption; the
    cleanup path stops the watcher first (so the watcher's poll
    loop cannot race the heartbeat), then the heartbeat client
    (so its daemon drain completes).
    """
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
    """Thin one-line wrapper that preserves the legacy public API.

    Existing tests and call sites (e.g.
    test_run_loop_pro_integration.py,
    test_pro_support_contract.py) monkey-patch this name to
    substitute a recording heartbeat. Returning None when the
    marker is missing or Pro mode is inactive matches the prior
    contract.

    New code should use :func:`_start_pro_marker_watcher` directly
    so the watcher is also wired up.
    """
    _watcher, client = _start_pro_marker_watcher(workspace_root)
    _ = _watcher
    return client
