from __future__ import annotations

import contextlib
import os
import time
import uuid
from inspect import signature
from typing import TYPE_CHECKING, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.agents.registry import AgentRegistry
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.agents.system_clock import SystemClock
from ralph.config.enums import Verbosity
from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
)
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
from ralph.onboarding import GITHUB_STAR_CTA
from ralph.phases import handle_phase, register_role_handlers
from ralph.phases._commit_review_events import (
    CommitReviewEvent,
    CommitReviewGateFailed,
    CommitReviewRejected,
)
from ralph.phases.timing import PhaseTimer
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import progress
from ralph.pipeline._auto_integrate_agent_phase import process_agent_phases
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
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
    recover_missing_plan_handoff as _recover_missing_plan_handoff,
)
from ralph.pipeline._runner_state_helpers import (
    reset_phase_chain_for_recovery as _reset_phase_chain_for_recovery,
)
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
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
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    register_entry_point,
    registered_entry_points,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptInvoke, AttemptRunnerDeps
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state
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
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_cycle_timebox,
    apply_development_timebox,
    conclude_cycle_on_route_out_of_cycle,
    conclude_development_timebox_on_route_out_of_development,
)
from ralph.pipeline.effect_router import (
    determine_effect_from_policy,
)
from ralph.pipeline.effects import (
    CommitEffect,
    CommitReviewRejectEffect,
    Effect,
    EmptyCommitEffect,
    ExhaustedAnalysisPhaseAdvanceEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import (
    AgentFailureEvent,
    CommitResidualEvent,
    Event,
    PhaseFailureEvent,
    PipelineEvent,
)
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineDeps
from ralph.pipeline.fan_out import execute_fan_out_sync as _fan_out_execute_fan_out_sync
from ralph.pipeline.handoffs import resolve_exhausted_analysis_bypass, resolve_phase_drain
from ralph.pipeline.integration_dispatch_blocked_error import (
    IntegrationDispatchBlockedError,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.integration_resolution import (
    RESOLUTION_DRAIN,
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.phase_agent_handler import (
    phase_event_after_agent_run,
    render_phase_failure_report,
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
    cycle_deadline_suspended,
    materialize_agent_prompt_if_needed,
    prompt_session_drain_for_phase,
    publish_cycle_deadline_env,
    withdraw_cycle_deadline_env,
)
from ralph.pipeline.rebase_state import (
    HISTORY_AWARE_CONFLICT_STRATEGY_INDEX,
    RebaseState,
    StartupRetentionReason,
)
from ralph.pipeline.reducer import redirect_expired_cycle_in_place
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
from ralph.prompts.master_prompt import materialize_master_prompt
from ralph.prompts.materialize import MissingPlanHandoffError, materialize_prompt_for_phase
from ralph.recovery.classifier import FailureContext
from ralph.telemetry._sentry import record_phase_execution
from ralph.visual.capture_lifecycle import CaptureLifecycle
from ralph.visual.capture_set import CaptureSet
from ralph.workspace import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

execute_agent_effect = PRODUCTION_INVOKE

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import Protocol

    from ralph.config.enums import PipelinePhase
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.websearch.secrets import EnvGetter
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.commit_executor import _CreateCommitFn
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        CycleTimeboxPolicy,
        DevelopmentTimeboxPolicy,
        PhaseDefinition,
        PipelinePolicy,
        PolicyBundle,
    )
    from ralph.recovery.controller import RecoveryController
    from ralph.visual.capture_request import CaptureRequest
    from ralph.visual.policy_facts import PolicyFacts


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
    "execute_commit_effect_from_deps",
    "execute_pending_rollback",
    "handle_phase",
    "heartbeat_policy_from_env",
    "install_signal_handlers",
    "install_width_refresher",
    "make_display_context",
    "materialize_master_prompt",
    "materialize_prompt_for_phase",
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
    "rollback_gate_failure_state",
    "run_process_async",
    "run_visual_capture_lifecycle",
    "shutdown_mcp_server",
    "skipped_exhausted_analysis_info",
    "start_mcp_server",
    "terminal_width",
    "truncate",
]


def __getattr__(name: str) -> object:
    if name == "run":
        from ralph.pipeline.run_loop import run as _run_loop_entry

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
        ) -> Event | CommitReviewEvent: ...

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


def _execute_commit_effect_from_deps(
    effect: CommitEffect,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | None,
    verbosity: Verbosity,
    config: UnifiedConfig | None = None,
) -> Event:
    if pipeline_deps.commit_effect_executor is not None:
        return cast("Event", pipeline_deps.commit_effect_executor(effect, workspace_scope.root))
    target: str | None = config.general.auto_integrate_target if config is not None else None
    return execute_commit_effect(
        effect,
        create_commit,
        stage_all,
        workspace_scope.root,
        display,
        verbosity=verbosity,
        has_residual_work_fn=repo_has_commit_work,
        config=config,
        auto_integrate_target=target,
    )


execute_commit_effect_from_deps = _execute_commit_effect_from_deps


def _main_phase_loop_controller(
    *,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle | None,
    deps: AttemptRunnerDeps,
    state: PipelineState | None,
    pipeline_deps: PipelineDeps | None,
) -> RecoveryController:
    if isinstance(pipeline_deps, PipelineDeps):
        existing = pipeline_deps.recovery_controller
        if existing is not None:
            return existing
    return build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=config,
        policy_bundle=policy_bundle,
        deps=deps,
        ledger_snapshot=state.ledger_snapshot if state is not None else None,
        false_bench_snapshot=state.false_bench_feedback if state is not None else None,
    )


def _run_main_phase_loop_attempt(
    *_args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    return run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


if AgentEntryPoint.MAIN_PHASE_LOOP not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        _run_main_phase_loop_attempt,
    )


def _keyword_effect_invoker(execute: AttemptInvoke) -> AttemptInvoke:
    def invoke(target: object, **kwargs: object) -> object:
        config = kwargs.pop("config")
        pipeline_deps = kwargs.pop("pipeline_deps")
        workspace_scope = kwargs.pop("workspace_scope")
        return execute(target, config, pipeline_deps, workspace_scope, **kwargs)

    return cast("AttemptInvoke", invoke)


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
    run_id: str | None = None,
) -> Event | CommitReviewEvent:
    resolved_display_context = display_context or (
        display._ctx if display is not None and hasattr(display, "_ctx") else make_display_context()
    )
    if pipeline_deps is None:
        pipeline_deps = DefaultPipelineFactory().build(config, resolved_display_context)
    if isinstance(effect, InvokeAgentEffect):
        with process_agent_phases().agent_phase(workspace_scope.root):
            deps = AttemptRunnerDeps(
                clock=SystemClock(),
                invoke=_keyword_effect_invoker(execute_agent_effect),
                probe_runner=None,
                workspace_probe=DefaultWorkspaceChangeProbe(),
                connectivity=None,
            )
            result = _run_main_phase_loop_attempt(
                effect=effect,
                config=config,
                controller=_main_phase_loop_controller(
                    config=config,
                    policy_bundle=policy_bundle,
                    deps=deps,
                    state=state,
                    pipeline_deps=pipeline_deps,
                ),
                deps=deps,
                state=state,
                pipeline_deps=pipeline_deps,
                workspace_scope=workspace_scope,
                display=display,
                display_context=resolved_display_context,
                verbosity=verbosity,
                policy_bundle=policy_bundle,
                run_id=run_id,
            )
            return cast("Event", result.event)
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect_from_deps(
            effect, pipeline_deps, workspace_scope, display, verbosity, config=config
        )
    if isinstance(effect, (CommitReviewRejectEffect, EmptyCommitEffect)):
        return _execute_commit_auxiliary_effect(effect, workspace_scope)
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


def _execute_commit_auxiliary_effect(
    effect: CommitReviewRejectEffect | EmptyCommitEffect,
    workspace_scope: WorkspaceScope,
) -> Event | CommitReviewEvent:
    if isinstance(effect, CommitReviewRejectEffect):
        logger.warning(
            "Rejecting proposal for phase '{}' on deterministic Stage 1 tripwire: {}",
            effect.phase,
            effect.reason,
        )
        return CommitReviewRejected(
            reason=effect.reason,
            ref=effect.ref,
            hard=effect.hard,
        )
    return _execute_empty_commit_effect(effect, workspace_scope)


def _execute_empty_commit_effect(
    effect: EmptyCommitEffect,
    workspace_scope: WorkspaceScope,
) -> PipelineEvent:
    match effect.phase_role:
        case "commit_review":
            logger.info("Completing empty commit review without invoking an agent")
            return PipelineEvent.AGENT_SUCCESS
        case "commit":
            logger.info("Completing empty commit without invoking a commit agent")
            _cleanup_commit_message_artifacts(workspace_scope.root)
            return PipelineEvent.COMMIT_SUCCESS


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
    run_id: str | None = None,
) -> Event | CommitReviewEvent:
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
        "run_id": run_id,
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
    run_id: str | None = None,
) -> Event | CommitReviewEvent:
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
        run_id=run_id,
    )


def run_visual_capture_lifecycle(
    *,
    lifecycle: CaptureLifecycle,
    target: str,
    matrix_key: str,
    design_capture_command: str,
    capture: Callable[[], CaptureSet],
    invoke_agent: Callable[[], Event | CommitReviewEvent],
) -> tuple[Event | CommitReviewEvent, CaptureSet, CaptureSet]:
    before = lifecycle.get_retained_before_set(target=target, matrix_key=matrix_key)
    if before is None:
        before = capture()
        lifecycle.capture_before_set(
            target=target,
            capture_set=before,
            matrix_key=matrix_key,
            design_capture_command=design_capture_command,
        )
    agent_result = invoke_agent()
    after = capture()
    if after.run_id == before.run_id:
        raise ValueError("visual after capture must be fresh, not the retained baseline run")
    if after.target != before.target or after.cell_ids != before.cell_ids:
        raise ValueError("visual after capture must have identical matrix coverage to before")
    return agent_result, before, after


def _resolve_policy_capture_plan(
    workspace_root: Path,
) -> tuple[PolicyFacts, CaptureRequest, str] | str:
    from ralph.visual.capture_lifecycle import compute_matrix_key
    from ralph.visual.capture_request import CaptureRequest
    from ralph.visual.policy_facts import DESIGN_SYSTEM_POLICY_RELPATH, parse_policy_facts

    try:
        facts = parse_policy_facts(
            (workspace_root / DESIGN_SYSTEM_POLICY_RELPATH).read_text(encoding="utf-8")
        )
        request = CaptureRequest.build(
            target=facts.target,
            viewports=facts.viewports,
            themes=facts.themes,
            states=facts.states,
        )
        matrix_key = compute_matrix_key(
            viewports=facts.viewports, themes=facts.themes, states=facts.states
        )
    except (OSError, ValueError) as exc:
        return str(exc)
    return facts, request, matrix_key


def _run_policy_declared_visual_capture(
    *,
    workspace_root: Path,
    run_id: str | None,
    cycle_id: str,
    invoke_agent: Callable[[], Event | CommitReviewEvent],
    env_getter: EnvGetter = os.environ.get,
) -> Event | CommitReviewEvent:
    if run_id is None:
        logger.warning("visual-review blocked: development invocation has no run id")
        return invoke_agent()
    secret = env_getter("RALPH_BROKER_SECRET")
    if secret is None:
        logger.warning("visual-review blocked: no broker secret for ledger-backed capture")
        return invoke_agent()
    from ralph.mcp.server._wire_ledger import wire_evidence_for
    from ralph.mcp.tools.workspace._media_capture import (
        MediaCaptureError,
        handle_media_capture,
    )

    plan = _resolve_policy_capture_plan(workspace_root)
    if isinstance(plan, str):
        logger.warning("visual-review blocked: no policy-declared web capture: {}", plan)
        return invoke_agent()
    facts, request, matrix_key = plan

    def capture() -> CaptureSet:
        capture_run_id = str(uuid.uuid4())
        result = handle_media_capture(
            workspace_root,
            run_id=capture_run_id,
            capture_request=request,
            design_capture_command=facts.design_capture_command,
            secret=secret,
        )
        if not wire_evidence_for(
            workspace_root,
            capture_run_id,
            tool_name="media_capture",
            secret=secret,
        ):
            raise MediaCaptureError(
                target=facts.target,
                cell_id="",
                reason="capture completed without authentic wire-ledger provenance",
            )
        return CaptureSet(
            target=result.target,
            cells=tuple(cell.cell for cell in result.cells),
            run_id=capture_run_id,
        )

    agent_result: Event | CommitReviewEvent | None = None

    def invoke_and_remember() -> Event | CommitReviewEvent:
        nonlocal agent_result
        agent_result = invoke_agent()
        return agent_result

    try:
        result, _before, _after = run_visual_capture_lifecycle(
            lifecycle=CaptureLifecycle(workspace_root, run_id=run_id, cycle_id=cycle_id),
            target=facts.target,
            matrix_key=matrix_key,
            design_capture_command=facts.design_capture_command,
            capture=capture,
            invoke_agent=invoke_and_remember,
        )
    except (MediaCaptureError, ValueError) as exc:
        logger.warning("visual-review blocked: {}", exc)
        if agent_result is not None:
            return agent_result
        return invoke_agent()
    return result


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
    pre_workspace: object | None = None,
    pre_phase_role: str | None = None,
    pre_phase_drain: str | None = None,
    run_id: str | None = None,
) -> Event | CommitReviewEvent:
    from ralph.mcp.explore.lifecycle import (
        after_agent_refresh,
        before_agent_refresh,
        is_execution_phase_for_refresh,
    )

    is_agent = isinstance(effect, InvokeAgentEffect)
    should_refresh = is_agent and is_execution_phase_for_refresh(
        phase_role=pre_phase_role,
        phase_drain=pre_phase_drain,
    )

    if should_refresh:
        pre_workspace_obj: object = pre_workspace
        before_index: object = getattr(pre_workspace_obj, "explore_index", None)
        try:
            before_agent_refresh(
                workspace_root=workspace_scope.root,
                explore_index=before_index,
            )
        except Exception as exc:
            logger.debug("before_agent_refresh skipped: %s", exc)

    def invoke_agent() -> Event | CommitReviewEvent:
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
            run_id=run_id,
        )

    try:
        if should_refresh:
            return _run_policy_declared_visual_capture(
                workspace_root=workspace_scope.root,
                run_id=run_id,
                cycle_id=pre_phase_drain or "development",
                invoke_agent=invoke_agent,
            )
        return invoke_agent()
    finally:
        if should_refresh:
            after_index: object = getattr(pre_workspace_obj, "explore_index", None)
            try:
                after_agent_refresh(
                    workspace_root=workspace_scope.root,
                    explore_index=after_index,
                )
            except Exception as exc:
                logger.debug("after_agent_refresh skipped: %s", exc)


def _reduce_runtime_recovery(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    *,
    reason: str,
    recovery: RecoveryController | None = None,
    exc: BaseException | None = None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    expired = redirect_expired_cycle_in_place(state, pipeline_policy, routing_timing)
    if expired is not None:
        return expired
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
    recovered_state, effects = reducer_reduce(
        state, failure_event, pipeline_policy, recovery=None, routing_timing=routing_timing
    )
    return recovered_state, effects


def checkpoint_path_for_root(root: Path) -> Path:
    return root / "checkpoint.json"


def rollback_checkpoint_path_for_root(root: Path) -> Path:
    return root / ".agent" / "checkpoint.json"


def _checkpoint_path(workspace_scope: WorkspaceScope) -> Path:
    return checkpoint_path_for_root(workspace_scope.root)


def _save_checkpoint_or_log(
    state: PipelineState,
    *,
    message: str,
    path: Path,
) -> bool:
    try:
        ckpt.save(state, path)
    except Exception as exc:
        logger.exception(message, phase=state.phase, err=exc)
        return False
    return True


def _maybe_clear_invoke_agent_entry_drains(
    effect: Effect,
    state: PipelineState,
    workspace: FsWorkspace,
    policy_bundle: PolicyBundle,
) -> None:
    if isinstance(effect, InvokeAgentEffect):
        is_resume = (
            effect.phase == state.phase
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


_PHASE_SUCCESS_EVENTS: frozenset[PipelineEvent] = frozenset(
    {
        PipelineEvent.AGENT_SUCCESS,
        PipelineEvent.COMMIT_SUCCESS,
        PipelineEvent.COMMIT_RESIDUAL,
        PipelineEvent.ANALYSIS_SUCCESS,
        PipelineEvent.REVIEW_CLEAN,
        PipelineEvent.FIX_SUCCESS,
    }
)


def _coarse_outcome_for_event(event: Event | CommitReviewEvent) -> str:
    if event == PipelineEvent.COMMIT_SKIPPED:
        return "skipped"
    if isinstance(event, CommitResidualEvent):
        return "success"
    if isinstance(event, PipelineEvent) and event in _PHASE_SUCCESS_EVENTS:
        return "success"
    return "failure"


def _log_auto_integrate_outcome(display: ParallelDisplay, outcome: RebaseState) -> None:
    message = format_auto_integrate_message(
        outcome.last_action,
        outcome.last_target,
        outcome.last_reason,
        fast_forwarded=outcome.fast_forwarded,
        refresh=outcome.last_refresh,
        push=outcome.last_push,
        remote_sync=outcome.last_remote_sync,
        remote=outcome.last_remote,
        freshness_verdict=outcome.freshness_verdict,
    )
    if outcome.is_skipped() or outcome.last_outcome == RebaseOutcomeKind.CONFLICT:
        display.emit_warn_line("run", "auto-integrate", message)
        return
    if outcome.is_rebasing_or_merging() and not outcome.fast_forwarded and outcome.last_reason:
        display.emit_warn_line("run", "auto-integrate", message)
        return
    emit_activity_line(display, None, f"[cyan]auto-integrate:[/cyan] {message}")


def _assert_integration_dispatch_invariant(
    state: PipelineState,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
) -> None:
    del config
    if state.phase == RESOLUTION_DRAIN:
        return
    verdict = inspect_integration_resolution(workspace_scope.root, state.rebase)
    assert_non_resolution_dispatch_allowed(state.phase, verdict)


def _inline_event_for_effect(effect: object) -> PipelineEvent | None:
    if isinstance(effect, SaveCheckpointEffect):
        return PipelineEvent.CHECKPOINT_SAVED
    if isinstance(effect, PreparePromptEffect):
        return PipelineEvent.PROMPT_PREPARED
    if isinstance(effect, ExitSuccessEffect):
        return PipelineEvent.COMPLETE
    if isinstance(effect, ExitFailureEffect):
        return PipelineEvent.FAILED
    if isinstance(effect, ExhaustedAnalysisPhaseAdvanceEffect):
        return PipelineEvent.PHASE_ADVANCE
    return None


def _integrate_inline_effect(
    *,
    effect: Effect,
    inline_result: PipelineState | int,
    state: PipelineState,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    pipeline_deps: PipelineDeps | None,
    display_context: DisplayContext | None,
) -> PipelineState | int:
    inline_event = _inline_event_for_effect(effect)
    if inline_event is None:
        return inline_result
    try:
        outcome = _integrate_on_phase_transition(
            event=inline_event,
            config=config,
            workspace_scope=workspace_scope,
            state=state,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )
    except Exception as inline_exc:
        logger.warning(
            "auto_integrate inline-effect boundary raised: {}",
            inline_exc,
        )
    effective_rebase = outcome if outcome is not None else state.rebase
    if isinstance(effect, ExitSuccessEffect) and not is_terminal_success_eligible(
        state, read_record(workspace_scope.root), effective_rebase
    ):
        return 1
    if outcome is not None and outcome.recovery_record_retained:
        inline_result = inline_result if isinstance(inline_result, PipelineState) else state
    elif outcome is not None and outcome.integration_unresolved is True:
        if isinstance(inline_result, PipelineState):
            failed_state, _ = reducer_reduce(
                state,
                _integration_conflict_failure(state, outcome),
                policy_bundle.pipeline if policy_bundle is not None else None,
            )
            return failed_state.copy_with(
                rebase=_merge_strategy_onto_outcome(failed_state.rebase, outcome)
            )
        return 1
    if outcome is not None and isinstance(inline_result, PipelineState):
        return inline_result.copy_with(
            rebase=_reconcile_rebase_if_live_resolved(workspace_scope, outcome)
        )
    return inline_result


def _merge_strategy_onto_outcome(reducer_rebase: RebaseState, outcome: RebaseState) -> RebaseState:
    return outcome.project_conflict_strategy_from(reducer_rebase)


def _reconcile_rebase_if_live_resolved(
    workspace_scope: WorkspaceScope,
    rebase: RebaseState,
) -> RebaseState:
    if not isinstance(cast("object", rebase), RebaseState):
        return rebase
    if not (
        rebase.integration_unresolved
        or rebase.resolution_exhausted
        or rebase.recovery_record_retained
    ):
        return rebase
    return (
        reconcile_stale_unresolved_state(rebase)
        if inspect_integration_resolution(workspace_scope.root, rebase).dispatch_allowed
        else rebase
    )


def _prepare_pipeline_step_dispatch(
    state: PipelineState,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    recovery_controller: RecoveryController | None,
    pipeline_deps: PipelineDeps | None,
) -> tuple[PipelineState, Effect]:
    rebase = _reconcile_rebase_if_live_resolved(workspace_scope, state.rebase)
    if rebase is not state.rebase:
        state = state.copy_with(rebase=rebase)
    _assert_integration_dispatch_invariant(state, workspace_scope, config)
    effect = call_determine_effect_from_policy(
        state,
        policy_bundle,
        workspace_scope,
        config,
        recovery=recovery_controller,
        pipeline_deps=pipeline_deps,
    )
    if isinstance(effect, InvokeAgentEffect):
        chain = state.chain_for_phase(effect.phase)
        if chain is not None and effect.agent_name in chain.agents:
            index = chain.agents.index(effect.agent_name)
            if index != chain.current_index:
                state = state.with_phase_chain(
                    effect.phase,
                    AgentChainState(agents=chain.agents, current_index=index, retries=0),
                ).copy_with(
                    last_agent_session_id=None,
                    agent_retry_intent=cleared_agent_retry_intent(),
                )
    return state, effect


def _integration_conflict_failure(state: PipelineState, outcome: RebaseState) -> PhaseFailureEvent:
    from ralph.recovery.failure_category import FailureCategory

    reason = outcome.last_reason or "the conflict resolver did not produce a resolution"
    return PhaseFailureEvent(
        phase=state.phase,
        reason=f"integration conflict requires resolution: {reason}",
        recoverable=True,
        failure_category=FailureCategory.INTEGRATION,
    )


def _maybe_auto_integrate(
    *,
    effect: object,
    event: object,
    commit_phase_def: PhaseDefinition | None,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: PipelineState,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle | None = None,
    registry: _RegistryLike | None = None,
    pipeline_deps: PipelineDeps | None = None,
    display_context: DisplayContext | None = None,
) -> RebaseState | None:
    if not (
        isinstance(effect, (CommitEffect, EmptyCommitEffect))
        and commit_phase_def is not None
        and commit_phase_def.role == "commit"
        and event in (PipelineEvent.COMMIT_SUCCESS, PipelineEvent.COMMIT_SKIPPED)
    ):
        return _integrate_on_phase_transition(
            event=event,
            config=config,
            workspace_scope=workspace_scope,
            state=state,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )
    clear_cycle_baseline(workspace_scope.root)
    if isinstance(effect, EmptyCommitEffect):
        return _integrate_on_phase_transition(
            event=PipelineEvent.COMMIT_SKIPPED,
            config=config,
            workspace_scope=workspace_scope,
            state=state,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )
    if event != PipelineEvent.COMMIT_SUCCESS:
        return _integrate_on_phase_transition(
            event=event,
            config=config,
            workspace_scope=workspace_scope,
            state=state,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )
    if pipeline_deps is not None and pipeline_deps.auto_integrate_resolver is not None:
        return pipeline_deps.auto_integrate_resolver(config, workspace_scope, state.rebase)
    conflict_resolver = _build_seam_conflict_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        strategy_history=(
            state.rebase.conflict_strategies_tried
            if state.rebase.conflict_strategy_index == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
            else ()
        ),
    )
    try:
        with cycle_deadline_suspended():
            outcome = auto_integrate_after_commit(
                config,
                workspace_scope,
                state.rebase,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=_build_seam_rebase_stop_resolver(
                    policy_bundle=policy_bundle,
                    registry=registry,
                    display=display,
                    config=config,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=workspace_scope,
                    display_context=display_context,
                    strategy_history=(
                        state.rebase.conflict_strategies_tried
                        if state.rebase.conflict_strategy_index
                        == HISTORY_AWARE_CONFLICT_STRATEGY_INDEX
                        else ()
                    ),
                ),
                display=display,
            )
    except Exception as auto_integrate_exc:
        logger.warning(
            "auto_integrate_after_commit raised unexpectedly: {}",
            auto_integrate_exc,
        )
        return None
    if outcome is not None:
        _log_auto_integrate_outcome(display, outcome)
    return outcome


_PHASE_TRANSITION_INTEGRATION_EVENTS = frozenset(
    {
        PipelineEvent.AGENT_SUCCESS,
        PipelineEvent.AGENT_FAILURE,
        PipelineEvent.AGENT_RETRY,
        PipelineEvent.ANALYSIS_SUCCESS,
        PipelineEvent.ANALYSIS_LOOPBACK,
        PipelineEvent.PHASE_LOOPBACK,
        PipelineEvent.PHASE_ADVANCE,
        PipelineEvent.REVIEW_CLEAN,
        PipelineEvent.REVIEW_ISSUES_FOUND,
        PipelineEvent.FIX_SUCCESS,
        PipelineEvent.FIX_FAILURE,
        PipelineEvent.COMMIT_RESIDUAL,
        PipelineEvent.COMMIT_SKIPPED,
        PipelineEvent.COMMIT_FAILURE,
        PipelineEvent.CHECKPOINT_SAVED,
        PipelineEvent.CONTEXT_CLEANED,
        PipelineEvent.INTERRUPTED,
        PipelineEvent.PROMPT_PREPARED,
        PipelineEvent.FAN_OUT_STARTED,
        PipelineEvent.WORKERS_RESUMED,
        PipelineEvent.ALL_WORKERS_COMPLETE,
        PipelineEvent.COMPLETE,
        PipelineEvent.FAILED,
    }
)


def _is_phase_transition_integration_event(event: object) -> bool:
    return isinstance(event, (CommitResidualEvent, AgentFailureEvent)) or (
        isinstance(event, PipelineEvent) and event in _PHASE_TRANSITION_INTEGRATION_EVENTS
    )


def _build_seam_conflict_resolver(
    *,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
    strategy_history: tuple[str, ...] = (),
) -> ConflictResolver | None:
    if policy_bundle is None or registry is None:
        return None
    return build_agent_conflict_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        strategy_history=strategy_history,
    )


def _build_seam_rebase_stop_resolver(
    *,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
    strategy_history: tuple[str, ...] = (),
) -> RebaseStopResolver | None:
    if policy_bundle is None or registry is None:
        return None
    return build_agent_rebase_stop_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
        strategy_history=strategy_history,
    )


def _integrate_on_phase_transition(
    *,
    event: object,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: PipelineState,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    pipeline_deps: PipelineDeps | None = None,
    display_context: DisplayContext | None = None,
) -> RebaseState | None:
    if not _is_phase_transition_integration_event(event):
        return None
    if pipeline_deps is not None and pipeline_deps.auto_integrate_resolver is not None:
        return pipeline_deps.auto_integrate_resolver(config, workspace_scope, state.rebase)
    conflict_resolver = _build_seam_conflict_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
    )
    try:
        with cycle_deadline_suspended():
            outcome = auto_integrate_on_phase_transition(
                config,
                workspace_scope,
                state.rebase,
                conflict_resolver=conflict_resolver,
                rebase_stop_resolver=_build_seam_rebase_stop_resolver(
                    policy_bundle=policy_bundle,
                    registry=registry,
                    display=display,
                    config=config,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=workspace_scope,
                    display_context=display_context,
                ),
                display=display,
            )
    except Exception as transition_exc:
        logger.warning(
            "auto_integrate_on_phase_transition raised unexpectedly: {}",
            transition_exc,
        )
        return None
    if outcome is not None:
        _log_auto_integrate_outcome(display, outcome)
    return outcome


def _integrate_after_fan_out(
    *,
    state: PipelineState,
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    pipeline_deps: PipelineDeps | None = None,
    display_context: DisplayContext | None = None,
) -> PipelineState:
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    if not bool(enabled_raw):
        return state
    outcome = _integrate_on_phase_transition(
        event=PipelineEvent.ALL_WORKERS_COMPLETE,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=display,
        policy_bundle=policy_bundle,
        registry=registry,
        pipeline_deps=pipeline_deps,
        display_context=display_context,
    )
    if outcome is None:
        return state
    if outcome.integration_unresolved is True and not outcome.recovery_record_retained:
        failed_state, _ = reducer_reduce(
            state,
            _integration_conflict_failure(state, outcome),
            policy_bundle.pipeline if policy_bundle is not None else None,
        )
        return failed_state.copy_with(
            rebase=_merge_strategy_onto_outcome(failed_state.rebase, outcome)
        )
    return state.copy_with(rebase=_reconcile_rebase_if_live_resolved(workspace_scope, outcome))


def _finalize_agent_invocation(
    *,
    effect: Effect,
    event: Event | CommitReviewEvent,
    state: PipelineState,
    config: UnifiedConfig,
    policy_bundle: PolicyBundle,
    workspace: FsWorkspace,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    verbosity: Verbosity,
    recovery_controller: RecoveryController | None,
    run_id: str | None,
) -> tuple[PipelineState, Event | CommitReviewEvent]:
    if not isinstance(effect, InvokeAgentEffect):
        return state, event
    state = _apply_session_capture(state)
    if event == PipelineEvent.AGENT_SUCCESS:
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
            run_id=run_id,
            workspace_probe=(
                recovery_controller.workspace_probe if recovery_controller is not None else None
            ),
        )
    else:
        render_phase_failure_report(
            effect,
            policy_bundle=policy_bundle,
            workspace=workspace,
            display=display,
            display_context=display_context,
            verbosity=verbosity,
            run_id=run_id,
            config=config,
        )
    return state, event


def _sample_cycle_timing(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    pipeline_deps: PipelineDeps | None,
    cycle_sample_box: list[float | None] | None,
) -> tuple[RoutingTiming | None, float | None, float, CycleTimeboxPolicy | None]:
    ct_policy = pipeline_policy.cycle_timebox
    if cycle_sample_box is None or ct_policy is None:
        return None, None, 0.0, ct_policy
    monotonic_fn = (
        pipeline_deps.monotonic
        if pipeline_deps is not None and pipeline_deps.monotonic is not None
        else time.monotonic
    )
    cycle_now = monotonic_fn()
    epoch_fn = pipeline_deps.wall_time if pipeline_deps is not None else time.time
    current_epoch = epoch_fn()
    last_sample = cycle_sample_box[0]
    if state.cycle_timebox_active is True and state.cycle_timebox_started_at_epoch is not None:
        total_elapsed = max(
            state.cycle_timebox_consumed_seconds,
            current_epoch - state.cycle_timebox_started_at_epoch,
        )
        cycle_delta = total_elapsed - state.cycle_timebox_consumed_seconds
    else:
        cycle_delta = (
            max(0.0, cycle_now - last_sample)
            if state.cycle_timebox_active is True and last_sample is not None
            else 0.0
        )
        total_elapsed = state.cycle_timebox_consumed_seconds + cycle_delta
    routing_timing = RoutingTiming(
        total_elapsed_seconds=total_elapsed,
        current_epoch=current_epoch,
    )
    return routing_timing, cycle_now, cycle_delta, ct_policy


def _sample_development_timing(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    pipeline_deps: PipelineDeps | None,
    sample_box: list[float | None] | None,
) -> tuple[RoutingTiming | None, float | None, float, DevelopmentTimeboxPolicy | None]:
    policy = pipeline_policy.development_timebox
    if sample_box is None or policy is None:
        return None, None, 0.0, policy
    monotonic_fn = (
        pipeline_deps.monotonic
        if pipeline_deps is not None and pipeline_deps.monotonic is not None
        else time.monotonic
    )
    now = monotonic_fn()
    epoch_fn = pipeline_deps.wall_time if pipeline_deps is not None else time.time
    current_epoch = epoch_fn()
    last_sample = sample_box[0]
    if state.dev_timebox_active is True and state.dev_timebox_started_at_epoch is not None:
        total_elapsed = max(
            state.dev_timebox_consumed_seconds,
            current_epoch - state.dev_timebox_started_at_epoch,
        )
        delta = total_elapsed - state.dev_timebox_consumed_seconds
    else:
        delta = (
            max(0.0, now - last_sample)
            if state.dev_timebox_active is True and last_sample is not None
            else 0.0
        )
        total_elapsed = state.dev_timebox_consumed_seconds + delta
    return (
        RoutingTiming(
            total_elapsed_seconds=total_elapsed,
            development_elapsed_seconds=total_elapsed,
            current_epoch=current_epoch,
        ),
        now,
        delta,
        policy,
    )


def _merge_routing_timings(
    cycle_timing: RoutingTiming | None, development_timing: RoutingTiming | None
) -> RoutingTiming | None:
    if cycle_timing is None:
        return development_timing
    if development_timing is None:
        return cycle_timing
    return RoutingTiming(
        total_elapsed_seconds=cycle_timing.total_elapsed_seconds,
        development_elapsed_seconds=development_timing.total_elapsed_seconds,
        current_epoch=cycle_timing.current_epoch or development_timing.current_epoch,
    )


def _sample_step_routing_timing(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    pipeline_deps: PipelineDeps | None,
    cycle_sample_box: list[float | None] | None,
    development_sample_box: list[float | None] | None,
) -> tuple[
    PipelineState,
    RoutingTiming | None,
    RoutingTiming | None,
    RoutingTiming | None,
    CycleTimeboxPolicy | None,
    DevelopmentTimeboxPolicy | None,
]:
    cycle_timing, cycle_now, cycle_delta, cycle_policy = _sample_cycle_timing(
        state, pipeline_policy, pipeline_deps, cycle_sample_box
    )
    development_timing, development_now, development_delta, development_policy = (
        _sample_development_timing(state, pipeline_policy, pipeline_deps, development_sample_box)
    )
    sampled_state = _fold_sampled_timeboxes(
        state,
        state,
        cycle_delta=cycle_delta,
        cycle_now=cycle_now,
        cycle_sample_box=cycle_sample_box,
        cycle_policy=cycle_policy,
        development_delta=development_delta,
        development_now=development_now,
        development_sample_box=development_sample_box,
        development_policy=development_policy,
    )
    return (
        sampled_state,
        cycle_timing,
        development_timing,
        _merge_routing_timings(cycle_timing, development_timing),
        cycle_policy,
        development_policy,
    )


def _prepare_step_timeboxes(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    pipeline_deps: PipelineDeps | None,
    cycle_sample_box: list[float | None] | None,
    development_sample_box: list[float | None] | None,
) -> tuple[
    PipelineState,
    RoutingTiming | None,
    RoutingTiming | None,
    RoutingTiming | None,
    CycleTimeboxPolicy | None,
    DevelopmentTimeboxPolicy | None,
]:
    prepared = _sample_step_routing_timing(
        state,
        pipeline_policy,
        pipeline_deps,
        cycle_sample_box,
        development_sample_box,
    )
    state, cycle_timing, development_timing, effective_timing, cycle_policy, dev_policy = prepared
    expired = redirect_expired_cycle_in_place(state, pipeline_policy, effective_timing)
    if expired is not None:
        state, _ = expired
    return (
        state,
        cycle_timing,
        development_timing,
        effective_timing,
        cycle_policy,
        dev_policy,
    )


def _fold_development_elapsed(
    before: PipelineState,
    after: PipelineState,
    *,
    delta_seconds: float,
    timing_enabled: bool,
) -> PipelineState:
    if (
        not timing_enabled
        or not before.dev_timebox_active
        or not after.dev_timebox_active
        or delta_seconds <= 0.0
    ):
        return after
    return after.model_copy(
        update={"dev_timebox_consumed_seconds": after.dev_timebox_consumed_seconds + delta_seconds}
    )


def _fold_sampled_timeboxes(
    before: PipelineState,
    after: PipelineState,
    *,
    cycle_delta: float,
    cycle_now: float | None,
    cycle_sample_box: list[float | None] | None,
    cycle_policy: CycleTimeboxPolicy | None,
    development_delta: float,
    development_now: float | None,
    development_sample_box: list[float | None] | None,
    development_policy: DevelopmentTimeboxPolicy | None,
) -> PipelineState:
    after = fold_cycle_elapsed(
        before,
        after,
        delta_seconds=cycle_delta,
        timing_enabled=cycle_sample_box is not None and cycle_policy is not None,
    )
    if cycle_sample_box is not None and cycle_policy is not None and cycle_now is not None:
        cycle_sample_box[0] = cycle_now
    after = _fold_development_elapsed(
        before,
        after,
        delta_seconds=development_delta,
        timing_enabled=development_sample_box is not None and development_policy is not None,
    )
    if (
        development_sample_box is not None
        and development_policy is not None
        and development_now is not None
    ):
        development_sample_box[0] = development_now
    return after


def _fold_cycle_elapsed(
    before: PipelineState,
    after: PipelineState,
    *,
    delta_seconds: float,
    timing_enabled: bool,
) -> PipelineState:
    if not timing_enabled or not before.cycle_timebox_active or delta_seconds <= 0.0:
        return after
    return after.model_copy(
        update={
            "cycle_timebox_consumed_seconds": (
                after.cycle_timebox_consumed_seconds + delta_seconds
            ),
        }
    )


def _run_fan_out_phase(
    *,
    effect: FanOutEffect,
    state: PipelineState,
    display: ParallelDisplay,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    pipeline_subscriber: object,
    config: UnifiedConfig,
    config_path: Path | None,
    cli_overrides: Mapping[str, object] | None,
    monitor_stop_cb: Callable[[], None] | None,
    pipeline_deps: PipelineDeps | None,
    registry: _RegistryLike,
    display_context: DisplayContext,
    routing_timing: RoutingTiming | None,
) -> PipelineState:
    _publish_fan_out_cycle_deadline(state, effect.phase, policy_bundle, routing_timing)

    def integrate_after_successful_fan_out(finished_state: PipelineState) -> PipelineState:
        withdraw_cycle_deadline_env()
        return _integrate_after_fan_out(
            state=finished_state,
            config=config,
            workspace_scope=workspace_scope,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )

    try:
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
            _monitor_stop_cb=monitor_stop_cb,
            pipeline_deps=pipeline_deps,
            _on_successful_completion=integrate_after_successful_fan_out,
        )
    finally:
        withdraw_cycle_deadline_env()


def _publish_fan_out_cycle_deadline(
    state: PipelineState,
    phase: str,
    policy_bundle: PolicyBundle,
    routing_timing: RoutingTiming | None,
) -> None:
    publish_cycle_deadline_env(
        state,
        phase,
        policy_bundle,
        routing_timing.total_elapsed_seconds if routing_timing is not None else None,
        routing_timing.development_elapsed_seconds if routing_timing is not None else None,
    )


def _start_phase_telemetry(
    state: PipelineState, pipeline_policy: PipelinePolicy
) -> tuple[str, str | None, PhaseTimer]:
    phase_def = pipeline_policy.phases.get(state.phase)
    role = phase_def.role if phase_def is not None and phase_def.role is not None else "execution"
    drain = phase_def.drain if phase_def is not None else None
    return role, drain, PhaseTimer.start(state.phase)


_KNOWN_INTEGRATION_ACTIONS: frozenset[IntegrationAction] = frozenset(IntegrationAction)


def _apply_step_integration_outcome(
    next_state: PipelineState,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    auto_integrate_outcome: RebaseState | None,
    config: UnifiedConfig | None = None,
) -> PipelineState:
    if auto_integrate_outcome is None:
        return next_state
    reconciled_rebase = _reconcile_rebase_if_live_resolved(workspace_scope, auto_integrate_outcome)
    record = read_record(workspace_scope.root)
    if record is not None or reconciled_rebase.recovery_record_retained:
        target = config.general.auto_integrate_target if config is not None else None
        remote = config.general.auto_integrate_remote if config is not None else None
        action = decide_seam_action(
            workspace_scope.root,
            record=record,
            retained=reconciled_rebase.recovery_record_retained,
            target=target,
            remote=remote,
        )
        decision = decide_phase_transition(
            next_state,
            policy_bundle.pipeline,
            integration_record=record,
            rebase_state=reconciled_rebase,
        )
        if isinstance(action, RecordFactOutcome):
            reconciled_rebase = reconciled_rebase.set_operation_display_state(
                OperationDisplayState.WAITING
            )
        elif action not in _KNOWN_INTEGRATION_ACTIONS:
            raise ValueError(f"Unknown integration action: {action!r}")
        if decision not in (
            PhaseDecision.CONTINUE_PHASE,
            PhaseDecision.ADVANCE_PHASE,
            PhaseDecision.BLOCK_FOR_RECOVERY,
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
            PhaseDecision.REPORT_TERMINAL_FAILURE,
            PhaseDecision.REPORT_INTERVENTION_REQUIRED,
        ):
            raise ValueError(f"Unknown phase decision: {decision!r}")
        if (
            decision
            in (
                PhaseDecision.BLOCK_FOR_RECOVERY,
                PhaseDecision.REPORT_INTERVENTION_REQUIRED,
            )
            and reconciled_rebase.operation_display_state != "intervention_required"
        ):
            reconciled_rebase = reconciled_rebase.set_operation_display_state(
                OperationDisplayState.WAITING
            )
    return next_state.copy_with(rebase=reconciled_rebase)


def _handle_blocked_dispatch_recovery(
    recovered_state: PipelineState,
    timed_state: PipelineState,
    workspace_scope: WorkspaceScope,
    pipeline_policy: PipelinePolicy,
    exc: BaseException,
    config: UnifiedConfig | None = None,
) -> PipelineState:
    if not isinstance(exc, IntegrationDispatchBlockedError):
        return recovered_state
    record = read_record(workspace_scope.root)
    target = config.general.auto_integrate_target if config is not None else None
    remote = config.general.auto_integrate_remote if config is not None else None
    action = decide_seam_action(
        workspace_scope.root,
        record=record,
        retained=timed_state.rebase.recovery_record_retained,
        target=target,
        remote=remote,
    )
    decision = decide_phase_transition(
        timed_state,
        pipeline_policy,
        integration_record=record,
        rebase_state=timed_state.rebase,
    )
    if decision is PhaseDecision.REPORT_TERMINAL_SUCCESS and not (
        action
        in (
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            IntegrationAction.CONTINUE_GIT_OPERATION,
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
            IntegrationAction.LAND_TARGET,
        )
        or isinstance(action, RecordFactOutcome)
    ):
        return recovered_state
    return recovered_state.copy_with(
        rebase=recovered_state.rebase.set_recovery_record_retained(
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
    )


def _commit_review_phase_for_gate_failure(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    previous = state.previous_phase
    if isinstance(previous, str):
        phase_def = pipeline_policy.phases.get(previous)
        if phase_def is not None and phase_def.role == "commit_review":
            return previous
    return state.phase


def rollback_gate_failure_state(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
    detail: str,
) -> PipelineState:
    gate_state = state.copy_with(
        phase=_commit_review_phase_for_gate_failure(state, pipeline_policy),
        rollback_pending=False,
        last_agent_session_id=None,
        agent_retry_intent=cleared_agent_retry_intent(),
    )
    failed_state, _ = reducer_reduce(
        gate_state,
        CommitReviewGateFailed(detail=detail),
        pipeline_policy,
    )
    return failed_state


def _write_rejection_hint(
    state: PipelineState,
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
) -> None:
    from ralph.phases.required_artifacts import retry_hint_path

    dev_hint_path = workspace_root / retry_hint_path("development", pipeline_policy=pipeline_policy)
    dev_hint_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_note = (
        f"Working tree rolled back to the run-start baseline ({state.rollback_baseline_ref})."
        if state.rollback_baseline_ref
        else "Working tree rolled back to HEAD."
    )
    rejection_text = (
        f"Commit review rejected previous iteration: {state.last_review_rejection_reason or '(no reason provided)'}\n"
        f"Proposal ref: {state.rollback_ref}\n"
        f"{baseline_note} To inspect rejected changes: git checkout {state.rollback_ref} -- .\n"
    )
    dev_hint_path.write_text(rejection_text, encoding="utf-8")


def execute_pending_rollback(
    state: PipelineState,
    workspace_root: Path,
    pipeline_policy: PipelinePolicy,
    *,
    checkpoint_path: Path | None = None,
) -> PipelineState:
    from ralph.git.proposal_seal import rollback

    marker_path = (
        checkpoint_path
        if checkpoint_path is not None
        else rollback_checkpoint_path_for_root(workspace_root)
    )
    rollback_ref = state.rollback_ref
    if not isinstance(rollback_ref, str) or not rollback_ref:
        return state
    if not _save_checkpoint_or_log(
        state,
        message="Checkpoint save failed before rollback in phase={phase}: {err}",
        path=marker_path,
    ):
        return rollback_gate_failure_state(
            state,
            pipeline_policy,
            "Rollback checkpoint save failed: rollback-pending marker was not persisted",
        )
    try:
        rollback(workspace_root, rollback_ref, baseline_ref=state.rollback_baseline_ref)
    except Exception as exc:
        logger.exception(
            "Commit review rollback refused for {ref}: {err}",
            ref=rollback_ref,
            err=exc,
        )
        return rollback_gate_failure_state(
            state,
            pipeline_policy,
            f"Rollback preflight failed: {exc}",
        )
    _write_rejection_hint(state, workspace_root, pipeline_policy)
    cleared = state.copy_with(rollback_pending=False, rollback_ref=None)
    if not _save_checkpoint_or_log(
        cleared,
        message="Checkpoint save failed after rollback in phase={phase}: {err}",
        path=marker_path,
    ):
        return rollback_gate_failure_state(
            state,
            pipeline_policy,
            "Rollback checkpoint save failed: cleared rollback marker was not persisted",
        )
    return cleared


def _execute_pending_rollback_if_needed(
    next_state: PipelineState,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
) -> PipelineState:
    if not (
        isinstance(next_state.rollback_pending, bool)
        and next_state.rollback_pending is True
        and isinstance(next_state.rollback_ref, str)
        and next_state.rollback_ref
    ):
        return next_state
    return execute_pending_rollback(
        next_state,
        workspace_scope.root,
        policy_bundle.pipeline,
    )


def _finalize_step_state(
    next_state: PipelineState,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    auto_integrate_outcome: RebaseState | None,
    config: UnifiedConfig | None,
) -> PipelineState:
    next_state = _execute_pending_rollback_if_needed(next_state, workspace_scope, policy_bundle)
    return _apply_step_integration_outcome(
        next_state, workspace_scope, policy_bundle, auto_integrate_outcome, config=config
    )


def _with_phase_step_timing(result: PipelineState | int, timer: PhaseTimer) -> PipelineState | int:
    return result.with_phase_timing(timer.finish()) if isinstance(result, PipelineState) else result


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
    _cycle_sample_box: list[float | None] | None = None,
    _development_sample_box: list[float | None] | None = None,
) -> PipelineState | int:
    _phase_role, _phase_drain, _phase_timer = _start_phase_telemetry(state, policy_bundle.pipeline)
    _ = determine_effect_from_policy
    _phase_outcome = "crashed"

    (
        state,
        _routing_timing,
        _development_routing_timing,
        _effective_routing_timing,
        ct_policy,
        dt_policy,
    ) = _prepare_step_timeboxes(
        state,
        policy_bundle.pipeline,
        pipeline_deps,
        _cycle_sample_box,
        _development_sample_box,
    )

    try:
        state, effect = _prepare_pipeline_step_dispatch(
            state,
            workspace_scope,
            config,
            policy_bundle,
            recovery_controller,
            pipeline_deps,
        )
        inline_result = handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=policy_bundle.pipeline,
            artifacts_policy=policy_bundle.artifacts,
            agents_policy=policy_bundle.agents,
            registry=registry,
            config=config,
            workspace_scope=workspace_scope,
            pipeline_deps=pipeline_deps,
            display=display,
            pipeline_subscriber=pipeline_subscriber,
            routing_timing=_effective_routing_timing,
            recovery_controller=recovery_controller,
        )
        if inline_result is not None:
            inline_result = _integrate_inline_effect(
                effect=effect,
                inline_result=inline_result,
                state=state,
                config=config,
                workspace_scope=workspace_scope,
                display=display,
                policy_bundle=policy_bundle,
                registry=registry,
                pipeline_deps=pipeline_deps,
                display_context=display_context,
            )
            _phase_outcome = "skipped"
            return _with_phase_step_timing(inline_result, _phase_timer)

        if isinstance(effect, FanOutEffect):
            _phase_outcome = "success"
            return _with_phase_step_timing(
                _run_fan_out_phase(
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
                    registry=registry,
                    display_context=display_context,
                    routing_timing=_effective_routing_timing,
                ),
                _phase_timer,
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
            try:
                _materialize_fn = (
                    pipeline_deps.phase_prompt_materializer if pipeline_deps is not None else None
                )
                materialize_agent_prompt_if_needed(
                    effect,
                    state,
                    workspace,
                    policy_bundle,
                    registry,
                    materialize_fn=_materialize_fn,
                    cycle_total_elapsed=(
                        _routing_timing.total_elapsed_seconds
                        if _routing_timing is not None
                        else None
                    ),
                    development_total_elapsed=(
                        _development_routing_timing.total_elapsed_seconds
                        if _development_routing_timing is not None
                        else None
                    ),
                )
            except MissingPlanHandoffError as exc:
                _phase_outcome = "skipped"
                return _with_phase_step_timing(
                    _recover_missing_plan_handoff(
                        state=state,
                        pipeline_policy=policy_bundle.pipeline,
                        checkpoint_path=_checkpoint_path(workspace_scope),
                        subscriber=pipeline_subscriber,
                        exc=exc,
                    ),
                    _phase_timer,
                )
            run_id = str(uuid.uuid4())
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
                pre_workspace=workspace,
                pre_phase_role=_phase_role,
                pre_phase_drain=_phase_drain,
                run_id=run_id,
            )
            state, event = _finalize_agent_invocation(
                effect=effect,
                event=event,
                state=state,
                config=config,
                policy_bundle=policy_bundle,
                workspace=workspace,
                workspace_scope=workspace_scope,
                display=display,
                display_context=display_context,
                verbosity=verbosity,
                recovery_controller=recovery_controller,
                run_id=run_id,
            )

        _commit_phase_def = policy_bundle.pipeline.phases.get(state.phase)
        _auto_integrate_outcome = _maybe_auto_integrate(
            effect=effect,
            event=event,
            commit_phase_def=_commit_phase_def,
            config=config,
            workspace_scope=workspace_scope,
            state=state,
            display=display,
            policy_bundle=policy_bundle,
            registry=registry,
            pipeline_deps=pipeline_deps,
            display_context=display_context,
        )
        if (
            isinstance(_auto_integrate_outcome, RebaseState)
            and _auto_integrate_outcome.integration_unresolved
            and not _auto_integrate_outcome.recovery_record_retained
        ):
            event = _integration_conflict_failure(state, _auto_integrate_outcome)
        _phase_outcome = _coarse_outcome_for_event(event)
        _reduce_routing_timing, _reduce_now, _reduce_delta, _ = _sample_cycle_timing(
            state, policy_bundle.pipeline, pipeline_deps, _cycle_sample_box
        )
        _reduce_development_timing, _reduce_development_now, _reduce_development_delta, _ = (
            _sample_development_timing(
                state, policy_bundle.pipeline, pipeline_deps, _development_sample_box
            )
        )
        _effective_reduce_timing = _merge_routing_timings(
            _reduce_routing_timing, _reduce_development_timing
        )
        next_state, _ = reducer_reduce(
            state,
            event,
            policy_bundle.pipeline,
            recovery=recovery_controller,
            routing_timing=_effective_reduce_timing,
        )
        next_state = _fold_sampled_timeboxes(
            state,
            next_state,
            cycle_delta=_reduce_delta,
            cycle_now=_reduce_now,
            cycle_sample_box=_cycle_sample_box,
            cycle_policy=ct_policy,
            development_delta=_reduce_development_delta,
            development_now=_reduce_development_now,
            development_sample_box=_development_sample_box,
            development_policy=dt_policy,
        )
        next_state = _finalize_step_state(
            next_state, workspace_scope, policy_bundle, _auto_integrate_outcome, config=config
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
        return _with_phase_step_timing(next_state, _phase_timer)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit) as exc:
        _phase_outcome = "crashed"
        logger.exception(
            "Pipeline step crashed in phase={phase}: {err}",
            phase=state.phase,
            err=exc,
        )
        crash_cycle_timing, crash_cycle_now, crash_cycle_delta, _ = _sample_cycle_timing(
            state, policy_bundle.pipeline, pipeline_deps, _cycle_sample_box
        )
        crash_dev_timing, crash_dev_now, crash_dev_delta, _ = _sample_development_timing(
            state, policy_bundle.pipeline, pipeline_deps, _development_sample_box
        )
        crash_timing = _merge_routing_timings(crash_cycle_timing, crash_dev_timing)
        timed_state = _fold_sampled_timeboxes(
            state,
            state,
            cycle_delta=crash_cycle_delta,
            cycle_now=crash_cycle_now,
            cycle_sample_box=_cycle_sample_box,
            cycle_policy=ct_policy,
            development_delta=crash_dev_delta,
            development_now=crash_dev_now,
            development_sample_box=_development_sample_box,
            development_policy=dt_policy,
        )
        recovered_state, _recv_effects = _reduce_runtime_recovery(
            timed_state,
            policy_bundle.pipeline,
            reason=f"Pipeline step crashed: {type(exc).__name__}: {exc}",
            recovery=recovery_controller,
            exc=exc,
            routing_timing=crash_timing,
        )
        for _eff in _recv_effects:
            if isinstance(_eff, ExitFailureEffect):
                emit_activity_line(
                    display, None, status_text("Recovery exhausted", _eff.reason, "red")
                )
        recovered_state = _handle_blocked_dispatch_recovery(
            recovered_state,
            timed_state,
            workspace_scope,
            policy_bundle.pipeline,
            exc,
            config=config,
        )
        _notify_pipeline_subscriber(pipeline_subscriber, recovered_state)
        _save_checkpoint_or_log(
            recovered_state,
            message="Checkpoint save failed while recording recovery in phase={phase}: {err}",
            path=_checkpoint_path(workspace_scope),
        )
        return _with_phase_step_timing(recovered_state, _phase_timer)
    finally:
        withdraw_cycle_deadline_env()
        with contextlib.suppress(Exception):
            phase_timing = _phase_timer.finish()
            record_phase_execution(
                role=_phase_role,
                duration_s=phase_timing.elapsed_seconds,
                outcome=_phase_outcome,
            )


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
    pipeline_deps: PipelineDeps | None = None,
    display: ParallelDisplay | None = None,
    pipeline_subscriber: _PipelineSubscriber | None = None,
    routing_timing: RoutingTiming | None = None,
    dashboard_subscriber: _PipelineSubscriber | None = None,
    recovery_controller: RecoveryController | None = None,
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
                    pipeline_deps=pipeline_deps,
                )
            except MissingPlanHandoffError as exc:
                recovered_state = _recover_missing_plan_handoff(
                    state=state,
                    pipeline_policy=pipeline_policy,
                    checkpoint_path=checkpoint_path,
                    subscriber=effective_subscriber,
                    exc=exc,
                )
                return recovered_state
        prepared_state = state
        target_phase = effect.phase
        if pipeline_policy.recovery.failed_route == state.phase:
            timeboxed = apply_cycle_timebox(
                state,
                effect.phase,
                policy=pipeline_policy,
                routing_timing=routing_timing
                or RoutingTiming(
                    total_elapsed_seconds=state.cycle_timebox_consumed_seconds,
                    current_epoch=time.time(),
                ),
            )
            development_timeboxed = apply_development_timebox(
                timeboxed.state,
                timeboxed.target_phase,
                policy=pipeline_policy,
                routing_timing=routing_timing
                or RoutingTiming(
                    total_elapsed_seconds=state.cycle_timebox_consumed_seconds,
                    development_elapsed_seconds=state.dev_timebox_consumed_seconds,
                    current_epoch=time.time(),
                ),
            )
            target_phase = development_timeboxed.target_phase
            if timeboxed.redirected:
                logger.bind(component="policy.routing").warning(timeboxed.redirect_reason)
            if development_timeboxed.redirected:
                logger.bind(component="policy.routing").warning(
                    development_timeboxed.redirect_reason
                )
            boundary_state = conclude_cycle_on_route_out_of_cycle(
                development_timeboxed.state,
                target_phase,
                policy=pipeline_policy,
            )
            boundary_state = conclude_development_timebox_on_route_out_of_development(
                boundary_state,
                target_phase,
                policy=pipeline_policy,
            )
            prepared_state = _reset_phase_chain_for_recovery(boundary_state, target_phase)
            target_phase_def = pipeline_policy.phases.get(target_phase)
            if target_phase_def is not None and target_phase_def.role == "commit":
                prepared_state = prepared_state.copy_with(commit=CommitState())
            if target_phase_def is not None and target_phase_def.role == "execution":
                clear_cycle_baseline(workspace_scope.root)
                write_start_commit_if_absent(workspace_scope.root)
        requested_drain = effect.drain if target_phase == effect.phase else None
        prepare_updates: dict[str, object] = {
            "phase": target_phase,
            "current_drain": requested_drain or resolve_phase_drain(target_phase, pipeline_policy),
        }
        if target_phase != prepared_state.phase:
            prepare_updates["last_agent_session_id"] = None
            prepare_updates["agent_retry_intent"] = cleared_agent_retry_intent()
        updated_state = prepared_state.copy_with(**prepare_updates)
        ckpt.save(updated_state, checkpoint_path)
        _notify_pipeline_subscriber(effective_subscriber, updated_state)
        return updated_state

    if isinstance(effect, ExitSuccessEffect):
        record = read_record(workspace_scope.root)
        return (
            _emit_success_exit(display, os.getenv)
            if is_terminal_success_eligible(state, record, state.rebase)
            and (record is not None or retained_integration_reason(workspace_scope.root) is None)
            else 1
        )

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
    show_cta = (hash(str(os.getpid()) + str(getenv("USER") or "")) % 2) == 0
    if show_cta:
        emit_activity_line(display, None, f"[bold yellow]{GITHUB_STAR_CTA}[/bold yellow]")
    return 0


def _call_determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    *,
    recovery: RecoveryController | None = None,
    pipeline_deps: PipelineDeps | None = None,
) -> Effect:
    fn = determine_effect_from_policy
    params = signature(fn).parameters
    if "config" in params:
        has_changes = (
            pipeline_deps.has_uncommitted_changes
            if pipeline_deps is not None and pipeline_deps.has_uncommitted_changes is not None
            else None
        )
        if "has_uncommitted_changes_fn" in params and has_changes is not None:
            if "recovery" in params:
                return fn(
                    state,
                    policy_bundle,
                    workspace_scope,
                    config=config,
                    has_uncommitted_changes_fn=has_changes,
                    recovery=recovery,
                )
            return fn(
                state,
                policy_bundle,
                workspace_scope,
                config=config,
                has_uncommitted_changes_fn=has_changes,
            )
        if "recovery" in params:
            return fn(state, policy_bundle, workspace_scope, config=config, recovery=recovery)
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
    opts.setdefault("_install_signal_handlers", install_signal_handlers)
    opts.setdefault("_executor_cls", SubprocessAgentExecutor)
    opts.setdefault("_mcp_factory_cls", DynamicBindingMcpServerFactory)
    opts.setdefault("_run_process_async", run_process_async)
    opts.setdefault("_reducer_reduce", reducer_reduce)
    return _fan_out_execute_fan_out_sync(
        effect=effect,
        state=state,
        display=display,
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
    return max(40, terminal_width() - prefix_len - 2)


def execute_commit_effect(
    effect: CommitEffect,
    create_commit_fn: _CreateCommitFn,
    stage_all_fn: Callable[[Path | str], None],
    repo_root: Path,
    display: ParallelDisplay | None = None,
    **opts: object,
) -> Event:
    opts.setdefault("has_commit_work_fn", repo_has_commit_work)
    opts.setdefault("has_residual_work_fn", repo_has_commit_work)
    return _ee_execute_commit_effect(
        effect,
        repo_root,
        display,
        create_commit_fn=create_commit_fn,
        stage_all_fn=stage_all_fn,
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
    return _pt_emit_phase_transition_if_changed(
        display,
        previous_phase,
        state,
        verbosity=verbosity,
        pipeline_policy=pipeline_policy,
    )


def write_start_commit_if_absent(workspace_root: Path) -> None:
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
finalize_agent_invocation = _finalize_agent_invocation
fold_cycle_elapsed = _fold_cycle_elapsed
sample_cycle_timing = _sample_cycle_timing
