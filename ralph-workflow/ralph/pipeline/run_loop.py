"""Pipeline event loop: the run() entry point and connectivity helpers."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

import ralph.pipeline.runner as _runner_module
from ralph.config.enums import Verbosity
from ralph.display.plain_renderer import RunStartOrientation
from ralph.pipeline.legacy_console_display import (
    LegacyConsoleDisplay,
    build_default_display,
    emit_display_line,
    status_text,
)
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, normalize_verbosity, verbosity_rank
from ralph.pipeline.phase_transition import emit_final_summary
from ralph.recovery.budget import seed_budget_registry as _seed_budget_registry
from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityMonitor, ConnectivityState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent as _FailureEvent
from ralph.recovery.events import FalloverEvent as _FalloverEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.agent_config import AgentConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class _PipelineSubscriberProtocol(Protocol):
    def notify(self, state: PipelineState) -> None: ...


class _RegistryLike(Protocol):
    def get(self, name: str) -> AgentConfig | None: ...


class _ConnectivityMonitorLike(Protocol):
    @property
    def current_state(self) -> ConnectivityState: ...

    def add_listener(self, cb: Callable[[object], None]) -> Callable[[], None]: ...


class _DisplayContextOwner(Protocol):
    _ctx: DisplayContext


class _DisplayWithPlainRenderer(_DisplayContextOwner, Protocol):
    _plain_renderer: _DisplayContextOwner


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
    active_display: ParallelDisplay | LegacyConsoleDisplay
    display_context: DisplayContext
    effective_verbosity: Verbosity
    registry: _RegistryLike
    effective_pipeline_subscriber: _PipelineSubscriberProtocol | None
    controller: RecoveryController
    monitor_stop: Callable[[], None] | None
    connectivity_monitor: _ConnectivityMonitorLike
    sleep: Callable[[float], None]
    is_quiet: bool


def _sync_live_display_context(display: _DisplayContextOwner, ctx: DisplayContext) -> None:
    """Keep the runner's active display and nested renderer on the same context."""
    display._ctx = ctx
    if hasattr(display, "_plain_renderer"):
        cast("_DisplayWithPlainRenderer", display)._plain_renderer._ctx = ctx


def _signal_if_now_online(monitor: _ConnectivityMonitorLike, wake: threading.Event) -> None:
    """Wake the event if connectivity has been restored (race-condition guard)."""
    if monitor.current_state != ConnectivityState.OFFLINE:
        wake.set()


def _apply_connectivity_check(
    state: PipelineState, monitor: _ConnectivityMonitorLike
) -> PipelineState:
    """Block synchronously if offline; return updated state when online."""
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
) -> tuple[ParallelDisplay | LegacyConsoleDisplay, DisplayContext, Callable[[], None]]:
    """Resolve active display and display context; return (display, ctx, stop_fn)."""
    if display is not None:
        resolved_ctx = display._ctx
    elif display_context is not None:
        resolved_ctx = display_context
    else:
        resolved_ctx = _runner_module.make_display_context()

    if display is not None:
        active: ParallelDisplay | LegacyConsoleDisplay = display
    elif is_quiet:
        active = LegacyConsoleDisplay(resolved_ctx)
    else:
        active = build_default_display(
            workspace_scope.root,
            resolved_ctx,
            policy_bundle,
        )

    def _stop() -> None:
        pass

    if isinstance(active, LegacyConsoleDisplay) or hasattr(active, "_ctx"):
        ctx_holder = [active._ctx]
        _stop = _runner_module.install_width_refresher(
            ctx_holder,
            on_refresh=lambda ctx: _sync_live_display_context(
                cast("_DisplayContextOwner", active), ctx
            ),
        )

    return active, resolved_ctx, _stop


def _emit_run_start(
    ctx: _LoopContext,
    state: PipelineState,
) -> None:
    """Emit run-start banner to display if it supports it."""
    if not hasattr(ctx.active_display, "emit_run_start"):
        return
    with suppress(Exception):
        _prompt_path_raw: object = getattr(
            ctx.effective_pipeline_subscriber, "_prompt_path", None
        )
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
        _plan_present = (
            ctx.workspace_scope.root / ".agent" / "artifacts" / "plan.json"
        ).exists()
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
        cast("ParallelDisplay", ctx.active_display).emit_run_start(_orientation)


def _run_inner_loop(
    state: PipelineState,
    ctx: _LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, str, int | None]:
    """Run main pipeline while loop; return (state, prev_phase, exit_code_if_interrupted)."""
    while state.phase != ctx.policy_bundle.pipeline.terminal_phase:
        state = _apply_connectivity_check(state, ctx.connectivity_monitor)
        step_result = _runner_module.run_pipeline_step(
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
            _monitor_stop_cb=ctx.monitor_stop,
        )
        if isinstance(step_result, int):
            return state, prev_phase, step_result
        state = step_result
        delay_ms = state.last_retry_delay_ms
        if isinstance(delay_ms, int) and delay_ms > 0:
            state = state.copy_with(last_retry_delay_ms=0)
            ctx.sleep(delay_ms / 1000.0)
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
    active_display: ParallelDisplay | LegacyConsoleDisplay,
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


def _build_recovery_controller(
    state: PipelineState,
    policy_bundle: PolicyBundle,
) -> tuple[RecoveryController, int]:
    """Build recovery controller from policy bundle; return (controller, cycle_cap)."""
    _cycle_cap: int = 200
    _raw_cycle_cap: object = getattr(state, "recovery_cycle_cap", 200)
    if isinstance(_raw_cycle_cap, int) and _raw_cycle_cap >= 1:
        _cycle_cap = _raw_cycle_cap

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=_cycle_cap,
            policy_bundle=policy_bundle,
            budget_registry=_seed_budget_registry(policy_bundle),
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
                " chain_cap={} cycle={} delay_ms={} remaining={}",
                evt.phase,
                evt.agent,
                evt.category,
                evt.counted_against_budget,
                evt.chain_capacity_remaining,
                evt.recovery_cycle,
                evt.retry_delay_ms,
                remaining,
            )
        elif isinstance(evt, _FalloverEvent):
            logger.bind(recovery=True).info(
                "FALLOVER phase={} from={} to={} reason={}",
                evt.phase,
                evt.from_agent,
                evt.to_agent,
                evt.reason,
            )

    return controller.event_bus.subscribe(_log_recovery_event)


def _resolve_effective_subscriber(
    dashboard_subscriber: _PipelineSubscriberProtocol | None,
    pipeline_subscriber: _PipelineSubscriberProtocol | None,
    active_display: ParallelDisplay | LegacyConsoleDisplay,
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
    emit_display_line(
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
    )
    return 130


def _cleanup_pipeline(
    loop_ctx: _LoopContext,
    unsubscribe_bus: Callable[[], None],
    display_stop: Callable[[], None],
    state: PipelineState,
) -> None:
    """Run all cleanup steps regardless of how the pipeline exited."""
    with suppress(Exception):
        unsubscribe_bus()
    with suppress(Exception):
        display_stop()
    if loop_ctx.monitor_stop is not None:
        with suppress(Exception):
            loop_ctx.monitor_stop()
    emit_final_summary(
        state,
        loop_ctx.workspace_scope.root,
        subscriber=cast("PipelineSubscriber | None", loop_ctx.effective_pipeline_subscriber),
        display=loop_ctx.active_display,
        display_context=loop_ctx.display_context,
    )
    with suppress(Exception):
        _runner_module.clear_cycle_baseline(loop_ctx.workspace_scope.root)


def _execute_with_cleanup(
    initial_state: PipelineState,
    loop_ctx: _LoopContext,
    prev_phase: str,
    unsubscribe_bus: Callable[[], None],
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
                emit_display_line(
                    loop_ctx.active_display,
                    None,
                    status_text("Pipeline failed", state.last_error or "Unknown error", "red"),
                )
                exit_code = 1
            _emit_post_loop_result(
                state, loop_ctx.active_display, loop_ctx.is_quiet, exit_code, loop_ctx.policy_bundle
            )
    finally:
        _cleanup_pipeline(loop_ctx, unsubscribe_bus, display_stop, state)
    return exit_code


def run(
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
    _recovery_sleep: Callable[[float], None] | None = None,
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

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    workspace_scope = _runner_module.resolve_workspace_scope()
    _runner_module.write_start_commit_if_absent(workspace_scope.root)
    if _runner_module.validate_custom_mcp_servers(workspace_scope.root) != 0:
        return 1
    policy_bundle = _runner_module.load_policy_bundle_for_run(workspace_scope, config)
    _runner_module.register_role_handlers(policy_bundle.pipeline)
    registry = _runner_module.AgentRegistry.from_config(config)
    state = initial_state or _runner_module.create_initial_state(
        config,
        agents_policy=policy_bundle.agents,
        pipeline_policy=policy_bundle.pipeline,
        counter_overrides=counter_overrides,
    )
    effective_verbosity = normalize_verbosity(
        verbosity if verbosity is not None else config.general.verbosity
    )
    is_quiet = verbosity_rank(effective_verbosity) <= VERBOSITY_RANK[Verbosity.QUIET]
    _sleep = _recovery_sleep or time.sleep
    connectivity_monitor, _monitor_stop = _setup_connectivity_monitor(connectivity_monitor)
    _controller, _ = _build_recovery_controller(state, policy_bundle)
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
        monitor_stop=_monitor_stop,
        connectivity_monitor=connectivity_monitor,
        sleep=_sleep,
        is_quiet=is_quiet,
    )
    return _execute_with_cleanup(state, loop_ctx, state.phase, _unsubscribe_bus, _display_stop)
