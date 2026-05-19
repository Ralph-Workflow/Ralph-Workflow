"""Pipeline runner: orchestration glue that wires extracted submodules together.

This module coordinates effect dispatch, step execution, and policy resolution.
Heavy lifting is delegated to focused submodules; runner.py owns only the
plumbing that connects them.
"""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.agents.invoke import AgentInvocationError, invoke_agent
from ralph.agents.registry import AgentRegistry
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.config.enums import Verbosity
from ralph.display.artifact_renderer import render_commit_message
from ralph.display.context import install_width_refresher, make_display_context
from ralph.display.phase_banner import show_phase_close_banner, show_phase_transition
from ralph.executor.process import run_process_async
from ralph.git.operations import create_commit, stage_all
from ralph.interrupt.asyncio_bridge import install_signal_handlers
from ralph.mcp.protocol.startup import heartbeat_policy_from_env
from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory
from ralph.mcp.server.lifecycle import (
    check_mcp_bridge_health,
    shutdown_mcp_server,
    start_mcp_server,
)
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases import handle_phase, register_role_handlers
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline._runner_interrupt import (
    handle_keyboard_interrupt as _handle_keyboard_interrupt,
)
from ralph.pipeline._runner_mcp_validation import (
    default_probe_agent_transports as _default_probe_agent_transports,
)
from ralph.pipeline._runner_mcp_validation import (
    default_validate_mcp as _default_validate_mcp,
)
from ralph.pipeline._runner_mcp_validation import (
    run_custom_mcp_validation,
)
from ralph.pipeline._runner_session import (
    apply_session_capture as _apply_session_capture,
)
from ralph.pipeline._runner_session import (
    set_last_captured_session_id as _set_last_captured_session_id,
)
from ralph.pipeline._runner_state_helpers import (
    notify_pipeline_subscriber as _notify_pipeline_subscriber,
)
from ralph.pipeline._runner_state_helpers import (
    reset_phase_chain_for_recovery as _reset_phase_chain_for_recovery,
)
from ralph.pipeline.activity_stream import (
    MAX_METADATA_SUMMARY_LENGTH,
    MAX_TEXT_LENGTH,
    MAX_TOOL_RESULT_BRIEF,
    metadata_summary,
    record_activity_on_subscriber,
    render_agent_activity_line,
    terminal_width,
    truncate,
)
from ralph.pipeline.agent_execution_deps import AgentExecutionDeps
from ralph.pipeline.agent_recovery_plan import AgentRecoveryPlan
from ralph.pipeline.commit_executor import (
    cleanup_commit_message_artifacts,
    commit_effect,
    default_mcp_capabilities_for_phase,
    phase_output_artifact_paths,
    repo_has_commit_work,
)
from ralph.pipeline.commit_executor import (
    execute_commit_effect as _ee_execute_commit_effect,
)
from ralph.pipeline.cycle_baseline import (
    clear_cycle_baseline,
    read_cycle_baseline,
    write_cycle_baseline,
)
from ralph.pipeline.effect_executor import (
    execute_agent_effect as _ee_execute_agent_effect,
)
from ralph.pipeline.effect_executor import (
    recovery_context_lines,
    recovery_error_parts,
    resolve_recovery_session_id,
    retry_prompt_file_for_context,
    retryable_agent_failure_reason,
)
from ralph.pipeline.effect_router import (
    determine_effect_from_policy,
)
from ralph.pipeline.effects import (
    CommitEffect,
    EarlySkipCommitEffect,
    Effect,
    ExhaustedAnalysisPhaseAdvanceEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.fan_out import execute_fan_out_sync as _fan_out_execute_fan_out_sync
from ralph.pipeline.handoffs import resolve_exhausted_analysis_bypass, resolve_phase_drain
from ralph.pipeline.legacy_console_display import (
    LegacyConsoleDisplay,
    emit_display_line,
    resolve_display,
    status_text,
)
from ralph.pipeline.phase_agent_handler import (
    phase_event_after_agent_run,
)
from ralph.pipeline.phase_transition import (
    PENDING_PHASE_TRANSITION_METADATA_ATTR,
    PendingPhaseTransitionMetadata,
    clear_phase_materialization_outputs,
    emit_final_summary,
    record_phase_transition_metadata,
    show_phase_start_with_context,
    skipped_exhausted_analysis_info,
)
from ralph.pipeline.phase_transition import (
    emit_phase_transition_if_changed as _pt_emit_phase_transition_if_changed,
)
from ralph.pipeline.prompt_prep import (
    _materialize_prepared_prompt as _materialize_prepared_prompt_impl,
)
from ralph.pipeline.prompt_prep import (
    materialize_agent_prompt_if_needed,
    prompt_session_drain_for_phase,
)
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.run_loop import run
from ralph.pipeline.state import CommitState, PipelineState
from ralph.pipeline.state_init import create_initial_state
from ralph.policy.loader import (
    load_policy_for_workspace_scope,
)
from ralph.policy.loader import (
    load_policy_or_die as _dir_load_policy_or_die,
)
from ralph.process.manager import process_phase_scope
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.prompts.materialize import MissingPlanHandoffError, materialize_prompt_for_phase
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.recovery.classifier import FailureContext
from ralph.workspace import FsWorkspace
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.agent_execution_deps import (
        _CheckMcpBridgeHealthFn,
        _McpSupervisorFactory,
        _ShutdownMcpServerFn,
        _StartMcpServerFn,
    )
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        PipelinePolicy,
        PolicyBundle,
    )
    from ralph.recovery.controller import RecoveryController


__all__ = [
    "MAX_METADATA_SUMMARY_LENGTH",
    "MAX_TEXT_LENGTH",
    "MAX_TOOL_RESULT_BRIEF",
    "PENDING_PHASE_TRANSITION_METADATA_ATTR",
    "AgentRegistry",
    "DynamicBindingMcpServerFactory",
    "McpSupervisor",
    "PendingPhaseTransitionMetadata",
    "SubprocessAgentExecutor",
    "available_width",
    "build_agent_recovery_plan",
    "build_session_mcp_plan",
    "check_mcp_bridge_health",
    "clear_cycle_baseline",
    "commit_effect",
    "create_initial_state",
    "default_mcp_capabilities_for_phase",
    "emit_final_summary",
    "emit_phase_transition_if_changed",
    "execute_agent_effect",
    "execute_commit_effect",
    "handle_phase",
    "heartbeat_policy_from_env",
    "install_signal_handlers",
    "install_width_refresher",
    "make_display_context",
    "materialize_prompt_for_phase",
    "materialize_system_prompt",
    "metadata_summary",
    "phase_output_artifact_paths",
    "prompt_session_drain_for_phase",
    "record_activity_on_subscriber",
    "reducer_reduce",
    "register_role_handlers",
    "render_agent_activity_line",
    "render_commit_message",
    "repo_has_commit_work",
    "resolve_display",
    "resolve_workspace_scope",
    "run",
    "run_process_async",
    "show_phase_close_banner",
    "show_phase_transition",
    "shutdown_mcp_server",
    "skipped_exhausted_analysis_info",
    "start_mcp_server",
    "terminal_width",
    "truncate",
]


if TYPE_CHECKING:

    class _PipelineSubscriber(Protocol):
        def notify(self, state: PipelineState) -> None: ...

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> _RegistryLike: ...

    class _ExecuteEffectKwargsFn(Protocol):
        def __call__(
            self,
            effect: Effect,
            config: UnifiedConfig,
            workspace_scope: WorkspaceScope,
            **kwargs: object,
        ) -> Event: ...

    class _ConnectivityMonitorLike(Protocol):
        @property
        def current_state(self) -> object: ...

        def add_listener(self, cb: Callable[[object], None]) -> Callable[[], None]: ...


_LEGACY_EXECUTE_EFFECT_ARITY = 3
_POLICY_LOADER_CONFIG_ARITY = 2

load_policy_or_die = _dir_load_policy_or_die

VALIDATE_MCP = _default_validate_mcp
PROBE_AGENT_TRANSPORTS = _default_probe_agent_transports
_VALIDATE_MCP = _default_validate_mcp
_PROBE_AGENT_TRANSPORTS = _default_probe_agent_transports


def _validate_custom_mcp_servers(workspace_root: Path) -> int:
    effective_validate = (
        VALIDATE_MCP if VALIDATE_MCP is not _default_validate_mcp else _VALIDATE_MCP
    )
    effective_probe = (
        PROBE_AGENT_TRANSPORTS
        if PROBE_AGENT_TRANSPORTS is not _default_probe_agent_transports
        else _PROBE_AGENT_TRANSPORTS
    )
    return run_custom_mcp_validation(workspace_root, effective_validate, effective_probe)


validate_custom_mcp_servers = _validate_custom_mcp_servers


def _execute_effect(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
) -> PipelineEvent:
    deps = AgentExecutionDeps(
        invoke_agent=invoke_agent,
        agent_invocation_error=AgentInvocationError,
        agent_registry=AgentRegistry,
        show_phase_start_cb=show_phase_start_with_context,
        set_session_id_cb=_set_last_captured_session_id,
    )
    if isinstance(effect, InvokeAgentEffect):
        return execute_agent_effect(
            effect,
            config,
            deps,
            workspace_scope,
            display=display,
            verbosity=verbosity,
            state=state,
            policy_bundle=policy_bundle,
        )
    if isinstance(effect, CommitEffect):
        return execute_commit_effect(
            effect, create_commit, stage_all, workspace_scope.root, display, verbosity=verbosity
        )
    if isinstance(effect, EarlySkipCommitEffect):
        logger.info("Skipping commit early: worktree is clean")
        _cleanup_commit_message_artifacts(workspace_scope.root)
        return PipelineEvent.COMMIT_SKIPPED
    if isinstance(effect, ExhaustedAnalysisPhaseAdvanceEffect):
        if state is not None and policy_bundle is not None:
            bypass = resolve_exhausted_analysis_bypass(state, effect.phase, policy_bundle.pipeline)
            logger.info(
                "Skipping exhausted analysis phase '{}' and reducing PHASE_ADVANCE to '{}'",
                effect.phase,
                bypass.target_phase,
            )
        else:
            logger.warning(
                "Skipping exhausted analysis phase '{}' without routing context", effect.phase
            )
        return PipelineEvent.PHASE_ADVANCE
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED

    logger.warning("Unknown effect type: {}", type(effect))
    return PipelineEvent.AGENT_FAILURE


def _execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
) -> Event:
    fn = execute_effect
    params = signature(fn).parameters
    accepts_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
    all_opts: dict[str, object] = {
        "display": display,
        "display_context": display_context,
        "verbosity": verbosity,
        "state": state,
        "policy_bundle": policy_bundle,
    }
    supported = all_opts if accepts_kwargs else {k: v for k, v in all_opts.items() if k in params}
    return cast("_ExecuteEffectKwargsFn", fn)(effect, config, workspace_scope, **supported)


def execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
) -> Event:
    """Execute an effect and return the resulting event, optionally routing output to a display."""
    return _execute_effect_with_optional_display(
        effect,
        config,
        workspace_scope,
        display=display,
        display_context=display_context,
        verbosity=verbosity,
        state=state,
        policy_bundle=policy_bundle,
    )


def _invoke_execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | LegacyConsoleDisplay | None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity,
    state: PipelineState,
    policy_bundle: PolicyBundle,
) -> Event:
    return execute_effect_with_optional_display(
        effect,
        config,
        workspace_scope,
        display=display,
        display_context=display_context,
        verbosity=verbosity,
        state=state,
        policy_bundle=policy_bundle,
    )


def _reduce_runtime_recovery(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    *,
    reason: str,
    recovery: RecoveryController | None = None,
    exc: BaseException | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if recovery is not None:
        raw_failure: BaseException | str = exc if exc is not None else reason
        new_state, effects, _ = recovery.handle(
            state,
            raw_failure,
            FailureContext(phase=state.phase, agent=state.current_agent()),
        )
        if state.work_units and not new_state.work_units:
            new_state = new_state.copy_with(work_units=state.work_units)
        return new_state, effects
    failure_event = PhaseFailureEvent(
        phase=state.phase,
        reason=reason,
        recoverable=True,
    )
    recovered_state, effects = reducer_reduce(state, failure_event, pipeline_policy, recovery=None)
    return recovered_state, effects


def _save_checkpoint_or_log(
    state: PipelineState,
    *,
    message: str,
) -> None:
    try:
        ckpt.save(state)
    except Exception as exc:
        logger.exception(message, phase=state.phase, err=exc)


def _run_pipeline_step(
    *,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    display: ParallelDisplay | LegacyConsoleDisplay,
    display_context: DisplayContext,
    verbosity: Verbosity,
    registry: _RegistryLike,
    pipeline_subscriber: _PipelineSubscriber | None,
    recovery_controller: RecoveryController | None = None,
    _monitor_stop_cb: Callable[[], None] | None = None,
) -> PipelineState | int:
    try:
        effect = call_determine_effect_from_policy(state, policy_bundle, workspace_scope, config)
        inline_result = handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=policy_bundle.pipeline,
            artifacts_policy=policy_bundle.artifacts,
            agents_policy=policy_bundle.agents,
            workspace_scope=workspace_scope,
            display=display,
            pipeline_subscriber=pipeline_subscriber,
        )
        if inline_result is not None:
            return inline_result

        if isinstance(effect, FanOutEffect):
            return execute_fan_out_sync(
                effect=effect,
                state=state,
                display=display,
                policy_bundle=policy_bundle,
                workspace_scope=workspace_scope,
                pipeline_subscriber=pipeline_subscriber,
                config=config,
                monitor_stop_cb=_monitor_stop_cb,
            )

        with process_phase_scope(state.phase):
            workspace = FsWorkspace(
                workspace_scope.root,
                allowed_roots=workspace_scope.allowed_roots,
            )
            _mat_fn = (
                materialize_prompt_for_phase
                if materialize_prompt_for_phase is not _original_materialize_prompt_for_phase
                else None
            )
            materialize_agent_prompt_if_needed(
                effect, state, workspace, policy_bundle, registry, materialize_fn=_mat_fn
            )
            event = invoke_execute_effect_with_optional_display(
                effect,
                config,
                workspace_scope,
                display=display,
                display_context=display_context,
                verbosity=verbosity,
                state=state,
                policy_bundle=policy_bundle,
            )
            if isinstance(effect, InvokeAgentEffect):
                state = _apply_session_capture(state)
            if isinstance(effect, InvokeAgentEffect) and event == PipelineEvent.AGENT_SUCCESS:
                if recovery_controller is not None:
                    recovery_controller.reset_backoff(effect.phase, effect.agent_name)
                _hp_fn = handle_phase if handle_phase is not _original_handle_phase else None
                event = phase_event_after_agent_run(
                    effect=effect,
                    config=config,
                    policy_bundle=policy_bundle,
                    workspace=workspace,
                    workspace_scope=workspace_scope,
                    display=display,
                    display_context=display_context,
                    verbosity=verbosity,
                    state=state,
                    handle_phase_fn=_hp_fn,
                )

        _commit_phase_def = policy_bundle.pipeline.phases.get(state.phase)
        if (
            isinstance(effect, CommitEffect)
            and _commit_phase_def is not None
            and _commit_phase_def.role == "commit"
            and event in (PipelineEvent.COMMIT_SUCCESS, PipelineEvent.COMMIT_SKIPPED)
        ):
            clear_cycle_baseline(workspace_scope.root)
        next_state, _ = reducer_reduce(
            state,
            event,
            policy_bundle.pipeline,
            recovery=recovery_controller,
        )
        skipped_phases = record_phase_transition_metadata(
            display,
            state,
            event,
            next_state,
            policy_bundle.pipeline,
        )
        for skipped_phase in skipped_phases:
            clear_phase_materialization_outputs(workspace, skipped_phase)
        _notify_pipeline_subscriber(pipeline_subscriber, next_state)
        _save_checkpoint_or_log(
            next_state,
            message=(
                "Checkpoint save failed in phase={phase}: {err} -- continuing without checkpoint"
            ),
        )
        return next_state
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        logger.exception(
            "Pipeline step crashed in phase={phase}: {err}",
            phase=state.phase,
            err=exc,
        )
        recovered_state, _recv_effects = _reduce_runtime_recovery(
            state,
            policy_bundle.pipeline,
            reason=f"Pipeline step crashed: {type(exc).__name__}: {exc}",
            recovery=recovery_controller,
            exc=exc,
        )
        for _eff in _recv_effects:
            if isinstance(_eff, ExitFailureEffect):
                emit_display_line(
                    display, None, status_text("Recovery exhausted", _eff.reason, "red")
                )
                return 1
        _notify_pipeline_subscriber(pipeline_subscriber, recovered_state)
        _save_checkpoint_or_log(
            recovered_state,
            message="Checkpoint save failed while recording recovery in phase={phase}: {err}",
        )
        return recovered_state


def _load_policy_bundle_for_run(
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
) -> PolicyBundle:
    if load_policy_or_die is not _dir_load_policy_or_die:
        effective_policy_dir = workspace_scope.resolve_agent_file("pipeline.toml").parent
        loader = load_policy_or_die
        params = signature(loader).parameters
        if "config" in params:
            return loader(effective_policy_dir, config=config)

        positional = [
            param
            for param in params.values()
            if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
        ]
        if (
            any(param.kind == param.VAR_KEYWORD for param in params.values())
            or len(positional) >= _POLICY_LOADER_CONFIG_ARITY
        ):
            return loader(effective_policy_dir, config=config)
        return loader(effective_policy_dir)

    return load_policy_for_workspace_scope(workspace_scope, config=config)


def _handle_inline_effect(
    *,
    effect: Effect,
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    workspace_scope: WorkspaceScope,
    agents_policy: AgentsPolicy | None = None,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    dashboard_subscriber: _PipelineSubscriber | None = None,
) -> PipelineState | int | None:
    effective_subscriber = dashboard_subscriber or pipeline_subscriber

    if isinstance(effect, SaveCheckpointEffect):
        ckpt.save(state)
        new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED, pipeline_policy)
        _notify_pipeline_subscriber(effective_subscriber, new_state)
        return new_state

    if isinstance(effect, PreparePromptEffect):
        if not effect.skip_materialization:
            try:
                materialize_prepared_prompt(
                    effect,
                    pipeline_policy,
                    artifacts_policy,
                    workspace_scope,
                    agents_policy,
                    state,
                )
            except MissingPlanHandoffError as exc:
                if state.phase != pipeline_policy.recovery.failed_route:
                    raise
                logger.warning(
                    "Missing plan handoff for phase={phase}: {err}; re-routing to entry phase",
                    phase=effect.phase,
                    err=exc,
                )
                current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
                recovered_state = state.copy_with(
                    phase=pipeline_policy.entry_phase,
                    previous_phase=state.phase,
                    last_error=str(exc),
                    recovery_epoch=current_epoch + 1,
                )
                ckpt.save(recovered_state)
                _notify_pipeline_subscriber(effective_subscriber, recovered_state)
                return recovered_state
        prepared_state = state
        if state.phase == pipeline_policy.recovery.failed_route:
            prepared_state = _reset_phase_chain_for_recovery(state, effect.phase)
            target_phase_def = pipeline_policy.phases.get(effect.phase)
            if target_phase_def is not None and target_phase_def.role == "commit":
                prepared_state = prepared_state.copy_with(commit=CommitState())
            if target_phase_def is not None and target_phase_def.role == "execution":
                clear_cycle_baseline(workspace_scope.root)
                write_start_commit_if_absent(workspace_scope.root)
        updated_state = prepared_state.copy_with(
            phase=effect.phase,
            current_drain=effect.drain or resolve_phase_drain(effect.phase, pipeline_policy),
        )
        ckpt.save(updated_state)
        _notify_pipeline_subscriber(effective_subscriber, updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        emit_display_line(display, None, "[green]Pipeline completed successfully.[/green]")
        return 0

    if isinstance(effect, ExitFailureEffect):
        emit_display_line(
            display,
            None,
            status_text("Recovery triggered", effect.reason, "yellow"),
        )
        current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
        recovered_state = state.copy_with(
            phase=pipeline_policy.recovery.failed_route,
            previous_phase=state.phase,
            last_error=effect.reason,
            recovery_epoch=current_epoch + 1,
        )
        ckpt.save(recovered_state)
        _notify_pipeline_subscriber(effective_subscriber, recovered_state)
        return recovered_state

    return None


def _call_determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
) -> Effect:
    fn = determine_effect_from_policy
    params = signature(fn).parameters
    if "config" in params:
        return fn(state, policy_bundle, workspace_scope, config=config)

    positional = [
        param
        for param in params.values()
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    if (
        any(param.kind == param.VAR_POSITIONAL for param in params.values())
        or len(positional) >= _LEGACY_EXECUTE_EFFECT_ARITY
    ):
        return fn(state, policy_bundle, workspace_scope)
    return fn(state, policy_bundle)


def build_agent_recovery_plan(
    *,
    exc: Exception,
    attempt_index: int,
    max_recovery_attempts: int,
    effect: InvokeAgentEffect,
    workspace_root: Path,
    raw_output: list[str],
    rendered_output: list[str],
    extracted_session_id: str | None,
    inactivity_error_type: type[Exception],
) -> AgentRecoveryPlan | None:
    """Determine whether and how to retry a failed agent invocation."""
    if attempt_index >= max_recovery_attempts:
        return None
    reason = retryable_agent_failure_reason(exc, inactivity_error_type)
    if reason is None:
        return None
    context_lines = recovery_context_lines(
        exc, raw_output, rendered_output, _fn=recovery_error_parts
    )
    prompt_file = retry_prompt_file_for_context(
        workspace_root=workspace_root,
        prompt_file=effect.prompt_file,
        reason=reason,
        context_lines=context_lines,
    )
    session_id = resolve_recovery_session_id(exc, extracted_session_id, inactivity_error_type)
    return AgentRecoveryPlan(prompt_file=prompt_file, session_id=session_id, reason=reason)


_original_start_mcp_server = start_mcp_server
_original_shutdown_mcp_server = shutdown_mcp_server
_original_check_mcp_bridge_health = check_mcp_bridge_health
_original_materialize_system_prompt = materialize_system_prompt
_original_mcp_supervisor = McpSupervisor
_original_heartbeat_policy_from_env = heartbeat_policy_from_env
_original_materialize_prompt_for_phase = materialize_prompt_for_phase
_original_handle_phase = handle_phase
_original_render_commit_message = render_commit_message
_original_show_phase_close_banner = show_phase_close_banner
_original_show_phase_transition = show_phase_transition
_cleanup_commit_message_artifacts = cleanup_commit_message_artifacts


def execute_fan_out_sync(
    *,
    effect: FanOutEffect,
    state: PipelineState,
    display: ParallelDisplay | LegacyConsoleDisplay,
    **opts: object,
) -> PipelineState:
    """Execute fan-out synchronously, forwarding current module globals as injectable overrides."""
    return _fan_out_execute_fan_out_sync(
        effect=effect,
        state=state,
        display=display,
        _install_signal_handlers=install_signal_handlers,
        _executor_cls=SubprocessAgentExecutor,
        _mcp_factory_cls=DynamicBindingMcpServerFactory,
        _run_process_async=run_process_async,
        _reducer_reduce=reducer_reduce,
        **opts,
    )


def materialize_prepared_prompt(
    effect: PreparePromptEffect,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    workspace_scope: WorkspaceScope,
    agents_policy: AgentsPolicy | None = None,
    state: PipelineState | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Delegate to _materialize_prepared_prompt, injecting the patchable prompt function."""
    _mat_fn = (
        materialize_prompt_for_phase
        if materialize_prompt_for_phase is not _original_materialize_prompt_for_phase
        else None
    )
    _materialize_prepared_prompt_impl(
        effect,
        pipeline_policy,
        artifacts_policy,
        workspace_scope,
        agents_policy=agents_policy,
        state=state,
        env=env,
        materialize_fn=_mat_fn,
    )


def available_width(prefix_len: int) -> int:
    """Return usable terminal width minus prefix and padding."""
    return max(40, terminal_width() - prefix_len - 2)


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    **opts: object,
) -> PipelineEvent:
    """Execute an agent-invocation effect, injecting any patched MCP lifecycle hooks."""
    effective_start_fn = (
        start_mcp_server if start_mcp_server is not _original_start_mcp_server else None
    )
    effective_shutdown_fn = (
        shutdown_mcp_server if shutdown_mcp_server is not _original_shutdown_mcp_server else None
    )
    effective_health_fn = (
        check_mcp_bridge_health
        if check_mcp_bridge_health is not _original_check_mcp_bridge_health
        else None
    )
    effective_materialize_fn = (
        materialize_system_prompt
        if materialize_system_prompt is not _original_materialize_system_prompt
        else None
    )
    effective_supervisor = McpSupervisor if McpSupervisor is not _original_mcp_supervisor else None
    effective_heartbeat_fn = (
        heartbeat_policy_from_env
        if heartbeat_policy_from_env is not _original_heartbeat_policy_from_env
        else None
    )
    effective_deps = AgentExecutionDeps(
        invoke_agent=deps.invoke_agent,
        agent_invocation_error=deps.agent_invocation_error,
        agent_registry=deps.agent_registry,
        show_phase_start_cb=deps.show_phase_start_cb or show_phase_start_with_context,
        set_session_id_cb=deps.set_session_id_cb,
        start_mcp_server_fn=cast(
            "_StartMcpServerFn | None", deps.start_mcp_server_fn or effective_start_fn
        ),
        shutdown_mcp_server_fn=cast(
            "_ShutdownMcpServerFn | None",
            deps.shutdown_mcp_server_fn or effective_shutdown_fn,
        ),
        check_mcp_bridge_health_fn=cast(
            "_CheckMcpBridgeHealthFn | None",
            deps.check_mcp_bridge_health_fn or effective_health_fn,
        ),
        materialize_system_prompt_fn=deps.materialize_system_prompt_fn or effective_materialize_fn,
        mcp_supervisor_factory=cast(
            "_McpSupervisorFactory | None", deps.mcp_supervisor_factory or effective_supervisor
        ),
        heartbeat_policy_from_env_fn=deps.heartbeat_policy_from_env_fn or effective_heartbeat_fn,
    )
    return _ee_execute_agent_effect(effect, config, effective_deps, workspace_scope, **opts)


def execute_commit_effect(
    effect: CommitEffect,
    create_commit_fn: Callable[[Path | str, str], str],
    stage_all_fn: Callable[[Path | str], None],
    repo_root: Path,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    **opts: object,
) -> PipelineEvent:
    """Execute a commit effect, injecting any patched render_commit_message hook."""
    effective_render_fn = (
        render_commit_message
        if render_commit_message is not _original_render_commit_message
        else None
    )
    return _ee_execute_commit_effect(
        effect,
        repo_root,
        display,
        create_commit_fn=create_commit_fn,
        stage_all_fn=stage_all_fn,
        has_commit_work_fn=repo_has_commit_work,
        render_commit_message_fn=opts.pop("render_commit_message_fn", None) or effective_render_fn,
        **opts,
    )


def emit_phase_transition_if_changed(
    display: ParallelDisplay | LegacyConsoleDisplay,
    previous_phase: str,
    state: PipelineState,
    *,
    verbosity: Verbosity,
    pipeline_policy: PipelinePolicy,
) -> str:
    """Emit phase-transition banners if the active phase changed, injecting patched banner hooks."""
    _close_fn = (
        show_phase_close_banner
        if show_phase_close_banner is not _original_show_phase_close_banner
        else None
    )
    _trans_fn = (
        show_phase_transition
        if show_phase_transition is not _original_show_phase_transition
        else None
    )
    return _pt_emit_phase_transition_if_changed(
        display,
        previous_phase,
        state,
        verbosity=verbosity,
        pipeline_policy=pipeline_policy,
        show_close_banner_fn=_close_fn,
        show_transition_fn=_trans_fn,
    )


def write_start_commit_if_absent(workspace_root: Path) -> None:
    """Record the current HEAD as the cycle baseline if no baseline exists yet."""
    if read_cycle_baseline(workspace_root) is not None:
        return
    try:
        repo = Repo(workspace_root)
    except InvalidGitRepositoryError:
        return
    if not repo.head.is_valid():
        return
    write_cycle_baseline(workspace_root, repo.head.commit.hexsha, force=True)


call_determine_effect_from_policy = _call_determine_effect_from_policy
invoke_execute_effect_with_optional_display = _invoke_execute_effect_with_optional_display
handle_inline_effect = _handle_inline_effect
run_pipeline_step = _run_pipeline_step
execute_effect = _execute_effect
notify_pipeline_subscriber = _notify_pipeline_subscriber
handle_keyboard_interrupt = _handle_keyboard_interrupt
save_checkpoint_or_log = _save_checkpoint_or_log
load_policy_bundle_for_run = _load_policy_bundle_for_run
