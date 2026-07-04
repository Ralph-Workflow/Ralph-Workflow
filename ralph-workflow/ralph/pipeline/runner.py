"""Pipeline runner: orchestration glue that wires extracted submodules together.

This module coordinates effect dispatch, step execution, and policy resolution.
Heavy lifting is delegated to focused submodules; runner.py owns only the
plumbing that connects them.
"""

from __future__ import annotations

import os
from inspect import signature
from typing import TYPE_CHECKING, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.config.enums import Verbosity
from ralph.display.context import install_width_refresher, make_display_context
from ralph.display.parallel_display import (
    ParallelDisplay,
    emit_activity_line,
    resolve_display,
    status_text,
)
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
from ralph.onboarding import CODEBERG_STAR_CTA
from ralph.phases import handle_phase, register_role_handlers
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import progress
from ralph.pipeline._runner_interrupt import handle_keyboard_interrupt as _handle_keyboard_interrupt
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
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent
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
from ralph.pipeline.effect_executor import execute_agent_effect
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
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.fan_out import execute_fan_out_sync as _fan_out_execute_fan_out_sync
from ralph.pipeline.handoffs import resolve_exhausted_analysis_bypass, resolve_phase_drain
from ralph.pipeline.phase_agent_handler import (
    phase_event_after_agent_run,
)
from ralph.pipeline.phase_entry_cleaner import clear_phase_entry_drains
from ralph.pipeline.phase_transition import (
    PENDING_PHASE_TRANSITION_METADATA_ATTR,
    PendingPhaseTransitionMetadata,
    clear_phase_materialization_outputs,
    emit_final_summary,
    record_phase_transition_metadata,
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
    from ralph.mcp.websearch.secrets import EnvGetter
    from ralph.pipeline.factory import PipelineDeps
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
    "repo_has_commit_work",
    "resolve_display",
    "resolve_workspace_scope",
    "run_process_async",
    "shutdown_mcp_server",
    "skipped_exhausted_analysis_info",
    "start_mcp_server",
    "terminal_width",
    "truncate",
]


def __getattr__(name: str) -> object:
    """Lazy attribute proxy that breaks the runner <-> run_loop import cycle.

    ``ralph.pipeline.run_loop`` historically imports this module as
    ``_runner_module`` to reach the orchestration helpers, while this
    module historically re-exported the ``run`` entry point from
    ``run_loop``. Importing both eagerly produces a circular import
    error in some test-collection orders. Proxying the cross-module
    symbol via :pep:`562` ``__getattr__`` defers the resolution until
    the consumer actually needs it, eliminating the cycle while
    preserving the public re-export contract.
    """
    if name == "run":
        from ralph.pipeline.run_loop import run as _run_loop_entry  # noqa: PLC0415

        module_globals: dict[str, object] = globals()
        module_globals["run"] = _run_loop_entry
        return _run_loop_entry
    raise AttributeError(f"module 'ralph.pipeline.runner' has no attribute {name!r}")


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
    display: ParallelDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> PipelineEvent:
    resolved_display_context = display_context or (
        display._ctx if display is not None and hasattr(display, "_ctx") else make_display_context()
    )
    if pipeline_deps is None:
        pipeline_deps = DefaultPipelineFactory().build(config, resolved_display_context)
    if isinstance(effect, InvokeAgentEffect):
        return execute_agent_effect(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=resolved_display_context,
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
    display: ParallelDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
    pipeline_deps: PipelineDeps | None = None,
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
        "pipeline_deps": pipeline_deps,
    }
    supported = all_opts if accepts_kwargs else {k: v for k, v in all_opts.items() if k in params}
    return cast("_ExecuteEffectKwargsFn", fn)(effect, config, workspace_scope, **supported)


def execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | None = None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
    pipeline_deps: PipelineDeps | None = None,
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
        pipeline_deps=pipeline_deps,
    )


def _invoke_execute_effect_with_optional_display(
    effect: Effect,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None = None,
    verbosity: Verbosity,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    pipeline_deps: PipelineDeps | None = None,
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
        pipeline_deps=pipeline_deps,
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


def _checkpoint_path(workspace_scope: WorkspaceScope) -> Path:
    return workspace_scope.root / "checkpoint.json"


def _save_checkpoint_or_log(
    state: PipelineState,
    *,
    message: str,
    path: Path,
) -> None:
    try:
        ckpt.save(state, path)
    except Exception as exc:
        logger.exception(message, phase=state.phase, err=exc)


def _maybe_clear_invoke_agent_entry_drains(
    effect: Effect,
    state: PipelineState,
    workspace: FsWorkspace,
    policy_bundle: PolicyBundle,
) -> None:
    if isinstance(effect, InvokeAgentEffect):
        is_resume = (
            state.phase == effect.phase
            and state.previous_phase is None
            and state.checkpoint_saved_count > 0
        )
        if not is_resume:
            clear_phase_entry_drains(
                workspace,
                str(effect.phase),
                state.previous_phase,
                policy_bundle.pipeline,
                policy_bundle.artifacts,
            )


def _run_pipeline_step(
    *,
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    display: ParallelDisplay,
    display_context: DisplayContext,
    verbosity: Verbosity,
    registry: _RegistryLike,
    pipeline_subscriber: _PipelineSubscriber | None,
    recovery_controller: RecoveryController | None = None,
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    _monitor_stop_cb: Callable[[], None] | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> PipelineState | int:
    try:
        effect = call_determine_effect_from_policy(state, policy_bundle, workspace_scope, config)
        inline_result = handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=policy_bundle.pipeline,
            artifacts_policy=policy_bundle.artifacts,
            agents_policy=policy_bundle.agents,
            registry=registry,
            config=config,
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
                config_path=config_path,
                cli_overrides=cli_overrides,
                monitor_stop_cb=_monitor_stop_cb,
                pipeline_deps=pipeline_deps,
            )

        with process_phase_scope(state.phase):
            workspace = FsWorkspace(
                workspace_scope.root,
                allowed_roots=workspace_scope.allowed_roots,
            )
            _maybe_clear_invoke_agent_entry_drains(
                effect,
                state,
                workspace,
                policy_bundle,
            )
            materialize_agent_prompt_if_needed(
                effect,
                state,
                workspace,
                policy_bundle,
                registry,
                materialize_fn=(
                    pipeline_deps.phase_prompt_materializer if pipeline_deps is not None else None
                ),
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
                pipeline_deps=pipeline_deps,
            )
            if isinstance(effect, InvokeAgentEffect):
                state = _apply_session_capture(state)
            if isinstance(effect, InvokeAgentEffect) and event == PipelineEvent.AGENT_SUCCESS:
                if recovery_controller is not None:
                    recovery_controller.reset_backoff(effect.phase, effect.agent_name)
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
            path=_checkpoint_path(workspace_scope),
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
                emit_activity_line(
                    display, None, status_text("Recovery exhausted", _eff.reason, "red")
                )
                return 1
        _notify_pipeline_subscriber(pipeline_subscriber, recovered_state)
        _save_checkpoint_or_log(
            recovered_state,
            message="Checkpoint save failed while recording recovery in phase={phase}: {err}",
            path=_checkpoint_path(workspace_scope),
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
    registry: _RegistryLike | None = None,
    config: UnifiedConfig | None = None,
    display: ParallelDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    dashboard_subscriber: _PipelineSubscriber | None = None,
) -> PipelineState | int | None:
    effective_subscriber = dashboard_subscriber or pipeline_subscriber
    checkpoint_path = _checkpoint_path(workspace_scope)

    if isinstance(effect, SaveCheckpointEffect):
        ckpt.save(state, checkpoint_path)
        new_state, _ = reducer_reduce(state, PipelineEvent.CHECKPOINT_SAVED, pipeline_policy)
        _notify_pipeline_subscriber(effective_subscriber, new_state)
        return new_state

    if isinstance(effect, PreparePromptEffect):
        if not effect.skip_materialization:
            # Phase-agnostic resume guard: suppress clearing when restoring a checkpoint
            is_resume = (
                state is not None
                and str(state.phase) == str(effect.phase)
                and state.previous_phase is None
                and state.checkpoint_saved_count > 0
            )
            if not is_resume:
                _entry_ws = FsWorkspace(
                    workspace_scope.root, allowed_roots=workspace_scope.allowed_roots
                )
                clear_phase_entry_drains(
                    _entry_ws,
                    str(effect.phase),
                    effect.previous_phase,
                    pipeline_policy,
                    artifacts_policy,
                )
            try:
                materialize_prepared_prompt(
                    effect,
                    pipeline_policy,
                    artifacts_policy,
                    workspace_scope,
                    agents_policy,
                    state=state,
                    registry=registry,
                    config=config,
                )
            except MissingPlanHandoffError as exc:
                logger.warning(
                    "Missing plan handoff for phase={phase}: {err}; re-routing to entry phase",
                    phase=effect.phase,
                    err=exc,
                )
                current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
                recovered_state = progress.advance_phase(
                    state,
                    pipeline_policy.entry_phase,
                    policy=pipeline_policy,
                ).copy_with(
                    last_error=str(exc),
                    recovery_epoch=current_epoch + 1,
                )
                ckpt.save(recovered_state, checkpoint_path)
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
        prepare_updates: dict[str, object] = {
            "phase": effect.phase,
            "current_drain": effect.drain or resolve_phase_drain(effect.phase, pipeline_policy),
        }
        # A change of phase here (skip-invocation success route, failed-route
        # re-entry) must clear the next-attempt session action exactly like
        # progress.advance_phase does. Preserving it would leak a stale resume
        # session id / retry intent into an unrelated phase's first attempt.
        # Same-phase re-prompts (the retry-in-session resume path) intentionally
        # keep the intent so the resume can take effect.
        if effect.phase != prepared_state.phase:
            prepare_updates["last_agent_session_id"] = None
            prepare_updates["agent_retry_intent"] = cleared_agent_retry_intent()
        updated_state = prepared_state.copy_with(**prepare_updates)
        ckpt.save(updated_state, checkpoint_path)
        _notify_pipeline_subscriber(effective_subscriber, updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        return _emit_success_exit(display, os.getenv)

    if isinstance(effect, ExitFailureEffect):
        emit_activity_line(
            display,
            None,
            status_text("Recovery triggered", effect.reason, "yellow"),
        )
        current_epoch = state.recovery_epoch if isinstance(state.recovery_epoch, int) else 0
        recovered_state = progress.advance_phase(
            state,
            pipeline_policy.recovery.failed_route,
            policy=pipeline_policy,
        ).copy_with(
            last_error=effect.reason,
            recovery_epoch=current_epoch + 1,
        )
        ckpt.save(recovered_state, checkpoint_path)
        _notify_pipeline_subscriber(effective_subscriber, recovered_state)
        return recovered_state

    return None


def _emit_success_exit(
    display: ParallelDisplay | None,
    getenv: EnvGetter,
) -> int:
    emit_activity_line(display, None, "[green]Pipeline completed successfully.[/green]")
    # Periodic star CTA - shown ~50% of successful runs.
    # Only fires after first-run (first-run already shows full welcome panel with star CTA).
    # Uses process-id hash to avoid deterministic spam: each user sees it ~1 in 2 runs.
    show_cta = (hash(str(os.getpid()) + str(getenv("USER") or "")) % 2) == 0
    if show_cta:
        emit_activity_line(display, None, f"[bold yellow]{CODEBERG_STAR_CTA}[/bold yellow]")
    return 0


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


_cleanup_commit_message_artifacts = cleanup_commit_message_artifacts


def execute_fan_out_sync(
    *,
    effect: FanOutEffect,
    state: PipelineState,
    display: ParallelDisplay,
    pipeline_deps: PipelineDeps | None = None,
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
        pipeline_deps=pipeline_deps,
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
    *,
    registry: _RegistryLike | None = None,
    config: UnifiedConfig | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> None:
    """Delegate to _materialize_prepared_prompt, injecting the patchable prompt function."""
    _materialize_prepared_prompt_impl(
        effect,
        pipeline_policy,
        artifacts_policy,
        workspace_scope,
        agents_policy=agents_policy,
        state=state,
        env=env,
        materialize_fn=(
            pipeline_deps.phase_prompt_materializer if pipeline_deps is not None else None
        ),
        registry=registry,
        config=config,
    )


def available_width(prefix_len: int) -> int:
    """Return usable terminal width minus prefix and padding."""
    return max(40, terminal_width() - prefix_len - 2)


def execute_commit_effect(
    effect: CommitEffect,
    create_commit_fn: Callable[[Path | str, str], str],
    stage_all_fn: Callable[[Path | str], None],
    repo_root: Path,
    display: ParallelDisplay | None = None,
    **opts: object,
) -> PipelineEvent:
    """Execute a commit effect while preserving runner-level dependency injection hooks."""
    return _ee_execute_commit_effect(
        effect,
        repo_root,
        display,
        create_commit_fn=create_commit_fn,
        stage_all_fn=stage_all_fn,
        has_commit_work_fn=repo_has_commit_work,
        **opts,
    )


def emit_phase_transition_if_changed(
    display: ParallelDisplay,
    previous_phase: str,
    state: PipelineState,
    *,
    verbosity: Verbosity,
    pipeline_policy: PipelinePolicy,
) -> str:
    """Emit phase-transition surfaces via the consolidated display surface."""
    return _pt_emit_phase_transition_if_changed(
        display,
        previous_phase,
        state,
        verbosity=verbosity,
        pipeline_policy=pipeline_policy,
    )


def write_start_commit_if_absent(workspace_root: Path) -> None:
    """Persist the current HEAD SHA as the cycle baseline when no baseline exists yet."""
    if read_cycle_baseline(workspace_root) is not None:
        return

    repo: Repo | None = None
    try:
        repo = Repo(workspace_root)
        write_cycle_baseline(workspace_root, str(repo.head.commit.hexsha), force=True)
    except (InvalidGitRepositoryError, OSError, ValueError):
        return
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()


execute_effect = _execute_effect
handle_inline_effect = _handle_inline_effect
call_determine_effect_from_policy = _call_determine_effect_from_policy
invoke_execute_effect_with_optional_display = _invoke_execute_effect_with_optional_display
load_policy_bundle_for_run = _load_policy_bundle_for_run
run_pipeline_step = _run_pipeline_step
save_checkpoint_or_log = _save_checkpoint_or_log
notify_pipeline_subscriber = _notify_pipeline_subscriber
handle_keyboard_interrupt = _handle_keyboard_interrupt
