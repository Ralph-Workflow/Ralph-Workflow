from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InvokeOptions,
    InvokeRuntimeOptions,
    PiContextExhaustedExitError,
    SupervisionInfrastructureError,
    attempt_facts_for_failure,
    build_invoke_options_from_config,
    extract_transport_session_id,
    invoke_agent,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._agy_incomplete_exit_error import AgyIncompleteExitError
from ralph.agents.invoke._direct_mcp_recovery import (
    condense_failure_text,
    run_with_direct_mcp_recovery,
)
from ralph.agents.invoke._open_code_resumable_exit_error import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.config.enums import AgentTransport, Verbosity
from ralph.config.mcp_loader import McpConfigError
from ralph.display.parallel_display import (
    ParallelDisplay,
    emit_activity_line,
    get_display_context,
    status_text,
    subscriber_for_display,
)
from ralph.git.operations import stage_files
from ralph.mcp.artifacts.canonical_submit import _clear_worker_artifacts
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.server.lifecycle import (
    McpServerError,
    RestartAwareMcpBridge,
)
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    build_retry_hint,
    retry_fingerprint_path_for_hint,
    retry_hint_fingerprint,
    retry_hint_path,
)
from ralph.pipeline._agent_bridge_ctx import _AgentBridgeCtx
from ralph.pipeline._agent_invocation_ctx import _AgentInvocationCtx
from ralph.pipeline._runner_session import set_last_captured_session_id
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.agent_execution_deps import build_agent_execution_deps
from ralph.pipeline.agent_recovery_input import AgentRecoveryInput
from ralph.pipeline.agent_recovery_plan import AgentRecoveryPlan
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.agent_retry_intent import (
    AgentRetryIntent,
    clear_fresh_bridge_requested,
    cleared_agent_retry_intent,
    fresh_bridge_requested,
    mark_fresh_bridge_requested,
)
from ralph.pipeline.agent_retry_intent import (
    pop_last_captured_retry_intent as _pop_last_captured_retry_intent,
)
from ralph.pipeline.agent_retry_intent import (
    set_last_captured_retry_intent as _set_last_captured_retry_intent_state,
)
from ralph.pipeline.attempt_finalizer import AttemptFinalization, AttemptFinalizer
from ralph.pipeline.commit_executor import clear_phase_output_artifacts
from ralph.pipeline.events import (
    AgentFailureEvent,
    PipelineEvent,
)
from ralph.pipeline.integration_resolution import (
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, verbosity_rank
from ralph.pipeline.phase_transition import show_phase_start_with_context
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.pipeline.session_bridge import build_session_bridge, scoped_reset_tool_registry_callback
from ralph.pipeline.waiting_dispatch import dispatch_waiting_event
from ralph.pipeline.watchdog_fire_record import record_watchdog_fire, watchdog_fire_detail
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_failure_log_line import render_attempt_failure_log_line
from ralph.recovery.classifier import SESSION_NOT_FOUND_SUBSTRINGS as _SESSION_NOT_FOUND_SUBSTRINGS
from ralph.recovery.failure_classifier import is_unsubmitted_artifact_failure
from ralph.recovery.failure_details import contains_casefolded_marker, failure_detail_parts
from ralph.recovery.retry_prompt import (
    PREMISE_CHECK_LINE,
    build_retry_error_block,
    build_validation_retry_footer,
    premise_check_line,
)
from ralph.workspace import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe, WorkspaceChangeProbe

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.agent_execution_deps import (
        _InvokeAgentFn,
        _ShowPhaseStartFn,
    )
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import MaterializeMasterPromptFn, PipelineDeps
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PolicyBundle
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.attempt_outcome import AttemptOutcome
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.dead_agent_verdict import TypedSignal
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope

    class _KwargsMaterializeMasterPromptFn(Protocol):
        def __call__(self, **kwargs: object) -> str: ...


_VERBOSE_LOG_LEVEL = 2
_FALLBACK_FINALIZER_ENTRY_POINT = "main_phase_loop"
_AGENT_RAW_OUTPUT_TAIL_LINES = 256
_AGENT_RENDERED_OUTPUT_TAIL_LINES = 64
_RECOVERY_CONTEXT_LINES = 12
_RECOVERY_CONTEXT_MAX_CHARS = 240
_PORCELAIN_STATUS_PREFIX_LEN = 3
_DEFAULT_PHASE_NAMES = frozenset(
    {
        "planning",
        "planning_analysis",
        "development",
        "development_commit_review",
        "development_commit",
        "development_analysis",
        "development_final_commit_review",
        "development_final_commit",
        "complete",
        "failed_terminal",
    }
)


def _record_successful_attempt_session(
    ctx: _AgentInvocationCtx,
    raw_output: tuple[str, ...],
    session_id: str | None,
) -> None:
    final_session_id = extract_transport_session_id(raw_output) or session_id
    if ctx.deps.set_session_id_cb is not None:
        ctx.deps.set_session_id_cb(final_session_id)
    set_last_captured_session_id(final_session_id)
    _set_last_captured_retry_intent(cleared_agent_retry_intent())


@dataclass(frozen=True)
class _AttemptResult:
    event: PipelineEvent | AgentFailureEvent | None
    next_prompt_file: str
    next_session_id: str | None


def _event_from_attempt_result(result: _AttemptResult) -> PipelineEvent | AgentFailureEvent:
    return result.event if result.event is not None else PipelineEvent.AGENT_FAILURE


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    *,
    display_context: DisplayContext | None,
    display: ParallelDisplay | None = None,
    verbosity: Verbosity = Verbosity.VERBOSE,
    state: PipelineState | None = None,
    policy_bundle: PolicyBundle | None = None,
    bridge: RestartAwareMcpBridge | None = None,
    raw_output_sink: deque[str] | None = None,
    rendered_output_sink: deque[str] | None = None,
    raw_line_sink: Callable[[str], None] | None = None,
    run_id: str | None = None,
    required_artifact: RequiredArtifact | None = None,
    session_id: str | None = None,
    extra_env: dict[str, str] | None = None,
    raise_resumable_exit: bool = False,
    agent_invocation_error_sink: Callable[[Exception], object] | None = None,
    attempt_finalizer: AttemptFinalizer | None = None,
    **opts: object,
) -> PipelineEvent | AgentFailureEvent:
    verdict = inspect_integration_resolution(
        workspace_scope.root,
        state.rebase if state is not None else RebaseState(),
    )
    assert_non_resolution_dispatch_allowed(effect.phase, verdict)

    resolved_display_context = get_display_context(display, display_context)
    registry = _registry_from_pipeline_deps(pipeline_deps, config)
    agent_config = cast("AgentConfig | None", registry.get(effect.agent_name))
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        _set_last_captured_retry_intent(
            AgentRetryIntent(
                failure_reason=AGENT_NOT_FOUND_REASON,
                skip_same_agent_retries=True,
                failed_agent_name=effect.agent_name,
            )
        )
        return PipelineEvent.AGENT_FAILURE

    active_rec = cast(
        "RecoveryController | None", opts.get("recovery_controller") or opts.get("recovery")
    )
    if active_rec is None:
        active_rec = pipeline_deps.recovery_controller
    resolved_attempt_finalizer = attempt_finalizer or _fallback_attempt_finalizer(
        active_rec,
        phase=str(effect.phase),
        agent=effect.agent_name,
    )
    active_ledger = active_rec.ledger if active_rec is not None else pipeline_deps.ledger
    active_clock: Clock = active_rec.clock if active_rec is not None else SystemClock()
    if active_ledger is None and state is not None and state.ledger_snapshot:
        from ralph.recovery.agent_failure_ledger import AgentFailureLedger

        active_ledger = AgentFailureLedger.from_snapshot(state.ledger_snapshot, clock=active_clock)
    if active_ledger is not None and not active_ledger.is_available(
        str(effect.phase), effect.agent_name
    ):
        logger.error(
            "Aborting agent '{}' invocation: agent is unavailable or spent in failure ledger.",
            effect.agent_name,
        )
        _set_last_captured_retry_intent(
            AgentRetryIntent(
                action=None,
                session_id=None,
                failure_reason="Agent unavailable or spent in failure ledger",
            )
        )
        return PipelineEvent.AGENT_FAILURE
    effective_agents_policy = (
        policy_bundle.agents
        if policy_bundle is not None
        else load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    )
    invoke_line = status_text("Invoking agent", effect.agent_name, "cyan")
    if display is not None:
        display.emit(effect.agent_name, invoke_line)
    else:
        emit_activity_line(
            display,
            None,
            invoke_line,
        )
    display_subscriber = subscriber_for_display(display)

    def waiting_listener(event: object) -> None:
        dispatch_waiting_event(
            event,
            subscriber=display_subscriber,
            unit_id=effect.agent_name,
            agent_name=effect.agent_name,
        )
        if effect.activity_status_listener is not None:
            effect.activity_status_listener(event)

    show_phase_start_cb: _ShowPhaseStartFn | None = None
    if state is not None and policy_bundle is not None:
        show_phase_start_cb = show_phase_start_with_context
    deps = build_agent_execution_deps(
        pipeline_deps=pipeline_deps,
        invoke_agent=_invoke_agent_from_registry_or_opts(pipeline_deps, opts),
        agent_invocation_error=_agent_invocation_error_from_opts(opts),
        agent_registry=AgentRegistry,
        show_phase_start_cb=show_phase_start_cb,
        set_session_id_cb=cast(
            "Callable[[str | None], None] | None", opts.get("set_session_id_cb")
        ),
    )
    invocation_started = time.monotonic()
    ctx = _AgentInvocationCtx(
        effect=effect,
        config=config,
        deps=deps,
        workspace_scope=workspace_scope,
        verbosity=verbosity,
        resolved_display_context=resolved_display_context,
        display_subscriber=display_subscriber,
        max_recovery_attempts=recovery_attempts_for_effect(effect, config),
        effective_agents_policy=effective_agents_policy,
        state=state,
        policy_bundle=policy_bundle,
        waiting_listener=waiting_listener,
        agent_config=agent_config,
        display=display,
        invocation_started_monotonic=invocation_started,
        clock=active_clock,
        invocation_options=cast("InvokeOptions | None", opts.get("invocation_options")),
        worker_namespace=cast("Path | None", opts.get("worker_namespace")),
        worker_artifact_dir=cast("Path | None", opts.get("worker_artifact_dir")),
        parallel_worker=cast("bool", opts.get("parallel_worker", False)),
    )
    invocation_outcome = "crashed"
    try:
        result = _invoke_agent_with_recovery(
            ctx,
            pipeline_deps,
            bridge=bridge,
            workspace_probe=_resolve_workspace_probe(opts),
            raw_output_sink=raw_output_sink,
            rendered_output_sink=rendered_output_sink,
            raw_line_sink=raw_line_sink,
            run_id=run_id,
            required_artifact=required_artifact,
            session_id=session_id,
            extra_env=extra_env,
            on_retry_failure=cast(
                "Callable[[list[str]], object] | None", opts.get("on_retry_failure")
            ),
            raise_resumable_exit=raise_resumable_exit,
            agent_invocation_error_sink=agent_invocation_error_sink,
            attempt_finalizer=resolved_attempt_finalizer,
            recovery_controller=active_rec,
        )
        invocation_outcome = "success" if result == PipelineEvent.AGENT_SUCCESS else "failure"
        return result
    except KeyboardInterrupt:
        invocation_outcome = "interrupted"
        raise
    except BaseException as exc:
        invocation_outcome = "crashed"
        _report_agent_invocation_exception(exc)
        raise
    finally:
        _record_agent_invocation_telemetry(
            effect=effect,
            agent_transport=agent_config.transport,
            policy_bundle=policy_bundle,
            agents_policy=effective_agents_policy,
            duration_s=max(0.0, time.monotonic() - invocation_started),
            outcome=invocation_outcome,
        )


def _resolve_workspace_probe(opts: dict[str, object]) -> WorkspaceChangeProbe:
    supplied = opts.get("workspace_probe")
    if isinstance(supplied, WorkspaceChangeProbe):
        return supplied
    return DefaultWorkspaceChangeProbe()


def _report_agent_invocation_exception(exc: BaseException) -> None:
    try:
        from ralph.telemetry._sentry import report_handled_exception

        report_handled_exception(exc, origin="agent_invocation")
    except Exception:
        return


def _record_agent_invocation_telemetry(
    *,
    effect: InvokeAgentEffect,
    agent_transport: AgentTransport | None,
    policy_bundle: PolicyBundle | None,
    agents_policy: AgentsPolicy,
    duration_s: float,
    outcome: str,
) -> None:
    try:
        from ralph.telemetry._sentry import record_agent_invocation

        phase_def = policy_bundle.pipeline.phases.get(str(effect.phase)) if policy_bundle else None
        drain_config = agents_policy.agent_drains.get(effect.drain or "")
        record_agent_invocation(
            transport=agent_transport or AgentTransport.GENERIC,
            phase_role=(phase_def.role or "execution") if phase_def is not None else "execution",
            drain=effect.drain,
            drain_class=drain_config.drain_class if drain_config is not None else None,
            pipeline_profile=_pipeline_profile(policy_bundle),
            duration_s=duration_s,
            outcome=outcome,
        )
    except Exception:
        return


def _pipeline_profile(policy_bundle: PolicyBundle | None) -> str:
    if policy_bundle is None:
        return "custom"
    raw_phase_names: object = getattr(policy_bundle.pipeline, "phases", {})
    if not isinstance(raw_phase_names, dict):
        return "custom"
    phase_names = cast("Mapping[object, object]", raw_phase_names)
    return "default" if all(name in _DEFAULT_PHASE_NAMES for name in phase_names) else "custom"


class _RegistryLike(Protocol):
    def get(self, name: str) -> object | None: ...


def _registry_from_pipeline_deps(
    pipeline_deps: PipelineDeps,
    config: UnifiedConfig,
) -> _RegistryLike:
    if pipeline_deps.registry_factory is not None:
        return cast("_RegistryLike", pipeline_deps.registry_factory(config))
    return cast("_RegistryLike", AgentRegistry.from_config(config))


def _invoke_agent_from_registry_or_opts(
    pipeline_deps: PipelineDeps,
    opts: dict[str, object],
) -> _InvokeAgentFn:
    invoke = cast("_InvokeAgentFn | None", opts.get("invoke_agent"))
    if invoke is not None:
        return invoke
    deps_invoke = pipeline_deps.invoke_agent
    if deps_invoke is not None:
        return deps_invoke
    return invoke_agent


def _agent_invocation_error_from_opts(opts: dict[str, object]) -> type[Exception]:
    error = cast("type[Exception] | None", opts.get("agent_invocation_error"))
    if error is not None:
        return error
    return AgentInvocationError


@dataclass
class _AttemptState:
    prompt_file: str
    resume_session_id: str | None
    last_agent_session_id: str | None = None
    completion_reprompt_used: bool = False
    attempts_started: int = 0
    last_observation: AgentFailureEvent | None = None
    attempt_observed: bool = False
    artifact_baseline: list[int] | None = None


def _artifact_fingerprint(
    ctx: _AgentInvocationCtx, artifact: RequiredArtifact | None
) -> list[int] | None:
    if artifact is None:
        return None
    from ralph.phases.artifact_freshness import artifact_fingerprint

    return artifact_fingerprint(ctx.workspace_scope.root / artifact.artifact_path)


def _observation_sink(state: _AttemptState) -> Callable[[AgentFailureEvent], None]:

    def _sink(observation: AgentFailureEvent) -> None:
        state.last_observation = observation

    return _sink


def _observe_attempt_workspace(
    ctx: _AgentInvocationCtx,
    state: _AttemptState,
    probe: WorkspaceChangeProbe | None,
    workspace: Workspace | None,
) -> None:
    state.attempt_observed = False
    if probe is None or workspace is None:
        return
    probe.observe_before(str(ctx.effect.phase), workspace)
    state.attempt_observed = True


def _attempt_failure_observation(
    ctx: _AgentInvocationCtx,
    state: _AttemptState,
    probe: WorkspaceChangeProbe | None,
    workspace: Workspace | None,
    exc: BaseException,
) -> AgentFailureEvent:
    existing = state.last_observation
    if existing is not None:
        observation = existing
    else:
        changed: bool | None = None
        if state.attempt_observed and probe is not None and workspace is not None:
            changed = bool(probe.has_changed(str(ctx.effect.phase), workspace))
        observation = AgentFailureEvent(
            phase=str(ctx.effect.phase),
            agent=ctx.effect.agent_name,
            artifact_valid=False,
            workspace_changed=changed,
        )
    if observation.facts is None:
        observation = replace(observation, facts=attempt_facts_for_failure(exc))
    state.last_observation = observation
    return observation


def _safe_last_agent_session_id(state: PipelineState | None) -> str | None:
    if state is None:
        return None
    raw_value: object = getattr(state, "last_agent_session_id", None)
    if isinstance(raw_value, str):
        return raw_value
    return None


def _prepare_recovery_attempt(
    ctx: _AgentInvocationCtx,
    bridge_ctx: _AgentBridgeCtx,
    pipeline_deps: PipelineDeps,
    state: _AttemptState,
    session_id: str | None,
    run_id: str,
    required_artifact: RequiredArtifact | None,
    *,
    extra_env: dict[str, str] | None,
    workspace_probe: WorkspaceChangeProbe | None = None,
) -> InvokeOptions | None:
    elapsed = (
        max(0.0, time.monotonic() - ctx.invocation_started_monotonic)
        if state.attempts_started > 0
        else 0.0
    )
    development_timebox = (
        ctx.policy_bundle.pipeline.development_timebox if ctx.policy_bundle is not None else None
    )
    if (
        ctx.state is not None
        and ctx.state.dev_timebox_active
        and development_timebox is not None
        and ctx.effect.phase == development_timebox.guarded_entry
        and ctx.state.dev_timebox_consumed_seconds + elapsed >= development_timebox.warning_seconds
    ):
        _set_last_captured_retry_intent(cleared_agent_retry_intent())
        return None
    options = _build_attempt_invoke_options(
        ctx,
        bridge_ctx,
        pipeline_deps,
        session_id,
        run_id,
        required_artifact,
        required_artifact_resolved=True,
        extra_env=extra_env,
        elapsed_recovery_seconds=elapsed,
        workspace_probe=workspace_probe,
    )
    if options.max_session_seconds == 0.0:
        _set_last_captured_retry_intent(cleared_agent_retry_intent())
        return None
    state.attempts_started += 1
    return options


def _report_terminal_invocation_error(
    recovered: _AttemptResult,
    agent_invocation_error_sink: Callable[[Exception], object] | None,
    exc: Exception,
) -> _AttemptResult:
    if agent_invocation_error_sink is not None:
        agent_invocation_error_sink(exc)
    return recovered


def _handle_recovery_attempt_failure(
    ctx: _AgentInvocationCtx,
    exc: Exception,
    state: _AttemptState,
    session_id: str | None,
    effective_run_id: str,
    raw_output: deque[str],
    rendered_output: deque[str],
    observation: AgentFailureEvent | None = None,
    *,
    attempt_finalizer: AttemptFinalizer | None = None,
    recovery_controller: RecoveryController | None = None,
    agent_invocation_error_sink: Callable[[Exception], object] | None = None,
) -> _AttemptResult:
    phase = str(ctx.effect.phase)
    agent = ctx.effect.agent_name
    workspace_changed: bool | None = (
        observation.resolved_workspace_changed() if observation else None
    )
    outcome = (
        attempt_finalizer(
            AttemptFinalization(
                attempt_ordinal=state.attempts_started,
                attempt_failed=True,
                exc=exc,
                workspace_changed=workspace_changed,
                typed_signal=_typed_signal_for_exception(exc),
                retryable_reason=retryable_agent_failure_reason(exc, AgentInactivityTimeoutError),
            )
        )
        if attempt_finalizer is not None
        else None
    )
    recovery_plan = build_agent_recovery_plan(
        _build_recovery_input_for_attempt(
            ctx=ctx,
            exc=exc,
            state=state,
            session_id=session_id,
            raw_output=raw_output,
            rendered_output=rendered_output,
            run_id=effective_run_id,
        ),
        outcome,
    )
    controller = recovery_controller
    ledger = ctx.deps.pipeline_deps.ledger
    demoted, demotion_reason = (
        (False, None)
        if outcome is not None and outcome.retries_same_agent and controller is None
        else _demotion_after_finalization(
            controller,
            ledger,
            phase=phase,
            agent=agent,
            workspace_changed=workspace_changed,
        )
    )
    charged_observation = _charged_observation(
        observation,
        charged=ledger is not None or controller is not None,
        phase=phase,
        agent=agent,
        exc=exc,
    )
    if charged_observation is not None:
        state.last_observation = charged_observation
    if demoted and charged_observation is not None:
        logger.error(
            "Aborting agent '{}' retries: unproductive attempt limit reached ({}). "
            "Refusing to spin; failing fast.",
            agent,
            demotion_reason,
        )
        _set_last_captured_retry_intent(
            AgentRetryIntent(
                action=None,
                session_id=None,
                failure_reason=recovery_plan.reason if recovery_plan is not None else "",
            )
        )
        return _AttemptResult(charged_observation, state.prompt_file, session_id)

    if recovery_plan is None:
        retry_intent = resolve_retry_intent(
            outcome if outcome is not None else exc,
            phase=phase,
            agent=agent,
            session_id=session_id,
            inactivity_error_type=AgentInactivityTimeoutError,
            exc=exc,
        )
        if retry_intent is not None:
            _set_last_captured_retry_intent(retry_intent)
        else:
            _set_last_captured_retry_intent(AgentRetryIntent())
        if outcome is None:
            raise exc
        if agent_invocation_error_sink is not None:
            agent_invocation_error_sink(exc)
        return _AttemptResult(
            charged_observation
            if charged_observation is not None
            else (observation if observation is not None else PipelineEvent.AGENT_FAILURE),
            state.prompt_file,
            session_id,
        )

    state.prompt_file = recovery_plan.prompt_file
    state.resume_session_id = recovery_plan.session_id
    state.completion_reprompt_used = state.completion_reprompt_used or isinstance(
        exc, AgyIncompleteExitError
    )
    retry_intent = resolve_retry_intent(
        outcome if outcome is not None else exc,
        phase=phase,
        agent=agent,
        session_id=recovery_plan.session_id,
        inactivity_error_type=AgentInactivityTimeoutError,
        exc=exc,
    )
    if retry_intent is not None:
        _set_last_captured_retry_intent(retry_intent)
    else:
        _set_last_captured_retry_intent(AgentRetryIntent())
    raise exc


def _fallback_attempt_finalizer(
    controller: RecoveryController | None,
    *,
    phase: str,
    agent: str,
) -> AttemptFinalizer | None:
    if controller is None:
        return None

    def finalize(finalization: AttemptFinalization) -> AttemptOutcome:
        facts = (
            attempt_facts_for_failure(finalization.exc)
            if finalization.exc is not None
            else AttemptFacts(artifact_valid=not finalization.attempt_failed)
        )
        if finalization.workspace_changed is not None:
            facts = replace(facts, workspace_changed=finalization.workspace_changed)
        return controller.finalize_attempt(
            phase,
            agent,
            facts=facts,
            attempt_failed=finalization.attempt_failed,
            typed_signal=finalization.typed_signal,
            exc=finalization.exc,
            retryable_reason=finalization.retryable_reason,
            attempt_key=(
                f"{_FALLBACK_FINALIZER_ENTRY_POINT}:{phase}:{agent}:"
                f"{controller.next_attempt_ordinal(phase, agent)}"
            ),
        )

    return finalize


def _typed_signal_for_exception(exc: BaseException) -> TypedSignal | None:
    from ralph.recovery.controller import build_typed_agent_signal
    from ralph.recovery.failure_classifier import (
        typed_signal_name,
        typed_signal_provider_originated,
    )

    signal_name = typed_signal_name(exc)
    if signal_name is None:
        return None
    return build_typed_agent_signal(
        signal_name,
        typed_signal_provider_originated(exc),
        None,
    )


def _demotion_after_finalization(
    controller: RecoveryController | None,
    ledger: AgentFailureLedger | None,
    *,
    phase: str,
    agent: str,
    workspace_changed: bool | None,
) -> tuple[bool, str | None]:
    if controller is not None:
        from ralph.recovery.agent_failure_ledger import Cooldown, Spent

        status = controller.ledger.status(phase, agent)
        demoted = not status.is_eligible
        demotion_reason = status.reason if isinstance(status, (Cooldown, Spent)) else None
        return demoted, demotion_reason
    if ledger is not None:
        from ralph.recovery.agent_failure_ledger import AttemptFact

        outcome = ledger.record(
            AttemptFact(
                phase=phase,
                agent=agent,
                artifact_valid=False,
                workspace_changed=workspace_changed,
            )
        )
        if outcome.demoted:
            logger.warning(
                "Agent '{}' demoted in phase '{}' (consecutive={}, total={}, reason={})",
                agent,
                phase,
                outcome.consecutive_unproductive,
                outcome.total_unproductive,
                outcome.reason,
            )
        return outcome.demoted, outcome.reason
    return False, None


def _charged_observation(
    observation: AgentFailureEvent | None,
    *,
    charged: bool,
    phase: str,
    agent: str,
    exc: Exception,
) -> AgentFailureEvent | None:
    if not charged:
        return None
    if observation is not None:
        return replace(observation, already_charged=True, reason=str(exc))
    return AgentFailureEvent(
        phase=phase,
        agent=agent,
        artifact_valid=False,
        workspace_changed=None,
        reason=str(exc),
        already_charged=True,
    )


def has_valid_terminal_artifact(
    ctx: _AgentInvocationCtx,
    resolved_required_artifact: RequiredArtifact | None,
    exc: Exception,
    *,
    baseline: list[int] | None = None,
) -> bool:
    returncode_obj: object = getattr(exc, "returncode", 0)
    if (
        not isinstance(returncode_obj, int)
        or returncode_obj == 0
        or resolved_required_artifact is None
    ):
        return False
    return _has_fresh_valid_artifact(ctx, resolved_required_artifact, baseline=baseline)


def _has_fresh_valid_artifact(
    ctx: _AgentInvocationCtx,
    resolved_required_artifact: RequiredArtifact,
    *,
    baseline: list[int] | None,
) -> bool:
    if not (ctx.workspace_scope.root / resolved_required_artifact.artifact_path).exists():
        return False
    current = _artifact_fingerprint(ctx, resolved_required_artifact)
    if current is None or current == baseline:
        return False
    try:
        from ralph.phases.artifacts import (
            load_phase_artifact,
            unwrap_phase_artifact_content,
        )

        ws = FsWorkspace(ctx.workspace_scope.root)
        art = load_phase_artifact(
            ws,
            resolved_required_artifact.artifact_path,
            artifact_type=resolved_required_artifact.artifact_type,
        )
        unwrap_phase_artifact_content(
            art,
            expected_type=resolved_required_artifact.artifact_type,
        )
        return True
    except Exception:
        return False


def _missing_required_artifact_event(
    ctx: _AgentInvocationCtx,
    state: _AttemptState,
    artifact: RequiredArtifact | None,
    workspace_probe: WorkspaceChangeProbe | None,
    workspace: Workspace,
) -> AgentFailureEvent | None:
    if str(ctx.effect.phase) == "commit":
        return None
    if artifact is None or not artifact.artifact_required:
        return None
    if _has_fresh_valid_artifact(ctx, artifact, baseline=state.artifact_baseline):
        return None
    changed = (
        workspace_probe.has_changed(str(ctx.effect.phase), workspace)
        if state.attempt_observed and workspace_probe is not None
        else None
    )
    return AgentFailureEvent(
        phase=str(ctx.effect.phase),
        agent=ctx.effect.agent_name,
        artifact_valid=False,
        workspace_changed=changed,
        reason="required artifact missing, stale, or invalid",
    )


def _invoke_agent_with_recovery(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    *,
    bridge: RestartAwareMcpBridge | None = None,
    raw_output_sink: deque[str] | None = None,
    rendered_output_sink: deque[str] | None = None,
    raw_line_sink: Callable[[str], None] | None = None,
    run_id: str | None = None,
    required_artifact: RequiredArtifact | None = None,
    session_id: str | None = None,
    extra_env: dict[str, str] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    raise_resumable_exit: bool = False,
    agent_invocation_error_sink: Callable[[Exception], object] | None = None,
    attempt_finalizer: AttemptFinalizer | None = None,
    recovery_controller: RecoveryController | None = None,
    workspace_probe: WorkspaceChangeProbe | None = None,
) -> PipelineEvent | AgentFailureEvent:
    raw_fresh_bridge: object = fresh_bridge_requested()
    if raw_fresh_bridge is True:
        bridge = None
    own_bridge = bridge is None
    effective_run_id = run_id or str(uuid.uuid4())
    resolved_required_artifact = _resolve_required_artifact(
        ctx,
        pipeline_deps,
        required_artifact,
    )
    if bridge is None:
        bridge = _start_bridge(ctx, pipeline_deps, effective_run_id)
    elif run_id is None:
        effective_run_id = cast("str", getattr(bridge, "run_id", effective_run_id))
    state = _AttemptState(
        prompt_file=ctx.effect.prompt_file,
        resume_session_id=session_id or _initial_resume_session_id(ctx),
        last_agent_session_id=_safe_last_agent_session_id(ctx.state),
    )
    attempt_workspace: Workspace = FsWorkspace(ctx.workspace_scope.root)
    try:
        master_prompt_file = _materialize_master_prompt(
            ctx,
            pipeline_deps,
            required_artifact=resolved_required_artifact,
            required_artifact_resolved=True,
        )
        bridge_ctx = _AgentBridgeCtx(
            bridge=bridge, session=cast("object", None), master_prompt_file=master_prompt_file
        )
        raw_output: deque[str] = (
            raw_output_sink
            if raw_output_sink is not None
            else deque(maxlen=_AGENT_RAW_OUTPUT_TAIL_LINES)
        )
        rendered_output: deque[str] = (
            rendered_output_sink
            if rendered_output_sink is not None
            else deque(maxlen=_AGENT_RENDERED_OUTPUT_TAIL_LINES)
        )

        if resolved_required_artifact is not None and resolved_required_artifact.artifact_required:
            from ralph.phases.artifact_freshness import record_invocation_baseline

            record_invocation_baseline(
                FsWorkspace(ctx.workspace_scope.root),
                str(ctx.effect.phase),
                resolved_required_artifact.artifact_path,
            )

        terminal_mcp_error = False

        def attempt_fn(
            retry_session_id: str | None,
            capture_session_id: Callable[[str], None],
        ) -> _AttemptResult:
            session_id = retry_session_id or state.resume_session_id
            options = _prepare_recovery_attempt(
                ctx,
                bridge_ctx,
                pipeline_deps,
                state,
                session_id,
                effective_run_id,
                resolved_required_artifact,
                extra_env=extra_env,
                workspace_probe=workspace_probe,
            )
            if options is None:
                return _AttemptResult(PipelineEvent.AGENT_FAILURE, state.prompt_file, session_id)
            state.last_observation = None
            options = replace(options, failure_observation_sink=_observation_sink(state))
            try:
                _check_bridge_health(ctx, bridge_ctx, pipeline_deps)
                if isinstance(bridge_ctx.bridge, RestartAwareMcpBridge):
                    bridge_ctx.bridge.reset_session_budget()
                state.artifact_baseline = _artifact_fingerprint(ctx, resolved_required_artifact)
                _observe_attempt_workspace(ctx, state, workspace_probe, attempt_workspace)
                _consume_attempt_output(
                    ctx,
                    bridge_ctx,
                    state.prompt_file,
                    options,
                    raw_output,
                    rendered_output,
                    capture_session_id,
                    pipeline_deps,
                    agent_label_scope=effective_run_id,
                    raw_line_sink=raw_line_sink,
                )
                missing_artifact = _missing_required_artifact_event(
                    ctx,
                    state,
                    resolved_required_artifact,
                    workspace_probe,
                    attempt_workspace,
                )
                if missing_artifact is not None:
                    return _AttemptResult(missing_artifact, state.prompt_file, session_id)
                _record_successful_attempt_session(ctx, tuple(raw_output), session_id)
                return _AttemptResult(PipelineEvent.AGENT_SUCCESS, state.prompt_file, session_id)
            except ctx.deps.agent_invocation_error as exc:
                if has_valid_terminal_artifact(
                    ctx, resolved_required_artifact, exc, baseline=state.artifact_baseline
                ):
                    _record_successful_attempt_session(ctx, tuple(raw_output), session_id)
                    return _AttemptResult(
                        PipelineEvent.AGENT_SUCCESS, state.prompt_file, session_id
                    )
                return _report_terminal_invocation_error(
                    _handle_recovery_attempt_failure(
                        ctx=ctx,
                        exc=exc,
                        state=state,
                        session_id=session_id,
                        effective_run_id=effective_run_id,
                        raw_output=raw_output,
                        rendered_output=rendered_output,
                        observation=_attempt_failure_observation(
                            ctx, state, workspace_probe, attempt_workspace, exc
                        ),
                        attempt_finalizer=attempt_finalizer,
                        recovery_controller=recovery_controller,
                    ),
                    agent_invocation_error_sink,
                    exc,
                )

        try:
            return _event_from_attempt_result(
                run_with_direct_mcp_recovery(
                    attempt_fn,
                    max_retries=ctx.max_recovery_attempts,
                    reset_tool_registry=scoped_reset_tool_registry_callback(
                        bridge_ctx.bridge,
                        effective_run_id,
                    ),
                    on_retry_failure=on_retry_failure,
                    retry_resumable_exit=True,
                )
            )
        except ctx.deps.agent_invocation_error as exc:
            if (
                raise_resumable_exit and isinstance(exc, OpenCodeResumableExitError)
            ) or surfaces_supervision_error(ctx.effect, exc):
                raise
            _write_terminal_missing_artifact_hint(ctx, exc, resolved_required_artifact)
            return _handle_terminal_agent_invocation_error(
                exc,
                agent_invocation_error_sink=agent_invocation_error_sink,
                observation=_attempt_failure_observation(
                    ctx, state, workspace_probe, attempt_workspace, exc
                ),
            )
    except (
        McpServerError,
        McpConfigError,
        OpenCodeResumableExitError,
        AgentInactivityTimeoutError,
        SupervisionInfrastructureError,
    ) as exc:
        if isinstance(exc, McpServerError):
            terminal_mcp_error = True
            if own_bridge and bridge is not None:
                bridge.shutdown()
            _mark_next_attempt_gets_fresh_bridge(exc)
            logger.warning(
                "MCP server error (retryable): last_stage={last_stage} elapsed_s={elapsed_s} "
                "restart_reason={restart_reason} handover_step={handover_step} "
                "probe_duration_s={probe_duration_s} probe_timeout_s={probe_timeout_s} "
                "child_pid={child_pid} psutil_status={psutil_status} failure_kind={failure_kind} "
                "error={exc}",
                last_stage=exc.last_stage,
                elapsed_s=exc.elapsed_s,
                restart_reason=exc.restart_reason,
                handover_step=exc.handover_step,
                probe_duration_s=exc.probe_duration_s,
                probe_timeout_s=exc.probe_timeout_s,
                child_pid=exc.child_pid,
                psutil_status=exc.psutil_status,
                failure_kind=exc.failure_kind,
                exc=exc,
            )
            return _handle_terminal_agent_invocation_error(
                exc,
                agent_invocation_error_sink=agent_invocation_error_sink,
                observation=_attempt_failure_observation(
                    ctx, state, workspace_probe, attempt_workspace, exc
                ),
            )
        raise
    except Exception:
        logger.exception("Unexpected error during agent invocation")
        return PipelineEvent.AGENT_FAILURE
    finally:
        if (own_bridge or terminal_mcp_error) and bridge is not None:
            bridge.shutdown()


def _start_bridge(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    run_id: str,
) -> RestartAwareMcpBridge:
    raw_fresh_start: object = fresh_bridge_requested()
    if raw_fresh_start is True:
        clear_fresh_bridge_requested()
    if not ctx.parallel_worker:
        workspace = FsWorkspace(
            ctx.workspace_scope.root, allowed_roots=ctx.workspace_scope.allowed_roots
        )
        clear_phase_output_artifacts(
            workspace,
            ctx.effect.phase,
            drain=ctx.effect.drain,
            policy_bundle=ctx.policy_bundle,
        )
    else:
        worker_namespace = ctx.worker_namespace
        if worker_namespace is None and ctx.worker_artifact_dir is not None:
            worker_namespace = ctx.worker_artifact_dir.parent
        if worker_namespace is not None:
            _clear_worker_artifacts(
                ctx.workspace_scope.root,
                run_id,
                worker_namespace=worker_namespace,
            )
    if (
        ctx.effect.activity_only_supervision
        and pipeline_deps.bridge_factory is build_session_bridge
    ):
        return cast(
            "RestartAwareMcpBridge",
            build_session_bridge(
                workspace_root=ctx.workspace_scope.root,
                drain=ctx.effect.drain or ctx.effect.phase,
                agents_policy=ctx.effective_agents_policy,
                transport=ctx.agent_config.transport,
                session_id_prefix=ctx.effect.phase,
                run_id=run_id,
                model_identity=pipeline_deps.model_identity,
                parallel_worker=ctx.parallel_worker,
                worker_namespace=ctx.worker_namespace,
                worker_artifact_dir=ctx.worker_artifact_dir,
                allowed_roots=ctx.workspace_scope.allowed_roots,
                conflict_activity_relay=True,
            ),
        )
    return cast(
        "RestartAwareMcpBridge",
        pipeline_deps.bridge_factory(
            workspace_root=ctx.workspace_scope.root,
            drain=ctx.effect.drain or ctx.effect.phase,
            agents_policy=ctx.effective_agents_policy,
            transport=ctx.agent_config.transport,
            session_id_prefix=ctx.effect.phase,
            run_id=run_id,
            model_identity=pipeline_deps.model_identity,
            parallel_worker=ctx.parallel_worker,
            worker_namespace=ctx.worker_namespace,
            worker_artifact_dir=ctx.worker_artifact_dir,
            allowed_roots=ctx.workspace_scope.allowed_roots,
        ),
    )


def _materialize_master_prompt(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    *,
    required_artifact: RequiredArtifact | None = None,
    required_artifact_resolved: bool = False,
) -> str:
    materialize = pipeline_deps.master_prompt_materializer
    planning_style = _planning_style_for_master_prompt(
        ctx,
        pipeline_deps,
        required_artifact=required_artifact,
        required_artifact_resolved=required_artifact_resolved,
    )
    workspace_root = ctx.workspace_scope.root
    phase_name = str(ctx.effect.phase)
    kwargs: dict[str, object] = {"workspace_root": workspace_root, "name": phase_name}
    if _accepts_keyword(materialize, "worker_namespace"):
        kwargs["worker_namespace"] = ctx.worker_namespace
    if planning_style is not None and _accepts_keyword(materialize, "planning_style"):
        kwargs["planning_style"] = planning_style
    if _accepts_keyword(materialize, "transport"):
        kwargs["transport"] = ctx.agent_config.transport
    dynamic_materialize = cast("_KwargsMaterializeMasterPromptFn", materialize)
    return dynamic_materialize(**kwargs)


def _accepts_keyword(
    materialize: MaterializeMasterPromptFn,
    keyword: str,
) -> bool:
    try:
        parameters = signature(materialize).parameters
    except (TypeError, ValueError):
        return True
    return keyword in parameters or any(
        str(parameter).startswith("**") for parameter in parameters.values()
    )


def _planning_style_for_master_prompt(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    *,
    required_artifact: RequiredArtifact | None,
    required_artifact_resolved: bool = False,
) -> bool | None:
    artifact = required_artifact
    if artifact is None and not required_artifact_resolved:
        policy_bundle = ctx.policy_bundle
        if policy_bundle is None:
            return None
        artifact = pipeline_deps.artifact_requirements_resolver(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=ctx.effect.phase,
            drain=ctx.effect.drain or ctx.effect.phase,
        )
    return artifact is not None and artifact.artifact_type == "plan"


def _resolve_required_artifact(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    required_artifact: RequiredArtifact | None,
) -> RequiredArtifact | None:
    if required_artifact is not None or ctx.policy_bundle is None:
        return required_artifact
    return pipeline_deps.artifact_requirements_resolver(
        ctx.policy_bundle.pipeline,
        ctx.policy_bundle.artifacts,
        phase=ctx.effect.phase,
        drain=ctx.effect.drain or ctx.effect.phase,
    )


def _initial_resume_session_id(ctx: _AgentInvocationCtx) -> str | None:
    if ctx.state is None:
        return None
    retry_intent = ctx.state.agent_retry_intent
    action = retry_intent.action
    prior_session_id = retry_intent.session_id
    if action is None or action == "fresh":
        return None
    return resolve_resume_session_id(
        has_prior_session=bool(prior_session_id),
        prior_session_id=prior_session_id,
        recovery_action=action,
    )


def _check_bridge_health(
    ctx: _AgentInvocationCtx,
    bridge_ctx: _AgentBridgeCtx,
    pipeline_deps: PipelineDeps,
) -> None:
    pipeline_deps.check_mcp_bridge_health_fn(bridge_ctx.bridge)
    if (
        isinstance(bridge_ctx.bridge, RestartAwareMcpBridge)
        and bridge_ctx.bridge.restart_count > 0
        and ctx.display_subscriber is not None
    ):
        ctx.display_subscriber.record_mcp_restart(bridge_ctx.bridge.restart_count)


def _bridge_endpoint_uri(bridge: object) -> str:
    raw_uri: object = getattr(bridge, "agent_endpoint_uri", None)
    if raw_uri is None:
        return ""
    if callable(raw_uri):
        uri_fn = cast("Callable[[], object]", raw_uri)
        try:
            return str(uri_fn())
        except Exception:
            return ""
    return str(raw_uri)


def _make_permission_prompt_listener(ctx: _AgentInvocationCtx) -> Callable[[str], None]:
    def _permission_prompt_listener(message: str) -> None:
        if ctx.display_subscriber is None:
            return
        prefix = "Ralph auto-answered permission prompt: "
        summary = message.removeprefix(prefix)
        prompt_summary, _, selected_option = summary.partition(" → ")
        ctx.display_subscriber.record_permission_prompt_action(
            agent_name=ctx.effect.agent_name,
            prompt_summary=prompt_summary or "permission prompt",
            selected_option=selected_option or "confirm",
        )

    return _permission_prompt_listener


def _build_attempt_invoke_options(
    ctx: _AgentInvocationCtx,
    bridge_ctx: _AgentBridgeCtx,
    pipeline_deps: PipelineDeps,
    resume_session_id: str | None,
    run_id: str,
    required_artifact_override: RequiredArtifact | None = None,
    required_artifact_resolved: bool = False,
    extra_env: dict[str, str] | None = None,
    elapsed_recovery_seconds: float = 0.0,
    workspace_probe: WorkspaceChangeProbe | None = None,
) -> InvokeOptions:
    required_artifact = required_artifact_override
    if (
        required_artifact is None
        and not required_artifact_resolved
        and ctx.policy_bundle is not None
    ):
        required_artifact = pipeline_deps.artifact_requirements_resolver(
            ctx.policy_bundle.pipeline,
            ctx.policy_bundle.artifacts,
            phase=ctx.effect.phase,
            drain=ctx.effect.drain or ctx.effect.phase,
        )
    required_artifact = required_artifact_for_invocation(
        required_artifact,
        ctx.worker_artifact_dir,
    )

    def _emit_pre_output_progress() -> None:
        if ctx.display is not None:
            ctx.display.emit(
                ctx.effect.agent_name,
                "Agent process started; waiting for first output",
            )

    env = {
        str(MCP_RUN_ID_ENV): run_id,
        str(AGENT_LABEL_SCOPE_ENV): run_id,
    }
    if extra_env is not None:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    endpoint_uri = _bridge_endpoint_uri(bridge_ctx.bridge)
    if endpoint_uri:
        env[str(MCP_ENDPOINT_ENV)] = endpoint_uri

    def _supervision_health_error() -> str | None:
        observed_health = ctx.effect.supervision_health_error
        if observed_health is not None:
            error = observed_health()
            if error is not None:
                return error
        health_bridge: object = bridge_ctx.bridge
        if isinstance(health_bridge, RestartAwareMcpBridge):
            return health_bridge.relay_health_error()
        return None

    invoke_options = build_invoke_options_from_config(
        ctx.config.general,
        InvokeRuntimeOptions(
            verbose=ctx.config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            show_progress=False,
            workspace_path=ctx.workspace_scope.root,
            extra_env=env,
            session_id=resume_session_id,
            master_prompt_file=bridge_ctx.master_prompt_file,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=_emit_pre_output_progress,
            permission_prompt_listener=_make_permission_prompt_listener(ctx),
            required_artifact=required_artifact,
            requires_completion_evidence=ctx.effect.requires_completion_evidence,
            workspace_monitor_factory=(
                ctx.invocation_options.workspace_monitor_factory
                if ctx.invocation_options is not None
                else None
            ),
            workspace_probe=workspace_probe,
            phase=str(ctx.effect.phase),
            agent_name=ctx.effect.agent_name,
            connectivity_state_provider=pipeline_deps.connectivity_state_provider,
            is_waiting_state_provider=pipeline_deps.is_waiting_state_provider,
            activity_only_supervision=ctx.effect.activity_only_supervision,
            activity_only_operator_cap_seconds=ctx.effect.activity_only_operator_cap_seconds,
            activity_only_status_interval_seconds=ctx.effect.activity_only_status_interval_seconds,
            relay_activity_sink_register=(
                bridge_ctx.bridge.register_activity_sink
                if isinstance(bridge_ctx.bridge, RestartAwareMcpBridge)
                else None
            ),
            relay_health_error=(
                _supervision_health_error
                if ctx.effect.supervision_health_error is not None
                or isinstance(bridge_ctx.bridge, RestartAwareMcpBridge)
                else None
            ),
        ),
    )
    if ctx.effect.activity_only_supervision:
        invoke_options = replace(
            invoke_options,
            idle_timeout_seconds=ctx.config.conflict_resolution.inactivity_timeout_seconds,
        )
    return _cap_development_invocation_to_phase_deadline(
        ctx,
        invoke_options,
        elapsed_recovery_seconds=elapsed_recovery_seconds,
    )


def _cap_development_invocation_to_phase_deadline(
    ctx: _AgentInvocationCtx,
    options: InvokeOptions,
    *,
    elapsed_recovery_seconds: float = 0.0,
) -> InvokeOptions:
    if ctx.state is None or ctx.policy_bundle is None:
        return options
    timebox = ctx.policy_bundle.pipeline.development_timebox
    if (
        timebox is None
        or not ctx.state.dev_timebox_active
        or ctx.effect.phase != timebox.guarded_entry
    ):
        return options
    remaining_seconds = max(
        0.0,
        timebox.duration_seconds
        - ctx.state.dev_timebox_consumed_seconds
        - elapsed_recovery_seconds,
    )
    idle_timeout_seconds = options.idle_timeout_seconds
    if idle_timeout_seconds is not None:
        idle_timeout_seconds = min(idle_timeout_seconds, remaining_seconds)
    return replace(
        options,
        idle_timeout_seconds=idle_timeout_seconds,
        max_session_seconds=remaining_seconds,
    )


def required_artifact_for_invocation(
    required_artifact: RequiredArtifact | None,
    worker_artifact_dir: Path | None,
) -> RequiredArtifact | None:
    if required_artifact is None or worker_artifact_dir is None:
        return required_artifact
    markdown_path = (
        str(worker_artifact_dir.parent / "handoffs" / Path(required_artifact.markdown_path).name)
        if required_artifact.markdown_path is not None
        else None
    )
    return replace(
        required_artifact,
        artifact_path=str(worker_artifact_dir / f"{required_artifact.artifact_type}.md"),
        markdown_path=markdown_path,
    )


def _consume_attempt_output(
    ctx: _AgentInvocationCtx,
    bridge_ctx: _AgentBridgeCtx,
    attempt_prompt_file: str,
    options: InvokeOptions,
    raw_output: deque[str],
    rendered_output: deque[str],
    capture_session_id: Callable[[str], None],
    pipeline_deps: PipelineDeps,
    *,
    agent_label_scope: str,
    raw_line_sink: Callable[[str], None] | None = None,
) -> None:
    on_mcp_restart = (
        ctx.display_subscriber.record_mcp_restart if ctx.display_subscriber is not None else None
    )
    get_heartbeat = pipeline_deps.heartbeat_policy_from_env_fn

    _raw_transport: object = getattr(ctx.agent_config, "transport", None)
    _agent_config_transport: AgentTransport = (
        _raw_transport if isinstance(_raw_transport, AgentTransport) else AgentTransport.GENERIC
    )
    _agent_registry = AgentRegistry()
    _subagent_pid_registry, _subagent_pid_source = _agent_registry.build_subagent_pid_registry(
        _agent_config_transport
    )
    _subagent_source_label = _agent_config_transport.value
    options = replace(
        options,
        subagent_pid_registry=_subagent_pid_registry,
        subagent_pid_source=_subagent_pid_source,
    )
    if options.initial_session_id is not None:
        capture_session_id(options.initial_session_id)

    def _run_invocation() -> None:
        output_lines = ctx.deps.invoke_agent(ctx.agent_config, attempt_prompt_file, options=options)
        if verbosity_rank(ctx.verbosity) >= VERBOSITY_RANK[Verbosity.NORMAL]:
            stream_parsed_agent_activity(
                output_lines,
                str(ctx.agent_config.json_parser),
                ctx.effect.agent_name,
                ctx.display,
                transport=ctx.agent_config.transport,
                display_context=ctx.resolved_display_context,
                raw_output_sink=raw_output,
                rendered_output_sink=rendered_output,
                session_id_sink=capture_session_id,
                raw_line_sink=raw_line_sink,
                agent_config=ctx.agent_config,
                subagent_pid_registry=_subagent_pid_registry,
                subagent_source_label=_subagent_source_label,
            )
            return
        for line in output_lines:
            text_line = str(line)
            raw_output.append(text_line)
            if raw_line_sink is not None:
                raw_line_sink(text_line)
            session_id = extract_transport_session_id((text_line,))
            if session_id is not None:
                capture_session_id(session_id)

    try:
        with pipeline_deps.mcp_supervisor_factory(
            bridge_ctx.bridge,
            check_interval=get_heartbeat().interval,
            on_restart=on_mcp_restart,
            agent_label_scope=agent_label_scope,
        ):
            _run_invocation()
    except ctx.deps.agent_invocation_error as exc:
        raise _enrich_invocation_error(exc, raw_output, rendered_output) from exc


def _enrich_invocation_error(
    exc: Exception,
    raw_output: deque[str],
    rendered_output: deque[str],
) -> Exception:
    if not isinstance(exc, AgentInvocationError):
        return exc
    extra = [
        line for line in (*raw_output, *rendered_output) if line and line not in exc.parsed_output
    ]
    exc.parsed_output = [*extra, *exc.parsed_output]
    return exc


def build_agent_recovery_plan(
    recovery_input: AgentRecoveryInput,
    outcome: AttemptOutcome | None = None,
) -> AgentRecoveryPlan | None:
    recorded_at = recovery_input.clock.epoch()
    record_watchdog_fire(
        recovery_input.workspace_root,
        recovery_input.exc,
        agent=recovery_input.effect.agent_name,
        phase=str(recovery_input.effect.phase),
        recorded_at=recorded_at,
    )
    if recovery_input.attempt_index >= recovery_input.max_recovery_attempts:
        return None
    if outcome is not None:
        if not outcome.retries_same_agent:
            return None
        reason: str = outcome.reason
    else:
        retryable_reason = retryable_agent_failure_reason(
            recovery_input.exc, recovery_input.inactivity_error_type
        )
        if retryable_reason is None:
            return None
        reason = retryable_reason
    completion_recovery = isinstance(recovery_input.exc, AgyIncompleteExitError)
    if completion_recovery and recovery_input.completion_reprompt_used:
        return None
    untruncated = _is_stale_session_failure(recovery_input.exc)
    context_lines = _recovery_context_lines(
        recovery_input.exc,
        recovery_input.raw_output,
        recovery_input.rendered_output,
        untruncated=untruncated,
    )
    session_id = _resolve_recovery_session_id(
        recovery_input.exc,
        recovery_input.extracted_session_id,
        recovery_input.inactivity_error_type,
    )
    retry_intent = resolve_retry_intent(
        outcome if outcome is not None else recovery_input.exc,
        phase=str(recovery_input.effect.phase),
        agent=recovery_input.effect.agent_name,
        session_id=session_id,
        inactivity_error_type=recovery_input.inactivity_error_type,
        exc=recovery_input.exc,
    )
    reset_tool_registry = retry_intent.reset_tool_registry if retry_intent is not None else False
    recovery_action = recovery_action_for_failure_reason(
        type(recovery_input.exc).__name__,
        has_prior_session=bool(session_id),
        reset_tool_registry=reset_tool_registry,
    )
    prompt_stale_session_id = recovery_input.stale_session_id if untruncated else None
    prompt_transport = recovery_input.transport if untruncated else None
    prompt_model = recovery_input.model if untruncated else None
    prompt_file = _retry_prompt_file_for_context(
        workspace_root=recovery_input.workspace_root,
        prompt_file=recovery_input.effect.prompt_file,
        reason=reason,
        context_lines=context_lines,
        recovery_action=recovery_action,
        untruncated=untruncated,
        stale_session_id=prompt_stale_session_id,
        transport=prompt_transport,
        model=prompt_model,
        run_id=recovery_input.run_id,
        completion_recovery=completion_recovery,
        drain=recovery_input.effect.drain,
        worker_namespace=recovery_input.worker_namespace,
        detail=watchdog_fire_detail(recovery_input.exc),
    )
    return AgentRecoveryPlan(
        prompt_file=prompt_file,
        session_id=session_id,
        reason=reason,
        recovery_action=recovery_action,
        stale_session_id=prompt_stale_session_id,
        transport=prompt_transport,
        model=prompt_model,
    )


def _resolve_recovery_session_id(
    exc: Exception,
    extracted_session_id: str | None,
    inactivity_error_type: type[Exception],
) -> str | None:
    if _failure_requires_fresh_session(exc, inactivity_error_type):
        return None
    resumable_session_id = cast("object", getattr(exc, "resumable_session_id", None))
    if isinstance(resumable_session_id, str) and resumable_session_id:
        return resumable_session_id
    if extracted_session_id:
        return extracted_session_id
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list) and parsed_output:
        parsed_session_id = extract_transport_session_id(tuple(str(item) for item in parsed_output))
        if parsed_session_id:
            return parsed_session_id
    return None


def recovery_attempts_for_effect(effect: InvokeAgentEffect, config: UnifiedConfig) -> int:
    if effect.activity_only_supervision:
        return 0
    return _same_agent_recovery_attempts(config)


def surfaces_supervision_error(effect: InvokeAgentEffect, exc: BaseException) -> bool:
    return effect.activity_only_supervision and isinstance(
        exc, (AgentInactivityTimeoutError, SupervisionInfrastructureError)
    )


def _same_agent_recovery_attempts(config: UnifiedConfig) -> int:
    raw = cast("object", getattr(config.general, "max_same_agent_retries", 10))
    return raw if isinstance(raw, int) and raw >= 0 else 10


def _failure_requires_fresh_session(exc: Exception, inactivity_error_type: type[Exception]) -> bool:
    if isinstance(exc, inactivity_error_type):
        session_resume_safe = cast("object", getattr(exc, "session_resume_safe", False))
        return session_resume_safe is not True
    return contains_casefolded_marker(_recovery_error_parts(exc), _SESSION_NOT_FOUND_SUBSTRINGS)


def _is_stale_session_failure(exc: Exception) -> bool:
    return contains_casefolded_marker(_recovery_error_parts(exc), _SESSION_NOT_FOUND_SUBSTRINGS)


def _recovery_error_parts(exc: Exception) -> list[str]:
    return failure_detail_parts(exc)


def _build_recovery_input_for_attempt(
    *,
    ctx: _AgentInvocationCtx,
    exc: Exception,
    state: _AttemptState,
    session_id: str | None,
    raw_output: deque[str],
    rendered_output: deque[str],
    run_id: str,
) -> AgentRecoveryInput:
    _transport_obj: object = getattr(ctx.agent_config, "transport", None)
    _transport_value: str | None = (
        _transport_obj.value if isinstance(_transport_obj, AgentTransport) else None
    )
    _model_value: str | None = getattr(ctx.agent_config, "model", None)
    is_stale_session_failure = _is_stale_session_failure(exc)
    return AgentRecoveryInput(
        exc=exc,
        attempt_index=0,
        max_recovery_attempts=ctx.max_recovery_attempts,
        effect=ctx.effect,
        workspace_root=ctx.workspace_scope.root,
        raw_output=list(raw_output),
        rendered_output=list(rendered_output),
        extracted_session_id=(extract_transport_session_id(tuple(raw_output)) or session_id),
        inactivity_error_type=AgentInactivityTimeoutError,
        stale_session_id=(
            state.last_agent_session_id or state.resume_session_id or session_id
            if is_stale_session_failure
            else None
        ),
        transport=_transport_value if is_stale_session_failure else None,
        model=_model_value if is_stale_session_failure else None,
        run_id=run_id,
        completion_reprompt_used=state.completion_reprompt_used,
        worker_namespace=ctx.worker_namespace,
        clock=ctx.clock,
    )


def _recovery_context_lines(
    exc: Exception,
    raw_output: list[str],
    rendered_output: list[str],
    *,
    _fn: Callable[[Exception], list[str]] | None = None,
    untruncated: bool = False,
) -> list[str]:
    _error_parts_fn = _fn or _recovery_error_parts
    if rendered_output:
        return _tail_recovery_context_lines(rendered_output, untruncated=untruncated)
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list) and parsed_output:
        return _tail_recovery_context_lines(
            [str(item) for item in parsed_output], untruncated=untruncated
        )
    stripped_raw = [line.strip() for line in raw_output if line.strip()]
    if stripped_raw:
        return _tail_recovery_context_lines(stripped_raw, untruncated=untruncated)
    error_parts = [part.strip() for part in _error_parts_fn(exc) if part.strip()]
    return _tail_recovery_context_lines(error_parts, untruncated=untruncated)


def _tail_recovery_context_lines(lines: list[str], *, untruncated: bool = False) -> list[str]:
    if untruncated:
        return list(lines)
    if len(lines) <= _RECOVERY_CONTEXT_LINES:
        return lines
    omitted = len(lines) - _RECOVERY_CONTEXT_LINES
    return [f"<previous log omitted> ({omitted} earlier lines)", *lines[-_RECOVERY_CONTEXT_LINES:]]


def _condense_recovery_context_lines(
    context_lines: list[str], *, untruncated: bool = False
) -> list[str]:
    if untruncated:
        return [_condense_recovery_line(line) for line in context_lines]
    condensed = [_condense_recovery_line(line) for line in context_lines]
    if (
        len(context_lines) > _RECOVERY_CONTEXT_LINES
        and condensed
        and not condensed[0].startswith("<previous log omitted>")
    ):
        omitted = len(context_lines) - _RECOVERY_CONTEXT_LINES
        return [
            f"<previous log omitted> ({omitted} earlier lines)",
            *condensed[-_RECOVERY_CONTEXT_LINES:],
        ]
    return condensed


def _condense_recovery_line(line: str) -> str:
    stripped = line.strip()
    if len(stripped) <= _RECOVERY_CONTEXT_MAX_CHARS:
        return stripped
    return stripped[:_RECOVERY_CONTEXT_MAX_CHARS].rstrip() + " ... (truncated)"


def _validation_retry_context(
    workspace_root: Path,
    drain: str | None,
    worker_namespace: Path | None,
) -> str:
    if drain is None:
        return ""
    from ralph.phases.required_artifacts import read_validation_retry_hint

    return read_validation_retry_hint(
        workspace_root,
        drain,
        worker_namespace=worker_namespace,
    )


def _retry_fingerprint_file(
    *,
    workspace_root: Path,
    drain: str | None,
    worker_namespace: Path | None,
    reason: str | None = None,
) -> Path | None:
    scope = drain or reason
    if scope is None:
        return None
    hint_file = (
        worker_namespace / "tmp" / f"last_retry_error_{scope}.txt"
        if worker_namespace is not None
        else workspace_root / retry_hint_path(scope)
    )
    return Path(retry_fingerprint_path_for_hint(hint_file.as_posix()))


def _retry_prompt_file_for_context(
    *,
    workspace_root: Path,
    prompt_file: str,
    reason: str,
    context_lines: list[str],
    recovery_action: str | None = None,
    untruncated: bool = False,
    stale_session_id: str | None = None,
    transport: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    completion_recovery: bool = False,
    drain: str | None = None,
    worker_namespace: Path | None = None,
    detail: str | None = None,
) -> str:
    return _write_agent_retry_prompt(
        workspace_root=workspace_root,
        prompt_file=prompt_file,
        reason=reason,
        context_lines=context_lines,
        recovery_action=recovery_action,
        untruncated=untruncated,
        stale_session_id=stale_session_id,
        transport=transport,
        model=model,
        run_id=run_id,
        completion_recovery=completion_recovery,
        drain=drain,
        worker_namespace=worker_namespace,
        detail=detail,
    )


def _resume_mode_tail(prompt_path: Path) -> str:
    return (
        "CONTINUE FROM WHERE YOU LEFT OFF: The prior session for this "
        "task is being resumed. Do not restart from the beginning; "
        f"refer to the original prompt at {prompt_path} for context "
        "only and pick up from the most recent state in the resumed session."
    )


def _stale_session_recovery_block(
    *, stale_session_id: str, transport: str | None, model: str | None
) -> str:
    transport_label = transport if transport else "unknown"
    model_label = model if model else "unknown"
    return (
        "STALE SESSION RECOVERY\n"
        f"The previous attempt's session id `{stale_session_id}` was rejected by "
        f"`{transport_label}` (model={model_label}).\n"
        "You are starting a FRESH session. Do NOT attempt to re-use that session id.\n"
        "Treat the original task and the prior output summary below as your starting context.\n"
        "the transport rejected the prior session id BEFORE the agent could produce any work.\n"
        "the prior output is INFORMATIONAL ONLY and may be empty, incoherent, or confused.\n"
        "the ORIGINAL TASK PROMPT below is your ONLY source of truth.\n"
        "do not treat the prior attempt output as ground truth; "
        "execute the task as a FRESH attempt."
    )


def _write_retry_context_file(
    *,
    workspace_root: Path,
    context_lines: list[str],
    untruncated: bool = False,
    run_id: str | None = None,
) -> Path:
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    context_path = prompt_dir / f"agent_retry_context_{uuid.uuid4().hex}.md"
    condensed = _condense_recovery_context_lines(context_lines, untruncated=untruncated)
    summary = "\n".join(condensed) if condensed else "(no output captured)"
    context_path.write_text(summary, encoding="utf-8")
    if run_id is not None:
        from ralph.workspace.agent_dir_retention import register_temporary_path_owner

        register_temporary_path_owner(workspace_root, context_path, run_id)
    return context_path


def _completion_recovery_block() -> str:
    return (
        "COMPLETION RECOVERY INSTRUCTION\n"
        "You are running NON-INTERACTIVELY: no user will read or answer questions.\n"
        "Do NOT wait for user input and do NOT ask for clarification; make "
        "reasonable decisions autonomously and finish the assigned work.\n"
        "The previous attempt exited without the required completion evidence, "
        "which failed the run. Before exiting you MUST:\n"
        "1. Finish the assigned work from the ORIGINAL TASK PROMPT above.\n"
        "2. If the task requires an artifact, submit it through the canonical "
        "MCP tool `ralph_submit_md_artifact`.\n"
        "3. Call the `declare_complete` MCP tool to declare the task complete.\n"
        "Exiting without this evidence fails the run; this is the only "
        "automatic recovery attempt."
    )


def _write_agent_retry_prompt(
    *,
    workspace_root: Path,
    prompt_file: str,
    reason: str,
    context_lines: list[str],
    recovery_action: str | None = None,
    untruncated: bool = False,
    stale_session_id: str | None = None,
    transport: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    completion_recovery: bool = False,
    drain: str | None = None,
    worker_namespace: Path | None = None,
    detail: str | None = None,
) -> str:
    prompt_path = Path(prompt_file)
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = prompt_dir / f"agent_retry_{uuid.uuid4().hex}.md"
    context_path = _write_retry_context_file(
        workspace_root=workspace_root,
        context_lines=context_lines,
        untruncated=untruncated,
        run_id=run_id,
    )
    condensed = _condense_recovery_context_lines(context_lines, untruncated=untruncated)
    summary = "\n".join(condensed) if condensed else "(no output captured)"
    validation_hint = _validation_retry_context(workspace_root, drain, worker_namespace)
    validation_retry = reason == "ArtifactValidation" or bool(validation_hint)
    fingerprint_file = _retry_fingerprint_file(
        workspace_root=workspace_root,
        drain=drain,
        worker_namespace=worker_namespace,
        reason=reason,
    )
    current_fingerprint = retry_hint_fingerprint(str(drain or reason), detail or reason)
    prior_fingerprint = ""
    if fingerprint_file is not None and DEFAULT_FILE_BACKEND.exists(fingerprint_file):
        try:
            prior_fingerprint = DEFAULT_FILE_BACKEND.read_text(
                fingerprint_file, encoding="utf-8"
            ).strip()
        except (OSError, ValueError):
            prior_fingerprint = ""
    premise = premise_check_line(current_fingerprint, prior_fingerprint)
    if premise and PREMISE_CHECK_LINE in validation_hint:
        premise = ""
    error_block = build_retry_error_block(
        failure_summary=f"the previous attempt failed because of {reason}",
        detail=detail,
        prompt_path=str(prompt_path),
        context_path=str(context_path),
        validation=validation_retry,
    )
    if fingerprint_file is not None:
        fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            fingerprint_file,
            current_fingerprint,
            encoding="utf-8",
        )
    validation_block = (
        "VALIDATION ERRORS (ACCUMULATED)\n"
        f"{validation_hint}\n\n"
        "The retained draft contains the prior work. Repair it in place before resubmitting."
        if validation_hint
        else ""
    )
    validation_footer = build_validation_retry_footer() if validation_retry else ""
    if recovery_action in {"resume", "new_session_with_id"}:
        tail = _resume_mode_tail(prompt_path)
        retry_prompt_path.write_text(
            (
                f"{error_block}\n\n"
                f"{premise + chr(10) + chr(10) if premise else ''}"
                f"{validation_block + chr(10) + chr(10) if validation_block else ''}"
                f"PREVIOUS OUTPUT SUMMARY EXCERPT:\n{summary}\n\n{tail}"
                f"{chr(10) + chr(10) + validation_footer if validation_footer else ''}\n"
            ),
            encoding="utf-8",
        )
        if run_id is not None:
            from ralph.workspace.agent_dir_retention import register_temporary_path_owner

            register_temporary_path_owner(workspace_root, retry_prompt_path, run_id)
        return str(retry_prompt_path)
    base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    body_parts: list[str] = [
        error_block,
        "PREVIOUS OUTPUT SUMMARY EXCERPT:",
        summary,
        "",
        "ORIGINAL TASK PROMPT:",
        base_prompt,
    ]
    if validation_block:
        body_parts[1:1] = ["", validation_block]
    if premise:
        body_parts[1:1] = ["", premise]
    if stale_session_id and not condensed:
        body_parts[3:3] = [
            "STALE SESSION EMPTY PRIOR OUTPUT",
            "",
            (
                f"the transport rejected the prior session id `{stale_session_id}` "
                "before any output was produced."
            ),
            (
                "the PREVIOUS OUTPUT SUMMARY EXCERPT above is empty BY DESIGN "
                "for stale-session retries -- do NOT interpret this as the "
                "prior agent having failed to do work."
            ),
            "the ORIGINAL TASK PROMPT below is your ONLY source of truth.",
            "Execute the task as a FRESH attempt.",
            "",
        ]
    if stale_session_id:
        body_parts.extend(
            [
                "",
                _stale_session_recovery_block(
                    stale_session_id=stale_session_id,
                    transport=transport,
                    model=model,
                ),
            ]
        )
    if completion_recovery:
        body_parts.extend(["", _completion_recovery_block()])
    if validation_footer:
        body_parts.extend(["", validation_footer])
    retry_prompt_path.write_text(
        "\n".join(body_parts) + "\n",
        encoding="utf-8",
    )
    if run_id is not None:
        from ralph.workspace.agent_dir_retention import register_temporary_path_owner

        register_temporary_path_owner(workspace_root, retry_prompt_path, run_id)
    return str(retry_prompt_path)


recovery_error_parts = _recovery_error_parts
resolve_recovery_session_id = _resolve_recovery_session_id
recovery_context_lines = _recovery_context_lines
AGENT_NOT_FOUND_REASON = "AgentNotFound"

retry_prompt_file_for_context = _retry_prompt_file_for_context
write_agent_retry_prompt = _write_agent_retry_prompt


def _mark_next_attempt_gets_fresh_bridge(exc: Exception | None = None) -> None:
    del exc
    mark_fresh_bridge_requested()


mark_next_attempt_gets_fresh_bridge = _mark_next_attempt_gets_fresh_bridge


def _set_last_captured_retry_intent(intent: AgentRetryIntent) -> None:
    _set_last_captured_retry_intent_state(intent)


def set_last_captured_retry_intent(intent: AgentRetryIntent) -> None:
    _set_last_captured_retry_intent_state(intent)


def _write_terminal_missing_artifact_hint(
    ctx: _AgentInvocationCtx,
    exc: Exception,
    required_artifact: RequiredArtifact | None,
) -> None:
    if required_artifact is None or not is_unsubmitted_artifact_failure(failure_detail_parts(exc)):
        return
    workspace_root = ctx.workspace_scope.root
    if (workspace_root / required_artifact.artifact_path).exists():
        return
    try:
        registry = (
            build_required_artifacts(ctx.policy_bundle.artifacts)
            if ctx.policy_bundle is not None
            else None
        )
        phase_name = str(ctx.effect.phase)
        hint_file = workspace_root / retry_hint_path(
            phase_name,
            pipeline_policy=ctx.policy_bundle.pipeline if ctx.policy_bundle is not None else None,
        )
        fingerprint_file = workspace_root / retry_fingerprint_path_for_hint(hint_file.as_posix())
        hint = build_retry_hint(
            phase_name,
            str(exc),
            registry=registry,
        )
        hint_file.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(DEFAULT_FILE_BACKEND, hint_file, hint, encoding="utf-8")
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            fingerprint_file,
            retry_hint_fingerprint(phase_name, str(exc), registry=registry),
            encoding="utf-8",
        )
    except Exception:
        return


def _handle_terminal_agent_invocation_error(
    exc: Exception,
    *,
    agent_invocation_error_sink: Callable[[Exception], object] | None,
    observation: AgentFailureEvent | None = None,
) -> PipelineEvent | AgentFailureEvent:
    _set_pi_context_exhaustion_intent(exc)
    if isinstance(exc, McpServerError):
        _set_last_captured_retry_intent(
            AgentRetryIntent(
                failure_reason="McpServerError",
                chain_cap_decision="reset",
            )
        )
    facts = observation.facts if observation is not None and observation.facts is not None else None
    if facts is None:
        facts = attempt_facts_for_failure(exc)
    logger.error(
        "Agent invocation failed: {}",
        render_attempt_failure_log_line(facts, condense_failure_text(str(exc))),
    )
    agent_invocation_error_sink and agent_invocation_error_sink(exc)
    if observation is not None:
        return replace(observation, reason=str(exc))
    return PipelineEvent.AGENT_FAILURE


def _set_pi_context_exhaustion_intent(exc: Exception) -> None:
    if isinstance(exc, PiContextExhaustedExitError):
        _set_last_captured_retry_intent(
            AgentRetryIntent(
                failure_reason=type(exc).__name__,
                skip_same_agent_retries=True,
                failed_agent_name=exc.agent_name,
            )
        )


def pop_last_captured_retry_intent() -> AgentRetryIntent:
    return _pop_last_captured_retry_intent()


__all__ = [
    "AGENT_NOT_FOUND_REASON",
    "mark_next_attempt_gets_fresh_bridge",
    "pop_last_captured_retry_intent",
    "set_last_captured_retry_intent",
    "stage_files",
]
