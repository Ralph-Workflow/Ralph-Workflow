"""Agent and commit effect execution for the pipeline runner."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    extract_session_id,
)
from ralph.config.enums import Verbosity
from ralph.config.mcp_loader import McpConfigError
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
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.agent_recovery_input import AgentRecoveryInput
from ralph.pipeline.agent_recovery_plan import AgentRecoveryPlan
from ralph.pipeline.commit_executor import clear_phase_output_artifacts
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.legacy_console_display import (
    LegacyConsoleDisplay,
    emit_display_line,
    get_display_context,
    status_text,
    subscriber_for_display,
)
from ralph.pipeline.phase_rendering import VERBOSITY_RANK, verbosity_rank
from ralph.pipeline.waiting_dispatch import dispatch_waiting_event
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.prompts.system_prompt import materialize_system_prompt
from ralph.recovery.classifier import SESSION_NOT_FOUND_SUBSTRINGS as _SESSION_NOT_FOUND_SUBSTRINGS
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
_PORCELAIN_STATUS_PREFIX_LEN = 3
_TRANSIENT_CONNECTIVITY_MARKERS = (
    "connection refused",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "timed out",
    "timeout",
    "offline",
    "econnreset",
    "enotfound",
    "socket hang up",
)


@dataclass(frozen=True)
class _AttemptResult:
    event: PipelineEvent | None
    next_prompt_file: str
    next_session_id: str | None


def execute_agent_effect(
    effect: InvokeAgentEffect,
    config: UnifiedConfig,
    deps: AgentExecutionDeps,
    workspace_scope: WorkspaceScope,
    **opts: object,
) -> PipelineEvent:
    """Execute an agent-invocation effect end-to-end, including MCP server lifecycle."""
    display = cast("ParallelDisplay | LegacyConsoleDisplay | None", opts.get("display"))
    display_context = cast("DisplayContext | None", opts.get("display_context"))
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
    state = cast("PipelineState | None", opts.get("state"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    resolved_display_context = get_display_context(display, display_context)
    emit_display_line(
        display,
        None,
        status_text("Invoking agent", effect.agent_name, "cyan"),
        resolved_display_context,
    )
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
    )
    return _invoke_agent_with_recovery(ctx)


def _invoke_agent_with_recovery(ctx: _AgentInvocationCtx) -> PipelineEvent:
    attempt_prompt_file = ctx.effect.prompt_file
    resume_session_id: str | None = (
        ctx.state.last_agent_session_id
        if (
            ctx.state is not None
            and ctx.state.session_preserve_retry_pending
            and ctx.state.last_agent_session_id
        )
        else None
    )
    bridge = None
    try:
        _materialize = ctx.deps.materialize_system_prompt_fn or materialize_system_prompt
        system_prompt_file = _materialize(
            workspace_root=ctx.workspace_scope.root, name=str(ctx.effect.phase)
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
            model_identity=session_mcp_plan.model_identity,
            stored_capability_profile=session_mcp_plan.capability_profile,
        )
        workspace = FsWorkspace(
            ctx.workspace_scope.root, allowed_roots=ctx.workspace_scope.allowed_roots
        )
        clear_phase_output_artifacts(
            workspace, ctx.effect.phase, drain=ctx.effect.drain, policy_bundle=ctx.policy_bundle
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
        for attempt_index in range(ctx.max_recovery_attempts + 1):
            result = _run_attempt(
                ctx, bridge_ctx, attempt_index, attempt_prompt_file, resume_session_id
            )
            if result.event is not None:
                return result.event
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

        options = build_invoke_options_from_config(
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
                permission_prompt_listener=_permission_prompt_listener,
                required_artifact=(
                    resolve_phase_required_artifact(
                        ctx.policy_bundle.pipeline,
                        ctx.policy_bundle.artifacts,
                        phase=ctx.effect.phase,
                        drain=ctx.effect.drain or ctx.effect.phase,
                    )
                    if ctx.policy_bundle is not None
                    else None
                ),
            ),
        )
        _on_mcp_restart = (
            ctx.display_subscriber.record_mcp_restart
            if ctx.display_subscriber is not None
            else None
        )
        _supervisor: _McpSupervisorFactory = cast(
            "_McpSupervisorFactory",
            ctx.deps.mcp_supervisor_factory or McpSupervisor,
        )
        _get_heartbeat = ctx.deps.heartbeat_policy_from_env_fn or heartbeat_policy_from_env
        with _supervisor(
            bridge_ctx.bridge,
            check_interval=_get_heartbeat().interval,
            on_restart=_on_mcp_restart,
        ):
            output_lines = ctx.deps.invoke_agent(
                ctx.agent_config, attempt_prompt_file, options=options
            )
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
                    session_id_sink=_capture_session_id,
                )
            else:
                for line in output_lines:
                    text_line = str(line)
                    raw_output.append(text_line)
                    session_id = extract_session_id((text_line,))
                    if session_id is not None:
                        extracted_session_id = session_id
        final_session_id = extracted_session_id or extract_session_id(tuple(raw_output))
        if ctx.deps.set_session_id_cb is not None:
            ctx.deps.set_session_id_cb(final_session_id)
        return _AttemptResult(PipelineEvent.AGENT_SUCCESS, attempt_prompt_file, resume_session_id)
    except McpServerError as exc:
        logger.error(
            "MCP server failed permanently after {} restart(s): {}", exc.restart_count, exc
        )
        return _AttemptResult(PipelineEvent.AGENT_FAILURE, attempt_prompt_file, resume_session_id)
    except ctx.deps.agent_invocation_error as exc:
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
                    extracted_session_id or extract_session_id(tuple(raw_output))
                ),
                inactivity_error_type=AgentInactivityTimeoutError,
            )
        )
        if recovery_plan is None:
            logger.error("Agent invocation failed: {}", exc)
            return _AttemptResult(
                PipelineEvent.AGENT_FAILURE, attempt_prompt_file, resume_session_id
            )
        logger.warning(
            "Retrying agent '{}' after {} ({}/{})",
            ctx.effect.agent_name,
            recovery_plan.reason,
            attempt_index + 1,
            ctx.max_recovery_attempts,
        )
        return _AttemptResult(None, recovery_plan.prompt_file, recovery_plan.session_id)
    except Exception:
        logger.exception("Unexpected error during agent invocation: {}")
        return _AttemptResult(PipelineEvent.AGENT_FAILURE, attempt_prompt_file, resume_session_id)


def build_agent_recovery_plan(recovery_input: AgentRecoveryInput) -> AgentRecoveryPlan | None:
    """Determine whether and how to retry a failed agent invocation."""
    if recovery_input.attempt_index >= recovery_input.max_recovery_attempts:
        return None
    reason = _retryable_agent_failure_reason(
        recovery_input.exc, recovery_input.inactivity_error_type
    )
    if reason is None:
        return None
    context_lines = _recovery_context_lines(
        recovery_input.exc, recovery_input.raw_output, recovery_input.rendered_output
    )
    prompt_file = _retry_prompt_file_for_context(
        workspace_root=recovery_input.workspace_root,
        prompt_file=recovery_input.effect.prompt_file,
        reason=reason,
        context_lines=context_lines,
    )
    session_id = _resolve_recovery_session_id(
        recovery_input.exc,
        recovery_input.extracted_session_id,
        recovery_input.inactivity_error_type,
    )
    return AgentRecoveryPlan(prompt_file=prompt_file, session_id=session_id, reason=reason)


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
    return extracted_session_id or None


def _same_agent_recovery_attempts(config: UnifiedConfig) -> int:
    raw = cast("object", getattr(config.general, "max_same_agent_retries", 10))
    return raw if isinstance(raw, int) and raw >= 0 else 10


def _failure_requires_fresh_session(exc: Exception, inactivity_error_type: type[Exception]) -> bool:
    if isinstance(exc, inactivity_error_type):
        session_resume_safe = cast("object", getattr(exc, "session_resume_safe", False))
        return session_resume_safe is not True
    raw_details = "\n".join(_recovery_error_parts(exc))
    return any(s in raw_details for s in _SESSION_NOT_FOUND_SUBSTRINGS)


def _retryable_agent_failure_reason(
    exc: Exception, inactivity_error_type: type[Exception]
) -> str | None:
    if isinstance(exc, inactivity_error_type):
        return "an inactivity timeout"
    if type(exc).__name__ == "OpenCodeResumableExitError":
        return "agent session exited without required completion evidence"
    raw_details = "\n".join(_recovery_error_parts(exc))
    if any(s in raw_details for s in _SESSION_NOT_FOUND_SUBSTRINGS):
        return "a stale session ID (fresh session required)"
    details = raw_details.lower()
    for marker in _TRANSIENT_CONNECTIVITY_MARKERS:
        if marker in details:
            return "a transient connectivity failure"
    return None


def _recovery_error_parts(exc: Exception) -> list[str]:
    parts: list[str] = [str(exc)]
    stderr = cast("object", getattr(exc, "stderr", None))
    if isinstance(stderr, str) and stderr.strip():
        parts.append(stderr.strip())
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list):
        parts.extend(str(item).strip() for item in parsed_output if str(item).strip())
    return parts


def _recovery_context_lines(
    exc: Exception,
    raw_output: list[str],
    rendered_output: list[str],
    *,
    _fn: Callable[[Exception], list[str]] | None = None,
) -> list[str]:
    _error_parts_fn = _fn or _recovery_error_parts
    if rendered_output:
        return rendered_output[-_RECOVERY_CONTEXT_LINES:]
    parsed_output = cast("object", getattr(exc, "parsed_output", None))
    if isinstance(parsed_output, list) and parsed_output:
        return [str(item) for item in parsed_output[-_RECOVERY_CONTEXT_LINES:]]
    stripped_raw = [line.strip() for line in raw_output if line.strip()]
    if stripped_raw:
        return stripped_raw[-_RECOVERY_CONTEXT_LINES:]
    error_parts = [part.strip() for part in _error_parts_fn(exc) if part.strip()]
    return error_parts[-_RECOVERY_CONTEXT_LINES:]


def _retry_prompt_file_for_context(
    *,
    workspace_root: Path,
    prompt_file: str,
    reason: str,
    context_lines: list[str],
) -> str:
    return _write_agent_retry_prompt(
        workspace_root=workspace_root,
        prompt_file=prompt_file,
        reason=reason,
        context_lines=context_lines,
    )


def _write_retry_context_file(*, workspace_root: Path, context_lines: list[str]) -> Path:
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    context_path = prompt_dir / f"agent_retry_context_{uuid.uuid4().hex}.md"
    summary = "\n".join(context_lines) if context_lines else "(no output captured)"
    context_path.write_text(summary, encoding="utf-8")
    return context_path


def _write_agent_retry_prompt(
    *,
    workspace_root: Path,
    prompt_file: str,
    reason: str,
    context_lines: list[str],
) -> str:
    prompt_path = Path(prompt_file)
    base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    prompt_dir = workspace_root / ".agent" / "tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    retry_prompt_path = prompt_dir / f"agent_retry_{uuid.uuid4().hex}.md"
    context_path = _write_retry_context_file(
        workspace_root=workspace_root,
        context_lines=context_lines,
    )
    summary = "\n".join(context_lines) if context_lines else "(no output captured)"
    error_block = build_retry_error_block(
        failure_summary=f"the previous attempt failed because of {reason}",
        prompt_path=str(prompt_path),
        context_path=str(context_path),
    )
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
retryable_agent_failure_reason = _retryable_agent_failure_reason
resolve_recovery_session_id = _resolve_recovery_session_id
recovery_context_lines = _recovery_context_lines
retry_prompt_file_for_context = _retry_prompt_file_for_context

__all__ = ["stage_files"]
