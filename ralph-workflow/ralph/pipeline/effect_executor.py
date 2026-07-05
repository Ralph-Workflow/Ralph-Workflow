"""Agent and commit effect execution for the pipeline runner."""

from __future__ import annotations

import threading as _threading
import uuid
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    extract_transport_session_id,
    invoke_agent,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.agents.invoke._open_code_resumable_exit_error import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
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
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.server.lifecycle import McpServerError, RestartAwareMcpBridge
from ralph.pipeline._agent_bridge_ctx import _AgentBridgeCtx
from ralph.pipeline._agent_invocation_ctx import _AgentInvocationCtx
from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.agent_execution_deps import build_agent_execution_deps
from ralph.pipeline.agent_recovery_input import AgentRecoveryInput
from ralph.pipeline.agent_recovery_plan import AgentRecoveryPlan
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.agent_retry_intent import (
    AgentRetryIntent,
    cleared_agent_retry_intent,
)
from ralph.pipeline.commit_executor import clear_phase_output_artifacts
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, verbosity_rank
from ralph.pipeline.phase_transition import show_phase_start_with_context
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.pipeline.session_bridge import reset_tool_registry_callback
from ralph.pipeline.waiting_dispatch import dispatch_waiting_event
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.recovery.classifier import SESSION_NOT_FOUND_SUBSTRINGS as _SESSION_NOT_FOUND_SUBSTRINGS
from ralph.recovery.failure_details import contains_casefolded_marker, failure_detail_parts
from ralph.recovery.retry_prompt import build_retry_error_block
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.agent_execution_deps import (
        _InvokeAgentFn,
        _ShowPhaseStartFn,
    )
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

_VERBOSE_LOG_LEVEL = 2
_AGENT_RAW_OUTPUT_TAIL_LINES = 256
_AGENT_RENDERED_OUTPUT_TAIL_LINES = 64
_RECOVERY_CONTEXT_LINES = 12
_RECOVERY_CONTEXT_MAX_CHARS = 240
_PORCELAIN_STATUS_PREFIX_LEN = 3


@dataclass(frozen=True)
class _AttemptResult:
    event: PipelineEvent | None
    next_prompt_file: str
    next_session_id: str | None


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    *,
    bridge: RestartAwareMcpBridge | None = None,
    raw_output_sink: deque[str] | None = None,
    rendered_output_sink: deque[str] | None = None,
    run_id: str | None = None,
    required_artifact: RequiredArtifact | None = None,
    session_id: str | None = None,
    extra_env: dict[str, str] | None = None,
    raise_resumable_exit: bool = False,
    agent_invocation_error_sink: Callable[[Exception], object] | None = None,
    **opts: object,
) -> PipelineEvent:
    """Execute an agent-invocation effect end-to-end, including MCP server lifecycle."""
    display = cast("ParallelDisplay | None", opts.get("display"))
    display_context = cast("DisplayContext | None", opts.get("display_context"))
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
    state = cast("PipelineState | None", opts.get("state"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    resolved_display_context = get_display_context(display, display_context)
    registry = _registry_from_pipeline_deps(pipeline_deps, config)
    agent_config = cast("AgentConfig | None", registry.get(effect.agent_name))
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
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

    show_phase_start_cb: _ShowPhaseStartFn | None = None
    if state is not None and policy_bundle is not None:
        show_phase_start_cb = show_phase_start_with_context
    deps = build_agent_execution_deps(
        pipeline_deps=pipeline_deps,
        invoke_agent=_invoke_agent_from_registry_or_opts(opts),
        agent_invocation_error=_agent_invocation_error_from_opts(opts),
        agent_registry=AgentRegistry,
        show_phase_start_cb=show_phase_start_cb,
        set_session_id_cb=cast(
            "Callable[[str | None], None] | None", opts.get("set_session_id_cb")
        ),
    )
    ctx = _AgentInvocationCtx(
        effect=effect,
        config=config,
        deps=deps,
        workspace_scope=workspace_scope,
        verbosity=verbosity,
        resolved_display_context=resolved_display_context,
        display_subscriber=display_subscriber,
        max_recovery_attempts=_same_agent_recovery_attempts(config),
        effective_agents_policy=effective_agents_policy,
        state=state,
        policy_bundle=policy_bundle,
        waiting_listener=waiting_listener,
        agent_config=agent_config,
        display=display,
        worker_namespace=cast("Path | None", opts.get("worker_namespace")),
        worker_artifact_dir=cast("Path | None", opts.get("worker_artifact_dir")),
        parallel_worker=cast("bool", opts.get("parallel_worker", False)),
    )
    return _invoke_agent_with_recovery(
        ctx,
        pipeline_deps,
        bridge=bridge,
        raw_output_sink=raw_output_sink,
        rendered_output_sink=rendered_output_sink,
        run_id=run_id,
        required_artifact=required_artifact,
        session_id=session_id,
        extra_env=extra_env,
        on_retry_failure=cast("Callable[[list[str]], object] | None", opts.get("on_retry_failure")),
        raise_resumable_exit=raise_resumable_exit,
        agent_invocation_error_sink=agent_invocation_error_sink,
    )


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
    opts: dict[str, object],
) -> _InvokeAgentFn:
    invoke = cast("_InvokeAgentFn | None", opts.get("invoke_agent"))
    if invoke is not None:
        return invoke
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
    # Captured from ``ctx.state.last_agent_session_id`` at attempt entry so
    # the retry prompt can name the rejected session id without re-reading
    # ``ctx.state`` inside the recovery path. None when no prior session
    # id is recorded.
    last_agent_session_id: str | None = None


def _safe_last_agent_session_id(state: PipelineState | None) -> str | None:
    """Read ``last_agent_session_id`` defensively, returning None on any issue."""
    if state is None:
        return None
    raw_value: object = getattr(state, "last_agent_session_id", None)
    if isinstance(raw_value, str):
        return raw_value
    return None


def _invoke_agent_with_recovery(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    *,
    bridge: RestartAwareMcpBridge | None = None,
    raw_output_sink: deque[str] | None = None,
    rendered_output_sink: deque[str] | None = None,
    run_id: str | None = None,
    required_artifact: RequiredArtifact | None = None,
    session_id: str | None = None,
    extra_env: dict[str, str] | None = None,
    on_retry_failure: Callable[[list[str]], object] | None = None,
    raise_resumable_exit: bool = False,
    agent_invocation_error_sink: Callable[[Exception], object] | None = None,
) -> PipelineEvent:
    own_bridge = bridge is None
    effective_run_id = run_id or str(uuid.uuid4())
    if bridge is None:
        bridge = _start_bridge(ctx, pipeline_deps, effective_run_id)
    elif run_id is None:
        effective_run_id = cast("str", getattr(bridge, "run_id", effective_run_id))
    try:
        system_prompt_file = _materialize_system_prompt(ctx, pipeline_deps)
        bridge_ctx = _AgentBridgeCtx(
            bridge=bridge, session=cast("object", None), system_prompt_file=system_prompt_file
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
        state = _AttemptState(
            prompt_file=ctx.effect.prompt_file,
            resume_session_id=session_id or _initial_resume_session_id(ctx),
            last_agent_session_id=_safe_last_agent_session_id(ctx.state),
        )
        progress_guard = RetryProgressGuard()

        def attempt_fn(
            retry_session_id: str | None,
            capture_session_id: Callable[[str], None],
        ) -> _AttemptResult:
            session_id = retry_session_id or state.resume_session_id
            options = _build_attempt_invoke_options(
                ctx,
                bridge_ctx,
                pipeline_deps,
                session_id,
                effective_run_id,
                required_artifact,
                extra_env=extra_env,
            )
            try:
                _check_bridge_health(ctx, bridge_ctx, pipeline_deps)
                if isinstance(bridge_ctx.bridge, RestartAwareMcpBridge):
                    bridge_ctx.bridge.reset_session_budget()
                _consume_attempt_output(
                    ctx,
                    bridge_ctx,
                    state.prompt_file,
                    options,
                    raw_output,
                    rendered_output,
                    capture_session_id,
                    pipeline_deps,
                )
                final_session_id = extract_transport_session_id(tuple(raw_output)) or session_id
                if ctx.deps.set_session_id_cb is not None:
                    ctx.deps.set_session_id_cb(final_session_id)
                _set_last_captured_retry_intent(cleared_agent_retry_intent())
                return _AttemptResult(PipelineEvent.AGENT_SUCCESS, state.prompt_file, session_id)
            except ctx.deps.agent_invocation_error as exc:
                recovery_plan = build_agent_recovery_plan(
                    _build_recovery_input_for_attempt(
                        ctx=ctx,
                        exc=exc,
                        state=state,
                        session_id=session_id,
                        raw_output=raw_output,
                        rendered_output=rendered_output,
                    )
                )
                if recovery_plan is None:
                    _set_last_captured_retry_intent(cleared_agent_retry_intent())
                    raise
                failure_signature = retry_failure_signature(
                    recovery_plan.reason, list(rendered_output)
                )
                if progress_guard.record(failure_signature):
                    logger.error(
                        "Aborting agent '{}' retries: {} consecutive identical "
                        "zero-progress failures. Refusing to spin; failing fast.",
                        ctx.effect.agent_name,
                        MAX_IDENTICAL_RETRY_ATTEMPTS,
                    )
                    _set_last_captured_retry_intent(cleared_agent_retry_intent())
                    return _AttemptResult(
                        PipelineEvent.AGENT_FAILURE, state.prompt_file, session_id
                    )
                state.prompt_file = recovery_plan.prompt_file
                state.resume_session_id = recovery_plan.session_id
                retry_intent = resolve_retry_intent(
                    exc,
                    phase=str(ctx.effect.phase),
                    agent=ctx.effect.agent_name,
                    session_id=recovery_plan.session_id,
                    inactivity_error_type=AgentInactivityTimeoutError,
                )
                _set_last_captured_retry_intent(
                    retry_intent if retry_intent is not None else cleared_agent_retry_intent()
                )
                raise

        try:
            result = run_with_direct_mcp_recovery(
                attempt_fn,
                max_retries=ctx.max_recovery_attempts,
                reset_tool_registry=cast(
                    "Callable[[], object] | None",
                    reset_tool_registry_callback(bridge_ctx.bridge),
                ),
                on_retry_failure=on_retry_failure,
                retry_resumable_exit=True,
            )
            return result.event if result.event is not None else PipelineEvent.AGENT_FAILURE
        except McpServerError as exc:
            logger.error(
                "MCP server failed permanently after {} restart(s): {}", exc.restart_count, exc
            )
            _set_last_captured_retry_intent(
                AgentRetryIntent(action="fresh", failure_reason="McpServerError")
            )
            return PipelineEvent.AGENT_FAILURE
        except ctx.deps.agent_invocation_error as exc:
            if raise_resumable_exit and isinstance(exc, OpenCodeResumableExitError):
                raise
            agent_invocation_error_sink and agent_invocation_error_sink(exc)
            return PipelineEvent.AGENT_FAILURE
    except McpConfigError:
        raise
    except OpenCodeResumableExitError:
        raise
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return PipelineEvent.AGENT_FAILURE
    finally:
        if own_bridge and bridge is not None:
            bridge.shutdown()


def _start_bridge(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
    run_id: str,
) -> RestartAwareMcpBridge:
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


def _materialize_system_prompt(
    ctx: _AgentInvocationCtx,
    pipeline_deps: PipelineDeps,
) -> str:
    _materialize = pipeline_deps.system_prompt_materializer
    try:
        return _materialize(
            workspace_root=ctx.workspace_scope.root,
            name=str(ctx.effect.phase),
            worker_namespace=ctx.worker_namespace,
        )
    except TypeError:
        return _materialize(
            workspace_root=ctx.workspace_scope.root,
            name=str(ctx.effect.phase),
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
    """Return the agent endpoint URI from a bridge, or an empty string if unavailable."""
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
    extra_env: dict[str, str] | None = None,
) -> InvokeOptions:
    required_artifact = required_artifact_override
    if required_artifact is None and ctx.policy_bundle is not None:
        required_artifact = pipeline_deps.artifact_requirements_resolver(
            ctx.policy_bundle.pipeline,
            ctx.policy_bundle.artifacts,
            phase=ctx.effect.phase,
            drain=ctx.effect.drain or ctx.effect.phase,
        )

    def _emit_pre_output_progress() -> None:
        if ctx.display is not None:
            ctx.display.emit(
                ctx.effect.agent_name,
                "Agent process started; waiting for first output",
            )

    if extra_env is not None:
        env: dict[str, str] = {str(k): str(v) for k, v in extra_env.items()}
    else:
        env = {
            str(MCP_RUN_ID_ENV): run_id,
            str(AGENT_LABEL_SCOPE_ENV): run_id,
        }
    endpoint_uri = _bridge_endpoint_uri(bridge_ctx.bridge)
    if endpoint_uri:
        env[str(MCP_ENDPOINT_ENV)] = endpoint_uri
    return build_invoke_options_from_config(
        ctx.config.general,
        InvokeRuntimeOptions(
            verbose=ctx.config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            show_progress=False,
            workspace_path=ctx.workspace_scope.root,
            extra_env=env,
            session_id=resume_session_id,
            system_prompt_file=bridge_ctx.system_prompt_file,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=_emit_pre_output_progress,
            permission_prompt_listener=_make_permission_prompt_listener(ctx),
            required_artifact=required_artifact,
            pure=ctx.agent_config.transport == AgentTransport.OPENCODE,
            connectivity_state_provider=pipeline_deps.connectivity_state_provider,
            is_waiting_state_provider=pipeline_deps.is_waiting_state_provider,
        ),
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
) -> None:
    on_mcp_restart = (
        ctx.display_subscriber.record_mcp_restart if ctx.display_subscriber is not None else None
    )
    get_heartbeat = pipeline_deps.heartbeat_policy_from_env_fn

    # R1 / R5 (Trustworthy Idle Watchdog spec): build the per-invocation
    # ``SubagentPidRegistry`` once at the orchestrator level so the
    # SAME registry reaches BOTH the strategy layer
    # (``invoke_agent`` -> ``strategy_for_command``) AND the parser
    # layer (``stream_parsed_agent_activity`` -> ``_resolve_parser``
    # -> ``get_parser`` -> ``parser._subagent_pid_registry``). The
    # registry is threaded into ``InvokeOptions`` via the new
    # ``subagent_pid_registry`` / ``subagent_pid_source`` fields so
    # ``invoke_agent`` consumes the SAME registry instead of
    # building a fresh one internally. Without this wiring, the
    # parser's structured-event registration hook fires into one
    # registry but the strategy layer's filtered seam sees a
    # DIFFERENT registry -- the watchdog-visible filtered
    # subagent count is desynchronized from the parser's
    # authoritative registration set, and the R1 filtered count
    # contract is silently violated.
    _raw_transport: object = getattr(ctx.agent_config, "transport", None)
    _agent_config_transport: AgentTransport = (
        _raw_transport if isinstance(_raw_transport, AgentTransport) else AgentTransport.GENERIC
    )
    _agent_registry = AgentRegistry()
    _subagent_pid_registry, _subagent_pid_source = (
        _agent_registry.build_subagent_pid_registry(_agent_config_transport)
    )
    _subagent_source_label = _agent_config_transport.value
    # Thread the shared registry + per-transport source through
    # ``InvokeOptions`` so ``invoke_agent`` consumes the SAME
    # registry instance. ``replace`` produces a fresh InvokeOptions
    # (the dataclass is frozen=True) without mutating the caller's
    # copy.
    options = replace(
        options,
        subagent_pid_registry=_subagent_pid_registry,
        subagent_pid_source=_subagent_pid_source,
    )

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
                agent_config=ctx.agent_config,
                subagent_pid_registry=_subagent_pid_registry,
                subagent_source_label=_subagent_source_label,
            )
            return
        for line in output_lines:
            text_line = str(line)
            raw_output.append(text_line)
            session_id = extract_transport_session_id((text_line,))
            if session_id is not None:
                capture_session_id(session_id)

    try:
        with pipeline_deps.mcp_supervisor_factory(
            bridge_ctx.bridge,
            check_interval=get_heartbeat().interval,
            on_restart=on_mcp_restart,
        ):
            _run_invocation()
    except ctx.deps.agent_invocation_error as exc:
        raise _enrich_invocation_error(exc, raw_output, rendered_output) from exc


def _enrich_invocation_error(
    exc: Exception,
    raw_output: deque[str],
    rendered_output: deque[str],
) -> Exception:
    """Merge captured output into an invocation error so classifiers see full context."""
    if not isinstance(exc, AgentInvocationError):
        return exc
    extra = [
        line for line in (*raw_output, *rendered_output) if line and line not in exc.parsed_output
    ]
    exc.parsed_output = [*extra, *exc.parsed_output]
    return exc


def build_agent_recovery_plan(recovery_input: AgentRecoveryInput) -> AgentRecoveryPlan | None:
    """Determine whether and how to retry a failed agent invocation.

    Computes ``session_id`` and ``recovery_action`` BEFORE the
    prompt-construction call so the prompt constructor can branch on
    ``recovery_action`` (``resume`` / ``new_session_with_id`` take the
    resume-style path; ``fresh`` inlines the original task). The
    single owner of ``recovery_action`` is this function; the only
    consumer is ``_write_agent_retry_prompt`` via
    ``_retry_prompt_file_for_context``.

    Defense-in-depth: the stale-session prompt metadata trio
    (``stale_session_id``, ``transport``, ``model``) is gated on
    ``_is_stale_session_failure(recovery_input.exc)`` -- the canonical
    predicate that scopes to ``SESSION_NOT_FOUND_SUBSTRINGS``. A prior
    session id captured on ``AgentRecoveryInput.stale_session_id``
    must NOT trigger the ``STALE SESSION RECOVERY`` block when the
    failure is non-stale (e.g. ``AgentInactivityTimeoutError``,
    ``OpenCodeResumableExitError``, generic connectivity failures).
    This guards against direct ``AgentRecoveryInput`` construction
    that bypasses ``_build_recovery_input_for_attempt`` (tests, future
    callers). AC-03.
    """
    if recovery_input.attempt_index >= recovery_input.max_recovery_attempts:
        return None
    reason = retryable_agent_failure_reason(
        recovery_input.exc, recovery_input.inactivity_error_type
    )
    if reason is None:
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
        recovery_input.exc,
        phase=str(recovery_input.effect.phase),
        agent=recovery_input.effect.agent_name,
        session_id=session_id,
        inactivity_error_type=recovery_input.inactivity_error_type,
    )
    reset_tool_registry = retry_intent.reset_tool_registry if retry_intent is not None else False
    recovery_action = recovery_action_for_failure_reason(
        type(recovery_input.exc).__name__,
        has_prior_session=bool(session_id),
        reset_tool_registry=reset_tool_registry,
    )
    # Stale-session prompt metadata: only forward when the failure was
    # actually a stale-session failure. See module docstring note on
    # AC-03. ``untruncated`` already encodes the canonical predicate.
    prompt_stale_session_id = (
        recovery_input.stale_session_id if untruncated else None
    )
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


def _same_agent_recovery_attempts(config: UnifiedConfig) -> int:
    raw = cast("object", getattr(config.general, "max_same_agent_retries", 10))
    return raw if isinstance(raw, int) and raw >= 0 else 10


def _failure_requires_fresh_session(exc: Exception, inactivity_error_type: type[Exception]) -> bool:
    if isinstance(exc, inactivity_error_type):
        session_resume_safe = cast("object", getattr(exc, "session_resume_safe", False))
        return session_resume_safe is not True
    return contains_casefolded_marker(_recovery_error_parts(exc), _SESSION_NOT_FOUND_SUBSTRINGS)


def _is_stale_session_failure(exc: Exception) -> bool:
    """Return True when the failure exception surfaces a stale-session marker.

    Single-owner for the untruncated retry-context branch. Scoped to
    SESSION_NOT_FOUND_SUBSTRINGS markers ONLY (stderr / str(exc) /
    parsed_output per the canonical classifier vocabulary); deliberately
    narrower than ``_failure_requires_fresh_session`` which is also True
    for ``AgentInactivityTimeoutError`` with ``session_resume_safe=False``.

    The marker scan stays scoped to the FAILURE EXCEPTION surfaces via
    ``_recovery_error_parts``; ``rendered_output`` and ``raw_output`` are
    NOT inspected, so an incidental ``session not found`` substring inside
    a long rendered output line does not widen the behavior.
    """
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
) -> AgentRecoveryInput:
    """Build an ``AgentRecoveryInput`` from an attempt-failure context.

    Centralizes the construction so the per-attempt failure handler in
    ``_invoke_agent_with_recovery`` stays compact. Also populates the
    stale-session framing metadata (``stale_session_id``, ``transport``,
    ``model``) so the retry prompt can name the rejected session id
    and the runtime that rejected it. ``getattr`` defaults guard against
    ``AttributeError`` if a future ``AgentConfig`` omits the ``model``
    field.

    The stale-session framing trio (``stale_session_id``, ``transport``,
    ``model``) is gated on ``_is_stale_session_failure(exc)`` -- the
    single-source-of-truth predicate that scopes to
    ``SESSION_NOT_FOUND_SUBSTRINGS``. For non-stale failures (e.g.
    ``AgentInactivityTimeoutError`` with ``session_resume_safe=False``,
    ``OpenCodeResumableExitError``, generic connectivity failures) a
    prior session id captured in ``state.last_agent_session_id`` does
    NOT trigger the ``STALE SESSION RECOVERY`` block -- that block
    carries the false claim that the prior session id was rejected,
    which only happens for actual stale-session failures. AC-03.

    ``stale_session_id`` falls back to ``state.resume_session_id`` (and
    then to the ``session_id`` parameter) when ``state.last_agent_session_id``
    is absent. This matters for callers that pass ``session_id``
    explicitly without providing pipeline ``state`` -- e.g.
    ``ralph.pipeline.plumbing.commit_plumbing`` invokes
    ``execute_agent_effect(..., session_id=prior_session_id, ...)``
    without a pipeline-state object. On those paths, the rejected
    session id is the one we just attempted -- not the one captured in
    pipeline state -- so the prompt must still surface it. AC-01, AC-04.
    """
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
        extracted_session_id=(
            extract_transport_session_id(tuple(raw_output)) or session_id
        ),
        inactivity_error_type=AgentInactivityTimeoutError,
        stale_session_id=(
            state.last_agent_session_id
            or state.resume_session_id
            or session_id
            if is_stale_session_failure
            else None
        ),
        transport=_transport_value if is_stale_session_failure else None,
        model=_model_value if is_stale_session_failure else None,
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
    )


def _resume_mode_tail(prompt_path: Path) -> str:
    """Return the resume-mode tail for the retry prompt.

    Single source of truth so the resume tail wording is one
    change in one place (and tests can import the helper as a stable
    token source).
    """
    return (
        "CONTINUE FROM WHERE YOU LEFT OFF: The prior session for this "
        "task is being resumed. Do not restart from the beginning; "
        f"refer to the original prompt at {prompt_path} for context "
        "only and pick up from the most recent state in the resumed session."
    )


def _stale_session_recovery_block(
    *, stale_session_id: str, transport: str | None, model: str | None
) -> str:
    """Return the structured ``STALE SESSION RECOVERY`` block for fresh-mode retry prompts.

    Single source of truth so the stale-session framing wording is one
    change in one place (and tests can import the helper as a stable
    token source). The block is layered ON TOP OF (not replacing)
    ``build_retry_error_block`` and the original task body. Uses
    fresh-session framing only -- NOT resume-style wording -- because
    a stale-session retry is a fresh-session retry with the prior
    session id explicitly rejected.
    """
    transport_label = transport if transport else "unknown"
    model_label = model if model else "unknown"
    return (
        "STALE SESSION RECOVERY\n"
        f"The previous attempt's session id `{stale_session_id}` was rejected by "
        f"`{transport_label}` (model={model_label}).\n"
        "You are starting a FRESH session. Do NOT attempt to re-use that session id.\n"
        "Treat the original task and the prior output summary below as your starting context."
    )


def _write_retry_context_file(
    *, workspace_root: Path, context_lines: list[str], untruncated: bool = False
) -> Path:
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    context_path = prompt_dir / f"agent_retry_context_{uuid.uuid4().hex}.md"
    condensed = _condense_recovery_context_lines(context_lines, untruncated=untruncated)
    summary = "\n".join(condensed) if condensed else "(no output captured)"
    context_path.write_text(summary, encoding="utf-8")
    return context_path


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
) -> str:
    prompt_path = Path(prompt_file)
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = prompt_dir / f"agent_retry_{uuid.uuid4().hex}.md"
    context_path = _write_retry_context_file(
        workspace_root=workspace_root,
        context_lines=context_lines,
        untruncated=untruncated,
    )
    condensed = _condense_recovery_context_lines(context_lines, untruncated=untruncated)
    summary = "\n".join(condensed) if condensed else "(no output captured)"
    error_block = build_retry_error_block(
        failure_summary=f"the previous attempt failed because of {reason}",
        prompt_path=str(prompt_path),
        context_path=str(context_path),
    )
    if recovery_action in {"resume", "new_session_with_id"}:
        # Resume / new_session_with_id: do NOT read the original task body
        # and do NOT include the 'ORIGINAL TASK PROMPT:' section. The
        # resumed session already has the original task in its context;
        # re-inlining it defeats resume (the documented property L wedge).
        # The agent is told to continue from where it left off and to refer
        # to the original prompt by path only if it needs context.
        # The stale-session block does NOT appear here -- resume path owns
        # the framing.
        tail = _resume_mode_tail(prompt_path)
        retry_prompt_path.write_text(
            (f"{error_block}\n\nPREVIOUS OUTPUT SUMMARY EXCERPT:\n{summary}\n\n{tail}\n"),
            encoding="utf-8",
        )
        return str(retry_prompt_path)
    # Fresh (and the defensive ``None`` default): inline the original task
    # body so a brand-new session has the full context. This preserves
    # the prior behavior for fresh-session retries (stale-session,
    # new-chain) and for un-updated callers that have not been threaded
    # the new ``recovery_action`` keyword.
    base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    body_parts: list[str] = [
        error_block,
        "PREVIOUS OUTPUT SUMMARY EXCERPT:",
        summary,
        "",
        "ORIGINAL TASK PROMPT:",
        base_prompt,
    ]
    # Stale-session framing: when this fresh-mode retry was triggered by a
    # stale-session failure (i.e. ``stale_session_id`` is set), append a
    # structured ``STALE SESSION RECOVERY`` block AFTER the original task
    # body. The block names the rejected session id, transport, and model
    # so the retry agent has structured context (instead of restarting
    # from a generic error block). Uses fresh-session framing only --
    # NOT resume-style wording -- because a stale-session retry is a
    # fresh-session retry with the prior session id explicitly rejected.
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
    retry_prompt_path.write_text(
        "\n".join(body_parts) + "\n",
        encoding="utf-8",
    )
    return str(retry_prompt_path)


recovery_error_parts = _recovery_error_parts
resolve_recovery_session_id = _resolve_recovery_session_id
recovery_context_lines = _recovery_context_lines
retry_prompt_file_for_context = _retry_prompt_file_for_context


_retry_intent_local: _threading.local = _threading.local()


def _set_last_captured_retry_intent(intent: AgentRetryIntent) -> None:
    _retry_intent_local.intent = intent


def pop_last_captured_retry_intent() -> AgentRetryIntent:
    """Return and clear the most recent canonical next-attempt retry intent."""

    raw: object = getattr(_retry_intent_local, "intent", cleared_agent_retry_intent())
    intent = raw if isinstance(raw, AgentRetryIntent) else cleared_agent_retry_intent()
    _retry_intent_local.intent = cleared_agent_retry_intent()
    return intent


__all__ = [
    "pop_last_captured_retry_intent",
    "stage_files",
]
