"""Pipeline runner: orchestration glue that wires extracted submodules together.

This module coordinates effect dispatch, step execution, and policy resolution.
Heavy lifting is delegated to focused submodules; runner.py owns only the
plumbing that connects them.
"""

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
from ralph.config.enums import Verbosity
from ralph.display.auto_integrate_message import format_auto_integrate_message
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
from ralph.phases.timing import PhaseTimer
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
    recover_missing_plan_handoff as _recover_missing_plan_handoff,
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
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
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
from ralph.pipeline.cycle_timing import RoutingTiming, apply_cycle_timebox
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
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.websearch.secrets import EnvGetter
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        CycleTimeboxPolicy,
        PhaseDefinition,
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
    "run_process_async",
    "run_visual_capture_lifecycle",
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


def _execute_commit_effect_from_deps(
    effect: CommitEffect,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    display: ParallelDisplay | None,
    verbosity: Verbosity,
) -> PipelineEvent:
    if pipeline_deps.commit_effect_executor is not None:
        return cast(
            "PipelineEvent",
            pipeline_deps.commit_effect_executor(effect, workspace_scope.root),
        )
    return execute_commit_effect(
        effect, create_commit, stage_all, workspace_scope.root, display, verbosity=verbosity
    )


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
            run_id=run_id,
        )
    if isinstance(effect, CommitEffect):
        return _execute_commit_effect_from_deps(
            effect, pipeline_deps, workspace_scope, display, verbosity
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
    run_id: str | None = None,
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
        "run_id": run_id,
    }
    supported = all_opts if accepts_kwargs else {k: v for k, v in all_opts.items() if k in params}
    return cast("_ExecuteEffectKwargsFn", fn)(
        effect, config, workspace_scope, **supported
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


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
        run_id=run_id,
    )


def run_visual_capture_lifecycle(
    *,
    lifecycle: CaptureLifecycle,
    target: str,
    matrix_key: str,
    design_capture_command: str,
    capture: Callable[[], CaptureSet],
    invoke_agent: Callable[[], Event],
) -> tuple[Event, CaptureSet, CaptureSet]:
    """Capture an immutable baseline, invoke the agent, then capture a fresh matrix.

    The caller supplies the policy-bound capture operation.  A retained baseline
    is reused on retry; the after set must be a new capture run with identical
    matrix coverage before it can be compared by a visual verdict.
    """
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


def _run_policy_declared_visual_capture(
    *,
    workspace_root: Path,
    run_id: str | None,
    cycle_id: str,
    invoke_agent: Callable[[], Event],
    env_getter: EnvGetter = os.environ.get,
) -> Event:
    """Run policy-declared before/after capture around one development invocation.

    An unavailable renderer, absent capture policy, or unsigned session is a
    visible visual-review blocker.  It never manufactures a CaptureSet and it
    does not prevent a non-visual development task from proceeding.
    """
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
        logger.warning("visual-review blocked: no policy-declared web capture: {}", exc)
        return invoke_agent()

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

    agent_result: Event | None = None

    def invoke_and_remember() -> Event:
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
) -> Event:
    # Phase 1 lifecycle hooks: bounded changed-file refresh before
    # and after an InvokeAgentEffect. Hooks skip cleanly when the
    # explore index is disabled or missing; they never block the
    # agent indefinitely (fail-open for the agent, fail-closed for
    # the reindex job).
    #
    # AC-04: the refresh gate is the development / fix phase drain
    # (NOT just the role). The planning block in
    # ``ralph/policy/defaults/pipeline.toml`` is also mapped to
    # ``role = "execution"``; gating on role alone would trigger
    # an uncosted index refresh for the planning agent. The drain
    # is the authoritative identity of a dev/fix session.
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

    def invoke_agent() -> Event:
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
    """Route a crashed step through recovery, bounded by the cycle deadline.

    This path hands the failure straight to the controller, skipping ``reduce``
    and therefore every guard inside it — so the deadline is asked here. With
    the budget already spent the controller's retries would otherwise each be
    another full-length invocation of the guarded phase, counted and reported
    nowhere.
    """
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


_PHASE_SUCCESS_EVENTS: frozenset[PipelineEvent] = frozenset(
    {
        PipelineEvent.AGENT_SUCCESS,
        PipelineEvent.COMMIT_SUCCESS,
        PipelineEvent.ANALYSIS_SUCCESS,
        PipelineEvent.REVIEW_CLEAN,
        PipelineEvent.FIX_SUCCESS,
    }
)


def _coarse_outcome_for_event(event: Event) -> str:
    """Map a post-with-block ``Event`` to a coarse phase-outcome tag.

    Returns one of the closed vocabulary strings forwarded to Sentry:
    ``"success"``, ``"skipped"``, or ``"failure"``. The mapping is
    deliberately fail-safe: ``COMMIT_SKIPPED`` is its own bucket, the
    closed success set covers agent/commit/analysis/review/fix success
    events, and everything else (loopback/retry/failure) is conservatively
    ``"failure"`` so a real failure can never be hidden behind
    ``"success"``. Non-``PipelineEvent`` events (PhaseFailureEvent /
    WorkerStartedEvent / WorkerCompletedEvent / WorkerFailedEvent /
    PostFanoutVerificationEvent / AnalysisDecisionEvent) all map to
    ``"failure"`` because none of them are part of the closed success set.
    """
    if event == PipelineEvent.COMMIT_SKIPPED:
        return "skipped"
    if isinstance(event, PipelineEvent) and event in _PHASE_SUCCESS_EVENTS:
        return "success"
    return "failure"


def _log_auto_integrate_outcome(display: ParallelDisplay, outcome: RebaseState) -> None:
    """Emit the user-facing action log line for an auto-integration outcome.

    Delegates the verb -> phrase mapping to
    :func:`ralph.display.auto_integrate_message.format_auto_integrate_message`
    so the live activity line and the run's final receipt cannot drift.
    The single ``[cyan]auto-integrate:[/cyan]`` prefix is added here;
    the formatter returns a bare phrase.

    Per the prompt Notes the user-facing line should describe what
    actually happened: ``rebased onto target`` / ``merged target into
    feature`` (each optionally followed by ``, fast-forwarded
    <target>`` or ``, fast-forward skipped: <reason>``) /
    ``skipped: <reason>`` / ``conflict: <reason>`` /
    ``recovered (<reason>)``.
    """
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
    # A skip or conflict means no integration happened this commit; an
    # operator who expects continuous integration must not lose that
    # signal in the INFO stream, so it escalates to a WARN line.
    if outcome.last_action in ("skipped", "conflict"):
        display.emit_warn_line("run", "auto-integrate", message)
        return
    if (
        outcome.last_action in ("rebased", "merged")
        and not outcome.fast_forwarded
        and outcome.last_reason
    ):
        display.emit_warn_line("run", "auto-integrate", message)
        return
    emit_activity_line(display, None, f"[cyan]auto-integrate:[/cyan] {message}")


def _inline_event_for_effect(effect: object) -> PipelineEvent | None:
    """Map an inline-effect handle to the phase-transition event the
    boundary integration hook should treat it as.

    The mapping mirrors what :func:`_execute_effect` returns for the
    SAME effect when it routes through the normal path; without it,
    the inline early-return would route every SaveCheckpointEffect
    through the no-op ``PHASE_LOOPBACK`` path, so a checkpoint save
    could never carry a catch-up landing to this checkout. Returns
    ``None`` only for effects the boundary integration is not
    equipped to handle (currently none of the inline ones; the
    fallback is conservative).
    """
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
    """Run the boundary integration hook for an inline-effect return and
    thread the outcome back through a state-shaped result.

    The helper owns the early-return-on-inline-effect path in
    :func:`_run_pipeline_step` so the orchestrator function keeps a
    sensible branch / statement count and the test surface is
    narrowly scoped to this contract. Returns ``inline_result``
    unchanged when:

    * the effect is not one of the mapped inline effects (the helper
      returns ``None`` for the event), OR
    * the integration returns ``None`` (disabled / no work to do),
      OR
    * ``inline_result`` is not a :class:`PipelineState` (e.g. the
      ``int`` return of :class:`ExitSuccessEffect`): the integration
      is run for its git side-effect but cannot be threaded onto a
      non-state value.
    """
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
    except Exception as inline_exc:  # pragma: no cover -- defensive
        logger.warning(
            "auto_integrate inline-effect boundary raised: {}",
            inline_exc,
        )
        return inline_result
    if outcome is not None and isinstance(inline_result, PipelineState):
        if outcome.last_action == "conflict":
            failed_state, _ = reducer_reduce(
                state,
                _integration_conflict_failure(state, outcome),
                policy_bundle.pipeline if policy_bundle is not None else None,
            )
            return failed_state.copy_with(rebase=outcome)
        # PipelineState-shaped result: thread the integration
        # outcome into the persisted checkpoint so the catch-up
        # survives a crash right after the inline effect. The
        # reducer/phase on the returned state is left untouched;
        # ``copy_with(rebase=...)`` only updates the rebase slot.
        return inline_result.copy_with(rebase=outcome)
    return inline_result


def _integration_conflict_failure(
    state: PipelineState, outcome: RebaseState
) -> PhaseFailureEvent:
    """Build the recovery-routable failure for an unresolved integration."""
    reason = outcome.last_reason or "the conflict resolver did not produce a resolution"
    return PhaseFailureEvent(
        phase=state.phase,
        reason=f"integration conflict requires resolution: {reason}",
        recoverable=True,
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
    """Run the post-commit auto-integration step when warranted.

    Mirrors the existing commit-seam block (clears the cycle baseline,
    then conditionally integrates). Returns ``None`` when no integration
    ran (disabled path / non-commit phase / ``COMMIT_SKIPPED`` / the
    integration call returned ``None``). Otherwise returns the
    recorded ``RebaseState`` and emits a user-facing action log line
    so the operator sees ``rebased`` / ``merged`` / ``fast-forwarded
    <target>`` / ``skipped: <reason>``.

    Extracted from :func:`_run_pipeline_step` so the seam keeps a
    sensible branch + statement count without losing the
    ``COMMIT_SUCCESS``-only trigger and the skip-on-``COMMIT_SKIPPED``
    guard.

    The ``effect`` / ``event`` parameters are typed as ``object`` so
    callers passing a union of effect / event types do not need to
    pre-narrow; the helper narrows via ``isinstance`` / ``in`` itself.
    """
    if not (
        isinstance(effect, CommitEffect)
        and commit_phase_def is not None
        and commit_phase_def.role == "commit"
        and event in (PipelineEvent.COMMIT_SUCCESS, PipelineEvent.COMMIT_SKIPPED)
    ):
        # Non-commit seam: keep the branch in lockstep with the target
        # at every successful phase boundary (silent no-op when the
        # worktree is dirty or nothing moved).
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
    # The commit-boundary integration (crash record + full sequence
    # keyed to a NEW commit) fires only on COMMIT_SUCCESS. An
    # early-skipped commit still means a clean tree, so the boundary
    # hook below catches a target that moved during the cycle.
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
                ),
                display=display,
            )
    except Exception as auto_integrate_exc:  # pragma: no cover -- defensive
        # R2/AC8: ladder rung 3 -- a clean abort is retried at the next seam.
        logger.warning(
            "auto_integrate_after_commit raised unexpectedly: {}",
            auto_integrate_exc,
        )
        return None
    if outcome is not None:
        _log_auto_integrate_outcome(display, outcome)
    return outcome


#: Events that trigger the boundary integration hook.
#:
#: The hook is STATELESS and EVENT-AGNOSTIC. It runs for EVERY
#: ``PipelineEvent`` reaching the phase-transition boundary other
#: than :data:`PipelineEvent.COMMIT_SUCCESS` (which has its own
#: commit-boundary path via :func:`auto_integrate_after_commit`).
#: Whether the integration actually moves a ref is decided downstream
#: by the same guards the commit seam already trusts:
#: :func:`ralph.pipeline.auto_integrate._worktree_is_clean` defers
#: (records a skip, mutates nothing) on any uncommitted TRACKED change,
#: and :func:`ralph.git.rebase.check_rebase_preconditions` blocks when
#: a rebase / merge / cherry-pick is already in progress. A clean
#: worktree means no in-progress phase work can be lost, so a catch-up
#: is safe regardless of which event fired.
#:
#: The hook is the SINGLE place that carries another agent's landing
#: to a feature branch that is not committing right now, so restricting
#: it to a success-only whitelist was the asymmetry that broke
#: cross-agent synchronisation on every non-success seam. The whitelist
#: used to be the gate; the worktree/preconditions guards below are the
#: gate now, and they enforce the same invariant on every event.
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


def _build_seam_conflict_resolver(
    *,
    policy_bundle: PolicyBundle | None,
    registry: _RegistryLike | None,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
) -> ConflictResolver | None:
    """Pipeline-backed resolver when policy + registry are available, else None.

    ``pipeline_deps`` and ``workspace_scope`` are what let the resolver
    start a REAL Ralph MCP session; a resolver built without them
    declines instead of invoking an agent that would have no exec policy
    and no completion contract. They are keyword-only with ``None``
    defaults so an all-mock seam still constructs.
    """
    if policy_bundle is None or registry is None:
        return None
    # Production path: hand the integration step a pipeline-backed
    # resolver so a conflicted endpoint merge is resolved and committed
    # instead of abandoned until the next commit.
    return build_agent_conflict_resolver(
        policy_bundle=policy_bundle,
        registry=registry,
        display=display,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        display_context=display_context,
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
) -> RebaseStopResolver | None:
    """Rebase-stop resolver when policy + registry are available, else None.

    The counterpart of :func:`_build_seam_conflict_resolver`, and gated on
    exactly the same availability: without it a conflicted rebase is
    aborted and degraded to one endpoint merge, which is precisely the
    behaviour that made auto-rebase look broken whenever a real conflict
    appeared.
    """
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
    """Run the boundary integration hook for successful phase events."""
    if event not in _PHASE_TRANSITION_INTEGRATION_EVENTS:
        # R2/AC8: ladder rung 3 -- this helper is called only for pipeline
        # transitions; non-seam events are retried at their next real seam.
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
    except Exception as transition_exc:  # pragma: no cover -- defensive
        # R2/AC8: ladder rung 3 -- a clean abort is retried at the next seam.
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
    """Integrate a completed fan-out at the shared coordinator seam."""
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
    if outcome.last_action == "conflict":
        failed_state, _ = reducer_reduce(
            state,
            _integration_conflict_failure(state, outcome),
            policy_bundle.pipeline if policy_bundle is not None else None,
        )
        return failed_state.copy_with(rebase=outcome)
    return state.copy_with(rebase=outcome)


def _finalize_agent_invocation(
    *,
    effect: Effect,
    event: Event,
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
) -> tuple[PipelineState, Event]:
    """Apply session capture and the post-agent-run render/phase-handler call.

    A no-op that returns ``(state, event)`` unchanged for every effect type
    other than :class:`InvokeAgentEffect`. Extracted from
    ``_run_pipeline_step`` (S-2) so that function's branch/statement count
    stays under the repo's complexity budget after the F6 success/failure
    render split was added -- this is a pure extraction, not a behavior
    change.
    """
    if not isinstance(effect, InvokeAgentEffect):
        return state, event
    # The deadline published for this invocation is deliberately still in the
    # environment here. Both branches below grade the agent's result, and
    # grading promotes and validates the result artifact, whose warned
    # incomplete-work gate asks the runtime -- not the agent -- whether this
    # cycle had warned. Revoking before that point answered "no" for every
    # promoted artifact, which is exactly the invocation the gate exists for.
    # The step's ``finally`` revokes instead, covering every exit path.
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
        )
    else:
        # F6 / DoD 12: a required-artifact phase whose agent invocation did
        # not succeed must not report success by omission. Display-only --
        # does not call handle_phase or otherwise change what the reducer
        # sees afterward (see render_phase_failure_report's reachability
        # docstring).
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
    """Sample the monotonic clock and build routing timing for one step.

    Returns ``(routing_timing, cycle_now, cycle_delta, ct_policy)``. When
    no sample box or no timebox policy is configured, returns all-None / zero
    so the caller's behavior is unchanged.
    """
    ct_policy = pipeline_policy.cycle_timebox
    if cycle_sample_box is None or ct_policy is None:
        return None, None, 0.0, ct_policy
    monotonic_fn = (
        pipeline_deps.monotonic
        if pipeline_deps is not None and pipeline_deps.monotonic is not None
        else time.monotonic
    )
    cycle_now = monotonic_fn()
    last_sample = cycle_sample_box[0]
    cycle_delta = (
        max(0.0, cycle_now - last_sample)
        if state.cycle_timebox_active and last_sample is not None
        else 0.0
    )
    routing_timing = RoutingTiming(
        total_elapsed_seconds=state.cycle_timebox_consumed_seconds + cycle_delta,
    )
    return routing_timing, cycle_now, cycle_delta, ct_policy






def _fold_cycle_elapsed(
    before: PipelineState,
    after: PipelineState,
    *,
    delta_seconds: float,
    timing_enabled: bool,
) -> PipelineState:
    """Add a step's wall-clock delta to the cycle's serialized consumed time.

    This is the only thing that makes the deadline advance: without it the
    cycle timer reads zero forever, so it never warns, never redirects, and
    publishes a deadline that never arrives.

    The delta is folded whenever the cycle was active coming INTO the step —
    whether or not the step concluded it — so a concluded cycle carries its
    true elapsed duration for the operator surfaces, and so the budget
    survives a crash-resume.
    """
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
    """Publish the cycle deadline for the workers, fan out, then revoke it.

    Publication and revocation belong together: the deadline lives in the
    process-global environment, so leaving it set after the fan-out nags
    whatever runs next — including the auto-integrate conflict resolver, which
    this fan-out invokes as its own completion callback.
    """
    _publish_fan_out_cycle_deadline(state, effect.phase, policy_bundle, routing_timing)

    def integrate_after_successful_fan_out(finished_state: PipelineState) -> PipelineState:
        # This callback runs INSIDE the fan-out, so the outer revoke has not
        # happened yet. Auto-integration spawns its own conflict-resolver
        # agent, which never joined the cycle and must not be nagged to wrap
        # up work it has no part in.
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
            monitor_stop_cb=monitor_stop_cb,
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
    """Publish the cycle deadline for a fan-out's worker processes.

    Fan-out spawns its own worker bridges without going through prompt
    materialization, so without this a fanned-out cycle gives its workers no
    deadline at all while still leaking whatever a previous invocation left
    published.
    """
    publish_cycle_deadline_env(
        state,
        phase,
        policy_bundle,
        routing_timing.total_elapsed_seconds if routing_timing is not None else None,
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
    _cycle_sample_box: list[float | None] | None = None,
) -> PipelineState | int:
    # Phase telemetry primitives — bound BEFORE the try/except so the
    # ``finally`` clause can read them on every exit path. PhaseRole is
    # derived from the EXISTING PhaseDefinition.role closed vocabulary
    # (never the raw ``state.phase`` string — privacy invariant). The
    # pessimistic ``_phase_outcome = "crashed"`` default ensures any
    # unmapped path is recorded as ``crashed`` rather than ``success``.
    phase_def = policy_bundle.pipeline.phases.get(state.phase)
    _phase_role = (
        phase_def.role if (phase_def is not None and phase_def.role is not None) else "execution"
    )
    # AC-04: the drain is the authoritative identity of a dev/fix
    # session; the role alone is too permissive (planning also maps
    # to ``role=execution``).
    _phase_drain = (
        phase_def.drain if (phase_def is not None and phase_def.drain is not None) else None
    )
    _phase_timer = PhaseTimer.start(state.phase)
    _phase_outcome = "crashed"

    def _with_phase_timing(result: PipelineState | int) -> PipelineState | int:
        return (
            result.with_phase_timing(_phase_timer.finish())
            if isinstance(result, PipelineState)
            else result
        )

    # --- Cycle timebox: sample the monotonic clock ONCE per step and build
    # routing timing for both prompt materialization (the 80% warning) and
    # the reducer (deadline enforcement).
    _routing_timing, _cycle_now, _cycle_delta, ct_policy = _sample_cycle_timing(
        state, policy_bundle.pipeline, pipeline_deps, _cycle_sample_box
    )

    try:
        effect = call_determine_effect_from_policy(
            state,
            policy_bundle,
            workspace_scope,
            config,
            pipeline_deps=pipeline_deps,
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
            routing_timing=_routing_timing,
        )
        if inline_result is not None:
            # Inline-effect early-return path: a phase transition realized
            # purely as an inline effect (SaveCheckpointEffect /
            # PreparePromptEffect / ExitSuccessEffect / ExitFailureEffect /
            # ExhaustedAnalysisPhaseAdvanceEffect) would otherwise BYPASS the
            # boundary integration the normal path runs at the bottom of
            # the step, so a checkpoint save or a prompt-prepared
            # transition could never carry another agent's landing to
            # this checkout. The hook is the single seam that catches
            # up an advanced target between commits, so it MUST fire on
            # every inline transition too. The integration logic
            # lives in ``_integrate_inline_effect`` to keep this
            # orchestrator's branch / statement count under the
            # project caps; the helper is best-effort and fail-closed
            # so an exception never escapes.
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
            return _with_phase_timing(inline_result)

        if isinstance(effect, FanOutEffect):
            _phase_outcome = "success"
            return _with_phase_timing(
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
                    routing_timing=_routing_timing,
                )
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
                )
            except MissingPlanHandoffError as exc:
                _phase_outcome = "skipped"
                return _with_phase_timing(
                    _recover_missing_plan_handoff(
                        state=state,
                        pipeline_policy=policy_bundle.pipeline,
                        checkpoint_path=_checkpoint_path(workspace_scope),
                        subscriber=pipeline_subscriber,
                        exc=exc,
                    )
                )
            # Generated once per attempt so the later render call (success
            # PASS/DEGRADED banner or the FAILED-no-artifact report) grades
            # the same run's evidence the agent invocation itself was
            # scoped under (S-2 run_id threading).
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
            _auto_integrate_outcome is not None
            and _auto_integrate_outcome.last_action == "conflict"
        ):
            event = _integration_conflict_failure(state, _auto_integrate_outcome)
        _phase_outcome = _coarse_outcome_for_event(event)
        # Resample the monotonic clock AFTER the agent invocation so the
        # reducer's deadline guard sees elapsed time that includes the
        # just-finished invocation. The step-start sample remains
        # authoritative for the prompt warning; this post-invocation sample
        # is authoritative for the routing guard and the consumed-seconds fold.
        _reduce_routing_timing, _reduce_now, _reduce_delta, _ = _sample_cycle_timing(
            state, policy_bundle.pipeline, pipeline_deps, _cycle_sample_box
        )
        next_state, _ = reducer_reduce(
            state,
            event,
            policy_bundle.pipeline,
            recovery=recovery_controller,
            routing_timing=_reduce_routing_timing,
        )
        next_state = fold_cycle_elapsed(
            state,
            next_state,
            delta_seconds=_reduce_delta,
            timing_enabled=_cycle_sample_box is not None and ct_policy is not None,
        )
        if _cycle_sample_box is not None and ct_policy is not None and _reduce_now is not None:
            _cycle_sample_box[0] = _reduce_now
        # Thread the integration outcome into the persisted checkpoint.
        # Must happen AFTER reducer_reduce (so the state model is
        # consistent) and BEFORE _save_checkpoint_or_log (so the
        # outcome survives a crash right after the phase).
        if _auto_integrate_outcome is not None:
            next_state = next_state.copy_with(rebase=_auto_integrate_outcome)
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
        return _with_phase_timing(next_state)
    except KeyboardInterrupt:
        # Re-raise — the ``finally`` clause still records ``crashed`` first.
        raise
    except BaseException as exc:
        _phase_outcome = "crashed"
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
            routing_timing=_routing_timing,
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
        return _with_phase_timing(recovered_state)
    finally:
        # Revoke the deadline published for this step's invocation. The
        # environment is process-global, so anything spawned later
        # (auto-integrate, plumbing agents) would otherwise inherit a nag
        # about a cycle it is not part of -- and revoking anywhere inside the
        # body leaks it on every path that raises, including Ctrl-C.
        withdraw_cycle_deadline_env()
        # SINGLE recording site for all exit paths (inline/FanOut/
        # MissingPlanHandoff/success/KeyboardInterrupt/BaseException).
        # Fail-soft: telemetry must never break the pipeline.
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
        if state.phase == pipeline_policy.recovery.failed_route:
            # Resolve the deadline FIRST. This hop re-enters the phase by
            # writing state.phase directly rather than routing, so it is the
            # one way into the guarded phase that is not a routing boundary --
            # and the deadline is enforced at boundaries. Everything below then
            # prepares the phase the run actually enters: preparing the
            # original target instead reset the cycle's git baseline to
            # redirect-time HEAD and persisted that phase's drain for a phase
            # that never runs.
            # The step already sampled the clock; using the stored consumed
            # total instead discards the seconds elapsed since that sample, so
            # a cycle that crossed its deadline mid-step was judged as still
            # having room.
            timeboxed = apply_cycle_timebox(
                state,
                effect.phase,
                policy=pipeline_policy,
                routing_timing=routing_timing
                or RoutingTiming(
                    total_elapsed_seconds=state.cycle_timebox_consumed_seconds,
                ),
            )
            target_phase = timeboxed.target_phase
            if timeboxed.redirected:
                logger.bind(component="policy.routing").warning(timeboxed.redirect_reason)
            prepared_state = _reset_phase_chain_for_recovery(timeboxed.state, target_phase)
            target_phase_def = pipeline_policy.phases.get(target_phase)
            if target_phase_def is not None and target_phase_def.role == "commit":
                prepared_state = prepared_state.copy_with(commit=CommitState())
            if target_phase_def is not None and target_phase_def.role == "execution":
                clear_cycle_baseline(workspace_scope.root)
                write_start_commit_if_absent(workspace_scope.root)
        # The effect's drain describes the phase it named; once a redirect has
        # changed the target, only the policy knows the right one.
        requested_drain = effect.drain if target_phase == effect.phase else None
        prepare_updates: dict[str, object] = {
            "phase": target_phase,
            "current_drain": requested_drain
            or resolve_phase_drain(target_phase, pipeline_policy),
        }
        # A change of phase here (skip-invocation success route, failed-route
        # re-entry) must clear the next-attempt session action exactly like
        # progress.advance_phase does. Preserving it would leak a stale resume
        # session id / retry intent into an unrelated phase's first attempt.
        # Same-phase re-prompts (the retry-in-session resume path) intentionally
        # keep the intent so the resume can take effect.
        if target_phase != prepared_state.phase:
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
        emit_activity_line(display, None, f"[bold yellow]{GITHUB_STAR_CTA}[/bold yellow]")
    return 0


def _call_determine_effect_from_policy(
    state: PipelineState,
    policy_bundle: PolicyBundle,
    workspace_scope: WorkspaceScope,
    config: UnifiedConfig,
    *,
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
            return fn(
                state,
                policy_bundle,
                workspace_scope,
                config=config,
                has_uncommitted_changes_fn=has_changes,
            )
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
finalize_agent_invocation = _finalize_agent_invocation
fold_cycle_elapsed = _fold_cycle_elapsed
sample_cycle_timing = _sample_cycle_timing
