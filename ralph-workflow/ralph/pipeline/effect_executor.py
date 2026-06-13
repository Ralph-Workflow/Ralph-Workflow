"""Agent and commit effect execution for the pipeline runner."""

from __future__ import annotations

import threading as _threading
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    extract_transport_session_id,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.config.enums import Verbosity
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
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.protocol.startup import heartbeat_policy_from_env
from ralph.mcp.server.lifecycle import (
    McpServerError,
    McpServerExtras,
    RestartAwareMcpBridge,
    check_mcp_bridge_health,
    shutdown_mcp_server,
    start_mcp_server,
)
from ralph.mcp.session_plan import SessionModelOpts, build_session_mcp_plan
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline._agent_bridge_ctx import _AgentBridgeCtx
from ralph.pipeline._agent_invocation_ctx import _AgentInvocationCtx
from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
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
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.pipeline.waiting_dispatch import dispatch_waiting_event
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.recovery.classifier import SESSION_NOT_FOUND_SUBSTRINGS as _SESSION_NOT_FOUND_SUBSTRINGS
from ralph.recovery.failure_classifier import FailureClassifier, should_reset_tool_registry
from ralph.recovery.failure_details import contains_casefolded_marker, failure_detail_parts
from ralph.recovery.retry_prompt import build_retry_error_block
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.agent_execution_deps import (
        AgentExecutionDeps,
        _CheckMcpBridgeHealthFn,
        _McpSupervisorFactory,
        _ShutdownMcpServerFn,
        _StartMcpServerFn,
    )
    from ralph.pipeline.effects import InvokeAgentEffect
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
    # When True, the next attempt should call
    # `RestartAwareMcpBridge.reset_tool_registry()` before dispatching.
    # Set by the failure classifier when a tool-availability failure
    # (e.g. live `No such tool available: mcp__<server>__<tool>`) is
    # detected. The default False preserves the existing behavior on
    # non-tool-availability failures.
    reset_tool_registry: bool = False
    # Normalized signature of a non-terminal (will-retry) failure, used by the
    # recovery loop's zero-progress guard to bound consecutive identical retries.
    # None for terminal results (success/permanent failure), which return early.
    failure_signature: str | None = None


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    **opts: object,
) -> PipelineEvent:
    """Execute an agent-invocation effect end-to-end, including MCP server lifecycle."""
    display = cast("ParallelDisplay | None", opts.get("display"))
    display_context = cast("DisplayContext | None", opts.get("display_context"))
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
    state = cast("PipelineState | None", opts.get("state"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    resolved_display_context = get_display_context(display, display_context)
    registry = deps.agent_registry.from_config(config)
    agent_config = registry.get(effect.agent_name)
    if agent_config is None:
        logger.error("Agent not found: {}", effect.agent_name)
        return PipelineEvent.AGENT_FAILURE
    effective_agents_policy = (
        policy_bundle.agents
        if policy_bundle is not None
        else load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    )
    if state is not None and policy_bundle is not None and deps.show_phase_start_cb is not None:
        deps.show_phase_start_cb(
            effect.phase,
            effect.agent_name,
            resolved_display_context,
            state,
            pipeline_policy=policy_bundle.pipeline,
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
    return _invoke_agent_with_recovery(ctx)


def _invoke_agent_with_recovery(ctx: _AgentInvocationCtx) -> PipelineEvent:
    attempt_prompt_file = ctx.effect.prompt_file
    # Single source of truth for the session-resume DECISION: resolve_resume_session_id
    # maps the stored retry action to the session id to thread into InvokeOptions.
    # The per-transport resume flag SYNTAX is owned separately by config.session_flag
    # in each command builder, so the decision and the flag string cannot drift.
    resume_session_id: str | None = None
    if ctx.state is not None:
        retry_intent = ctx.state.agent_retry_intent
        action = retry_intent.action
        prior_session_id = retry_intent.session_id
        if action is not None and action != "fresh":
            resume_session_id = resolve_resume_session_id(
                has_prior_session=bool(prior_session_id),
                prior_session_id=prior_session_id,
                recovery_action=action,
            )
    bridge = None
    try:
        _materialize = ctx.deps.materialize_system_prompt_fn or materialize_system_prompt
        try:
            system_prompt_file = _materialize(
                workspace_root=ctx.workspace_scope.root,
                name=str(ctx.effect.phase),
                worker_namespace=ctx.worker_namespace,
            )
        except TypeError:
            system_prompt_file = _materialize(
                workspace_root=ctx.workspace_scope.root,
                name=str(ctx.effect.phase),
            )
        session_mcp_plan = build_session_mcp_plan(
            transport=ctx.agent_config.transport,
            drain=ctx.effect.drain or ctx.effect.phase,
            workspace_path=ctx.workspace_scope.root,
            agents_policy=ctx.effective_agents_policy,
            model_opts=SessionModelOpts(model_flag=ctx.agent_config.model_flag),
        )
        session = AgentSession(
            session_id=f"{ctx.effect.phase}-{uuid.uuid4().hex[:8]}",
            run_id=str(uuid.uuid4()),
            drain=ctx.effect.drain or ctx.effect.phase,
            capabilities=set(session_mcp_plan.capabilities),
            parallel_worker=ctx.parallel_worker,
            worker_artifact_dir=ctx.worker_artifact_dir,
            worker_namespace=ctx.worker_namespace,
            allowed_roots=ctx.workspace_scope.allowed_roots,
            model_identity=session_mcp_plan.model_identity,
            stored_capability_profile=session_mcp_plan.capability_profile,
        )
        workspace = FsWorkspace(
            ctx.workspace_scope.root, allowed_roots=ctx.workspace_scope.allowed_roots
        )
        if not ctx.parallel_worker:
            # Shared phase outputs live at the repo root, outside a parallel
            # worker's write scope; clearing them is the parent's job.
            clear_phase_output_artifacts(
                workspace,
                ctx.effect.phase,
                drain=ctx.effect.drain,
                policy_bundle=ctx.policy_bundle,
            )
        _start_mcp: _StartMcpServerFn = cast(
            "_StartMcpServerFn", ctx.deps.start_mcp_server_fn or start_mcp_server
        )
        bridge = _start_mcp(
            session,
            workspace,
            extras=McpServerExtras(phase=ctx.effect.phase, extra_env=session_mcp_plan.server_env),
        )
        bridge_ctx = _AgentBridgeCtx(
            bridge=bridge, session=session, system_prompt_file=system_prompt_file
        )
        progress_guard = RetryProgressGuard()
        for attempt_index in range(ctx.max_recovery_attempts + 1):
            result = _run_attempt(
                ctx, bridge_ctx, attempt_index, attempt_prompt_file, resume_session_id
            )
            if result.event is not None:
                return result.event
            # Zero-progress guard: a retry that reproduces the prior failure's
            # signature makes no forward progress. Bound consecutive identical
            # retries so the loop cannot spin re-running a doomed attempt for the
            # full recovery/session budget (the endless "Retrying ... (N/10)"
            # wedge). A changed signature means progress and resets the streak.
            if result.failure_signature is not None and progress_guard.record(
                result.failure_signature
            ):
                logger.error(
                    "Aborting agent '{}' retries: {} consecutive identical "
                    "zero-progress failures. Refusing to spin; failing fast.",
                    ctx.effect.agent_name,
                    MAX_IDENTICAL_RETRY_ATTEMPTS,
                )
                return PipelineEvent.AGENT_FAILURE
            if (
                result.reset_tool_registry
                and isinstance(bridge_ctx.bridge, RestartAwareMcpBridge)
            ):
                bridge_ctx.bridge.reset_tool_registry()
            attempt_prompt_file = result.next_prompt_file
            resume_session_id = result.next_session_id
    except McpConfigError:
        raise
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return PipelineEvent.AGENT_FAILURE
    finally:
        _shutdown: _ShutdownMcpServerFn = cast(
            "_ShutdownMcpServerFn",
            ctx.deps.shutdown_mcp_server_fn or shutdown_mcp_server,
        )
        if bridge is not None:
            _shutdown(bridge)
    return PipelineEvent.AGENT_FAILURE


def _run_attempt(
    ctx: _AgentInvocationCtx,
    bridge_ctx: _AgentBridgeCtx,
    attempt_index: int,
    attempt_prompt_file: str,
    resume_session_id: str | None,
) -> _AttemptResult:
    raw_output: deque[str] = deque(maxlen=_AGENT_RAW_OUTPUT_TAIL_LINES)
    rendered_output: deque[str] = deque(maxlen=_AGENT_RENDERED_OUTPUT_TAIL_LINES)
    extracted_session_id: str | None = None

    def _capture_session_id(session_id: str) -> None:
        nonlocal extracted_session_id
        extracted_session_id = session_id

    try:
        _check_bridge_health(ctx, bridge_ctx)
        # Per-invocation session-budget reset: every attempt boundary
        # re-arms the inner subprocess's soft wrap-up nag so a retried
        # agent does not inherit the prior attempt's elapsed time. The
        # 60-minute timing budget is a per-invocation soft timeout; a
        # fresh command line (or a retry within the recovery loop) is a
        # fresh attempt. The ``isinstance`` guard lets test stubs that
        # inject a non-RestartAwareMcpBridge skip the reset silently.
        if isinstance(bridge_ctx.bridge, RestartAwareMcpBridge):
            bridge_ctx.bridge.reset_session_budget()
        options = _build_attempt_invoke_options(ctx, bridge_ctx, resume_session_id)
        _consume_attempt_output(
            ctx,
            bridge_ctx,
            attempt_prompt_file,
            options,
            raw_output,
            rendered_output,
            _capture_session_id,
        )
        final_session_id = extracted_session_id or extract_transport_session_id(tuple(raw_output))
        if ctx.deps.set_session_id_cb is not None:
            ctx.deps.set_session_id_cb(final_session_id)
        # Success path: clear any previously recorded failure reason so
        # the next attempt starts fresh. The single-source-of-truth
        # resume helper reads this field to decide between "resume" and
        # "fresh".
        _set_last_captured_retry_intent(cleared_agent_retry_intent())
        return _AttemptResult(PipelineEvent.AGENT_SUCCESS, attempt_prompt_file, resume_session_id)
    except McpServerError as exc:
        logger.error(
            "MCP server failed permanently after {} restart(s): {}", exc.restart_count, exc
        )
        _set_last_captured_retry_intent(
            AgentRetryIntent(action="fresh", failure_reason="McpServerError")
        )
        return _AttemptResult(PipelineEvent.AGENT_FAILURE, attempt_prompt_file, resume_session_id)
    except ctx.deps.agent_invocation_error as exc:
        # Run the failure classifier to detect tool-availability
        # failures (the post-tool-result wedge). The classifier returns
        # a ClassifiedFailure with reset_tool_registry=True when the
        # message matches "no such tool available" or the exception is a
        # runtime ToolDispatchError with "is not registered". The next
        # attempt (in _invoke_agent_with_recovery's for loop) will call
        # bridge.reset_tool_registry() when this flag is set.
        classifier = FailureClassifier()
        classified = classifier.classify(
            exc,
            phase=str(ctx.effect.phase),
            agent=ctx.effect.agent_name,
        )
        recovery_plan = build_agent_recovery_plan(
            AgentRecoveryInput(
                exc=exc,
                attempt_index=attempt_index,
                max_recovery_attempts=ctx.max_recovery_attempts,
                effect=ctx.effect,
                workspace_root=ctx.workspace_scope.root,
                raw_output=list(raw_output),
                rendered_output=list(rendered_output),
                extracted_session_id=(
                    extracted_session_id or extract_transport_session_id(tuple(raw_output))
                ),
                inactivity_error_type=AgentInactivityTimeoutError,
            )
        )
        retry_intent = resolve_retry_intent(
            exc,
            phase=str(ctx.effect.phase),
            agent=ctx.effect.agent_name,
            session_id=recovery_plan.session_id if recovery_plan is not None else None,
            inactivity_error_type=AgentInactivityTimeoutError,
        )
        _set_last_captured_retry_intent(
            retry_intent if retry_intent is not None else cleared_agent_retry_intent()
        )
        if recovery_plan is None:
            logger.error("Agent invocation failed: {}", exc)
            return _AttemptResult(
                PipelineEvent.AGENT_FAILURE,
                attempt_prompt_file,
                resume_session_id,
                reset_tool_registry=classified.reset_tool_registry,
            )
        logger.warning(
            "Retrying agent '{}' after {} ({}/{})",
            ctx.effect.agent_name,
            recovery_plan.reason,
            attempt_index + 1,
            ctx.max_recovery_attempts,
        )
        return _AttemptResult(
            None,
            recovery_plan.prompt_file,
            recovery_plan.session_id,
            reset_tool_registry=classified.reset_tool_registry,
            failure_signature=retry_failure_signature(
                recovery_plan.reason, list(rendered_output)
            ),
        )
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        _set_last_captured_retry_intent(cleared_agent_retry_intent())
        return _AttemptResult(PipelineEvent.AGENT_FAILURE, attempt_prompt_file, resume_session_id)


def _check_bridge_health(ctx: _AgentInvocationCtx, bridge_ctx: _AgentBridgeCtx) -> None:
    _check_health: _CheckMcpBridgeHealthFn = cast(
        "_CheckMcpBridgeHealthFn",
        ctx.deps.check_mcp_bridge_health_fn or check_mcp_bridge_health,
    )
    _check_health(bridge_ctx.bridge)
    if (
        isinstance(bridge_ctx.bridge, RestartAwareMcpBridge)
        and bridge_ctx.bridge.restart_count > 0
        and ctx.display_subscriber is not None
    ):
        ctx.display_subscriber.record_mcp_restart(bridge_ctx.bridge.restart_count)


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
    resume_session_id: str | None,
) -> InvokeOptions:
    required_artifact = (
        resolve_phase_required_artifact(
            ctx.policy_bundle.pipeline,
            ctx.policy_bundle.artifacts,
            phase=ctx.effect.phase,
            drain=ctx.effect.drain or ctx.effect.phase,
        )
        if ctx.policy_bundle is not None
        else None
    )

    def _emit_pre_output_progress() -> None:
        if ctx.display is not None:
            ctx.display.emit(
                ctx.effect.agent_name,
                "Agent process started; waiting for first output",
            )

    return build_invoke_options_from_config(
        ctx.config.general,
        InvokeRuntimeOptions(
            verbose=ctx.config.general.verbosity >= _VERBOSE_LOG_LEVEL,
            show_progress=False,
            workspace_path=ctx.workspace_scope.root,
            extra_env={
                MCP_ENDPOINT_ENV: bridge_ctx.bridge.agent_endpoint_uri(),
                MCP_RUN_ID_ENV: bridge_ctx.session.run_id,
                AGENT_LABEL_SCOPE_ENV: bridge_ctx.session.run_id,
            },
            session_id=resume_session_id,
            system_prompt_file=bridge_ctx.system_prompt_file,
            waiting_listener=ctx.waiting_listener,
            pre_output_listener=_emit_pre_output_progress,
            permission_prompt_listener=_make_permission_prompt_listener(ctx),
            required_artifact=required_artifact,
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
) -> None:
    on_mcp_restart = (
        ctx.display_subscriber.record_mcp_restart if ctx.display_subscriber is not None else None
    )
    supervisor_factory: _McpSupervisorFactory = cast(
        "_McpSupervisorFactory",
        ctx.deps.mcp_supervisor_factory or McpSupervisor,
    )
    get_heartbeat = ctx.deps.heartbeat_policy_from_env_fn or heartbeat_policy_from_env
    with supervisor_factory(
        bridge_ctx.bridge,
        check_interval=get_heartbeat().interval,
        on_restart=on_mcp_restart,
    ):
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
            )
            return
        for line in output_lines:
            text_line = str(line)
            raw_output.append(text_line)
            session_id = extract_transport_session_id((text_line,))
            if session_id is not None:
                capture_session_id(session_id)


def build_agent_recovery_plan(recovery_input: AgentRecoveryInput) -> AgentRecoveryPlan | None:
    """Determine whether and how to retry a failed agent invocation.

    Computes ``session_id`` and ``recovery_action`` BEFORE the
    prompt-construction call so the prompt constructor can branch on
    ``recovery_action`` (``resume`` / ``new_session_with_id`` take the
    resume-style path; ``fresh`` inlines the original task). The
    single owner of ``recovery_action`` is this function; the only
    consumer is ``_write_agent_retry_prompt`` via
    ``_retry_prompt_file_for_context``.
    """
    if recovery_input.attempt_index >= recovery_input.max_recovery_attempts:
        return None
    reason = retryable_agent_failure_reason(
        recovery_input.exc, recovery_input.inactivity_error_type
    )
    if reason is None:
        return None
    context_lines = _recovery_context_lines(
        recovery_input.exc, recovery_input.raw_output, recovery_input.rendered_output
    )
    session_id = _resolve_recovery_session_id(
        recovery_input.exc,
        recovery_input.extracted_session_id,
        recovery_input.inactivity_error_type,
    )
    reset_tool_registry = should_reset_tool_registry(
        recovery_input.exc,
        phase=str(recovery_input.effect.phase),
        agent=recovery_input.effect.agent_name,
    )
    recovery_action = recovery_action_for_failure_reason(
        type(recovery_input.exc).__name__,
        has_prior_session=bool(session_id),
        reset_tool_registry=reset_tool_registry,
    )
    prompt_file = _retry_prompt_file_for_context(
        workspace_root=recovery_input.workspace_root,
        prompt_file=recovery_input.effect.prompt_file,
        reason=reason,
        context_lines=context_lines,
        recovery_action=recovery_action,
    )
    return AgentRecoveryPlan(
        prompt_file=prompt_file,
        session_id=session_id,
        reason=reason,
        recovery_action=recovery_action,
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


def _recovery_error_parts(exc: Exception) -> list[str]:
    return failure_detail_parts(exc)


def _recovery_context_lines(
    exc: Exception,
    raw_output: list[str],
    rendered_output: list[str],
    *,
    _fn: Callable[[Exception], list[str]] | None = None,
) -> list[str]:
    _error_parts_fn = _fn or _recovery_error_parts
    if rendered_output:
        return _tail_recovery_context_lines(rendered_output)
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list) and parsed_output:
        return _tail_recovery_context_lines([str(item) for item in parsed_output])
    stripped_raw = [line.strip() for line in raw_output if line.strip()]
    if stripped_raw:
        return _tail_recovery_context_lines(stripped_raw)
    error_parts = [part.strip() for part in _error_parts_fn(exc) if part.strip()]
    return _tail_recovery_context_lines(error_parts)


def _tail_recovery_context_lines(lines: list[str]) -> list[str]:
    if len(lines) <= _RECOVERY_CONTEXT_LINES:
        return lines
    omitted = len(lines) - _RECOVERY_CONTEXT_LINES
    return [f"<previous log omitted> ({omitted} earlier lines)", *lines[-_RECOVERY_CONTEXT_LINES:]]


def _condense_recovery_context_lines(context_lines: list[str]) -> list[str]:
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
) -> str:
    return _write_agent_retry_prompt(
        workspace_root=workspace_root,
        prompt_file=prompt_file,
        reason=reason,
        context_lines=context_lines,
        recovery_action=recovery_action,
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


def _write_retry_context_file(*, workspace_root: Path, context_lines: list[str]) -> Path:
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    context_path = prompt_dir / f"agent_retry_context_{uuid.uuid4().hex}.md"
    condensed = _condense_recovery_context_lines(context_lines)
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
) -> str:
    prompt_path = Path(prompt_file)
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = prompt_dir / f"agent_retry_{uuid.uuid4().hex}.md"
    context_path = _write_retry_context_file(
        workspace_root=workspace_root,
        context_lines=context_lines,
    )
    condensed = _condense_recovery_context_lines(context_lines)
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
        tail = _resume_mode_tail(prompt_path)
        retry_prompt_path.write_text(
            (
                f"{error_block}\n\n"
                "PREVIOUS OUTPUT SUMMARY EXCERPT:\n"
                f"{summary}\n\n"
                f"{tail}\n"
            ),
            encoding="utf-8",
        )
        return str(retry_prompt_path)
    # Fresh (and the defensive ``None`` default): inline the original task
    # body so a brand-new session has the full context. This preserves
    # the prior behavior for fresh-session retries (stale-session,
    # new-chain) and for un-updated callers that have not been threaded
    # the new ``recovery_action`` keyword.
    base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    retry_prompt_path.write_text(
        (
            f"{error_block}\n\n"
            "PREVIOUS OUTPUT SUMMARY EXCERPT:\n"
            f"{summary}\n\n"
            "ORIGINAL TASK PROMPT:\n"
            f"{base_prompt}\n"
        ),
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
