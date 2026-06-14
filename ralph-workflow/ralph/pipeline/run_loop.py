"""Pipeline event loop: the run() entry point and connectivity helpers."""

from __future__ import annotations

import asyncio
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
    status_text,
)
from ralph.onboarding import RUN_COMPLETION_STAR_CTA
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, normalize_verbosity, verbosity_rank
from ralph.pipeline.phase_transition import emit_final_summary
from ralph.recovery.budget import seed_budget_registry as _seed_budget_registry
from ralph.recovery.connectivity import ConnectivityEvent, ConnectivityMonitor, ConnectivityState
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent as _FailureEvent
from ralph.recovery.events import FalloverEvent as _FalloverEvent

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


def _sync_live_display_context(display: _DisplayContextOwner, ctx: DisplayContext) -> None:
    """Keep the runner's active display on the same context as the runner."""
    display._ctx = ctx


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
) -> tuple[ParallelDisplay, DisplayContext, Callable[[], None]]:
    """Resolve active display and display context; return (display, ctx, stop_fn)."""
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

    if hasattr(active, "_ctx"):
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


def _run_inner_loop(
    state: PipelineState,
    ctx: _LoopContext,
    prev_phase: str,
) -> tuple[PipelineState, str, int | None]:
    """Run main pipeline while loop; return (state, prev_phase, exit_code_if_interrupted)."""
    while state.phase != ctx.policy_bundle.pipeline.terminal_phase:
        state = _apply_connectivity_check(state, ctx.connectivity_monitor)
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
            pipeline_deps=ctx.pipeline_deps,
        )
        if isinstance(step_result, int):
            return state, prev_phase, step_result
        state = step_result
        if ctx.snapshot_registry is not None:
            from ralph.pro_support.state_query import (  # noqa: PLC0415
                build_pipeline_state_snapshot,
            )

            ctx.snapshot_registry.publish(
                build_pipeline_state_snapshot(state, ctx.workspace_scope.root)
            )
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
        _cleanup_pipeline(loop_ctx, unsubscribe_bus, display_stop, state)
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
        pro_hooks: Optional ``ProPipelineHooks`` carrying 5 factory callables
            plus 1 policy_bundle_override and 1 snapshot_registry. When
            supplied, individual ``policy_bundle_factory`` /
            ``registry_factory`` / etc. kwargs are pulled from this dataclass
            (which keeps them in a single, typed bundle).
        policy_bundle_factory: Optional callable that replaces
            ``_runner_module.load_policy_bundle_for_run``; ignored when
            ``pro_hooks.policy_bundle_override`` is set.
        registry_factory: Optional callable that replaces
            ``_runner_module.AgentRegistry.from_config``.
        state_factory: Optional callable that replaces
            ``_runner_module.create_initial_state``.
        recovery_controller_factory: Optional callable that replaces
            ``_build_recovery_controller``.
        marker_watcher_factory: Optional callable that constructs a
            ``ProMarkerWatcher``; default constructs one with production
            defaults.
        snapshot_registry: Optional ``SnapshotRegistry``; when set, the inner
            loop publishes a ``PipelineStateSnapshot`` to it on each reduce.
        pipeline_deps: Optional ``PipelineDeps`` carrying injected
            collaborators. When supplied, it takes precedence over the
            legacy ``pro_hooks`` and individual factory kwargs.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    workspace_scope = _runner_module.resolve_workspace_scope()
    _runner_module.write_start_commit_if_absent(workspace_scope.root)
    if _runner_module.validate_custom_mcp_servers(workspace_scope.root) != 0:
        return 1
    if pipeline_deps is not None and display_context is None:
        display_context = pipeline_deps.display_context
    if pro_hooks is not None:
        if policy_bundle_factory is None and pipeline_deps is None:
            policy_bundle_factory = pro_hooks.policy_bundle_factory
        if registry_factory is None and pipeline_deps is None:
            registry_factory = cast(
                "Callable[[UnifiedConfig], _RegistryLike] | None",
                pro_hooks.registry_factory,
            )
        if state_factory is None and pipeline_deps is None:
            state_factory = pro_hooks.state_factory
        if recovery_controller_factory is None and pipeline_deps is None:
            recovery_controller_factory = pro_hooks.recovery_controller_factory
        if marker_watcher_factory is None and pipeline_deps is None:
            marker_watcher_factory = pro_hooks.marker_watcher_factory
        if snapshot_registry is None and pipeline_deps is None:
            snapshot_registry = pro_hooks.snapshot_registry
        if pro_hooks.policy_bundle_override is not None and pipeline_deps is None:
            policy_bundle = pro_hooks.policy_bundle_override
        elif policy_bundle_factory is None:
            policy_bundle = _runner_module.load_policy_bundle_for_run(
                workspace_scope, config
            )
        else:
            policy_bundle = policy_bundle_factory(workspace_scope, config)
    elif policy_bundle_factory is None:
        policy_bundle = _runner_module.load_policy_bundle_for_run(
            workspace_scope, config
        )
    else:
        policy_bundle = policy_bundle_factory(workspace_scope, config)
    if pipeline_deps is not None and pipeline_deps.policy_bundle is not None:
        policy_bundle = pipeline_deps.policy_bundle
    elif pipeline_deps is not None and pipeline_deps.policy_bundle_factory is not None:
        policy_bundle = pipeline_deps.policy_bundle_factory(workspace_scope, config)
    _runner_module.register_role_handlers(policy_bundle.pipeline)
    registry: _RegistryLike
    if pipeline_deps is not None and pipeline_deps.registry_factory is not None:
        registry = cast("_RegistryLike", pipeline_deps.registry_factory(config))
    elif registry_factory is None:
        registry = _runner_module.AgentRegistry.from_config(config)
    else:
        registry = registry_factory(config)
    if pipeline_deps is not None and pipeline_deps.state_factory is not None:
        state = initial_state or pipeline_deps.state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    elif state_factory is None:
        state = initial_state or _runner_module.create_initial_state(
            config,
            agents_policy=policy_bundle.agents,
            pipeline_policy=policy_bundle.pipeline,
            counter_overrides=counter_overrides,
        )
    elif initial_state is not None:
        state = initial_state
    else:
        state = state_factory(
            config,
            policy_bundle.agents,
            policy_bundle.pipeline,
            counter_overrides,
        )
    effective_verbosity = normalize_verbosity(
        verbosity if verbosity is not None else config.general.verbosity
    )
    is_quiet = verbosity_rank(effective_verbosity) <= VERBOSITY_RANK[Verbosity.QUIET]
    _sleep = _recovery_sleep or time.sleep
    connectivity_monitor, _monitor_stop = _setup_connectivity_monitor(connectivity_monitor)
    if pipeline_deps is not None and pipeline_deps.recovery_controller_factory is not None:
        _controller, _ = pipeline_deps.recovery_controller_factory(state, policy_bundle, config)
    elif recovery_controller_factory is None:
        _controller, _ = _build_recovery_controller(state, policy_bundle, config)
    else:
        _controller, _ = recovery_controller_factory(state, policy_bundle, config)
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
    _effective_marker_watcher_factory = marker_watcher_factory
    if pipeline_deps is not None and pipeline_deps.marker_watcher_factory is not None:
        _effective_marker_watcher_factory = pipeline_deps.marker_watcher_factory
    if pipeline_deps is not None and pipeline_deps.snapshot_registry is not None:
        snapshot_registry = pipeline_deps.snapshot_registry
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
    if _module_legacy_obj is not _start_pro_marker_watcher and callable(
        _module_legacy_obj
    ):
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
        snapshot_registry=snapshot_registry,
        pipeline_deps=pipeline_deps,
    )
    return _execute_with_cleanup(state, loop_ctx, state.phase, _unsubscribe_bus, _display_stop)


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
    from ralph.pro_support.watcher import ProMarkerWatcher  # noqa: PLC0415

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
