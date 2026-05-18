"""Agent and commit effect execution for the pipeline runner."""

from __future__ import annotations

import uuid
from collections import deque
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from git import Repo
from loguru import logger

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    InvokeOptions,
    InvokeRuntimeOptions,
    build_invoke_options_from_config,
    extract_session_id,
)
from ralph.config.enums import Verbosity
from ralph.display.artifact_renderer import render_commit_message
from ralph.git.operations import create_commit, stage_all, stage_files
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
    read_commit_message_payload_from_path,
)
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
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    resolve_phase_required_artifact,
)
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
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
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.mcp.protocol.startup import HeartbeatPolicy
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PipelinePolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    class _InvokeAgentFn(Protocol):
        def __call__(
            self,
            config: AgentConfig,
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> Iterable[str]: ...

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> _RegistryLike: ...

    class _ShowPhaseStartFn(Protocol):
        def __call__(
            self,
            phase: str,
            agent_name: str,
            display_context: DisplayContext,
            state: PipelineState,
            *,
            pipeline_policy: PipelinePolicy,
        ) -> None: ...

    class _StartMcpServerFn(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> RestartAwareMcpBridge: ...

    class _CreateCommitFn(Protocol):
        def __call__(self, repo_root: Path | str, message: str, **kwargs: object) -> str: ...

    class _StageAllFn(Protocol):
        def __call__(self, repo_root: Path | str) -> None: ...

    class _HasCommitWorkFn(Protocol):
        def __call__(self, repo_root: Path) -> bool: ...

    class _RenderCommitMessageFn(Protocol):
        def __call__(self, repo_root: Path, display_context: object) -> None: ...

    class _ShutdownMcpServerFn(Protocol):
        def __call__(self, bridge: object) -> None: ...

    class _CheckMcpBridgeHealthFn(Protocol):
        def __call__(self, bridge: object) -> None: ...

    class _MaterializeSystemPromptFn(Protocol):
        def __call__(self, *, workspace_root: Path, name: str) -> str: ...

    class _McpSupervisorFactory(Protocol):
        def __call__(
            self,
            bridge: object,
            *,
            check_interval: object,
            on_restart: object,
        ) -> AbstractContextManager[None]: ...

    class _HeartbeatPolicyFromEnvFn(Protocol):
        def __call__(self) -> HeartbeatPolicy: ...

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

    @dataclass(frozen=True)
    class AgentExecutionDeps:
        """Injectable dependencies for executing an agent-invocation effect."""

        invoke_agent: _InvokeAgentFn
        agent_invocation_error: type[Exception]
        agent_registry: _AgentRegistryFactory
        show_phase_start_cb: _ShowPhaseStartFn | None = None
        set_session_id_cb: Callable[[str | None], None] | None = None
        start_mcp_server_fn: _StartMcpServerFn | None = None
        shutdown_mcp_server_fn: _ShutdownMcpServerFn | None = None
        check_mcp_bridge_health_fn: _CheckMcpBridgeHealthFn | None = None
        materialize_system_prompt_fn: _MaterializeSystemPromptFn | None = None
        mcp_supervisor_factory: _McpSupervisorFactory | None = None
        heartbeat_policy_from_env_fn: _HeartbeatPolicyFromEnvFn | None = None

    @dataclass(frozen=True)
    class AgentRecoveryPlan:
        """Resolved retry plan for a failed agent invocation."""

        prompt_file: str
        session_id: str | None
        reason: str

    @dataclass(frozen=True)
    class AgentRecoveryInput:
        """All inputs required to determine whether and how to retry an agent invocation."""

        exc: Exception
        attempt_index: int
        max_recovery_attempts: int
        effect: InvokeAgentEffect
        workspace_root: Path
        raw_output: list[str]
        rendered_output: list[str]
        extracted_session_id: str | None
        inactivity_error_type: type[Exception]

    @dataclass(frozen=True)
    class _AgentInvocationCtx:
        effect: InvokeAgentEffect
        config: UnifiedConfig
        deps: AgentExecutionDeps
        workspace_scope: WorkspaceScope
        verbosity: Verbosity
        resolved_display_context: DisplayContext | None
        display_subscriber: PipelineSubscriber | None
        max_recovery_attempts: int
        effective_agents_policy: AgentsPolicy
        state: PipelineState | None
        policy_bundle: PolicyBundle | None
        waiting_listener: Callable[[object], None]
        agent_config: AgentConfig
        display: ParallelDisplay | LegacyConsoleDisplay | None

    @dataclass(frozen=True)
    class _AgentBridgeCtx:
        bridge: RestartAwareMcpBridge
        session: AgentSession
        system_prompt_file: str

    event: PipelineEvent | None
    next_prompt_file: str
    next_session_id: str | None


AgentExecutionDeps = _AttemptResult.AgentExecutionDeps
AgentRecoveryPlan = _AttemptResult.AgentRecoveryPlan
AgentRecoveryInput = _AttemptResult.AgentRecoveryInput
_AgentInvocationCtx = _AttemptResult._AgentInvocationCtx
_AgentBridgeCtx = _AttemptResult._AgentBridgeCtx


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
            effect.phase, effect.agent_name, resolved_display_context, state,
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
    raw = cast("object", getattr(config.general, "max_same_agent_retries", 1))
    return raw if isinstance(raw, int) and raw >= 0 else 1


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
    if not context_lines:
        return prompt_file
    return _write_agent_retry_prompt(
        workspace_root=workspace_root,
        prompt_file=prompt_file,
        reason=reason,
        context_lines=context_lines,
    )


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
    summary = "\n".join(context_lines) if context_lines else "(no output captured)"
    retry_prompt_path.write_text(
        (
            f"{base_prompt}\n\n"
            "RETRY CONTEXT:\n"
            f"The previous attempt ended because of {reason}.\n"
            "Treat this as an infrastructure interruption, not a new user request.\n"
            "Resume from the current workspace state instead of starting over.\n"
            "Review the latest files and prior output summary before continuing.\n"
            "Previous output summary:\n"
            f"{summary}\n"
        ),
        encoding="utf-8",
    )
    return str(retry_prompt_path)


def execute_commit_effect(
    effect: CommitEffect,
    repo_root: Path,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    **opts: object,
) -> PipelineEvent:
    """Execute a commit effect, creating or skipping a git commit."""
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
    _raw_create = opts.get("create_commit_fn")
    _create_commit_fn: _CreateCommitFn = cast(
        "_CreateCommitFn", _raw_create if callable(_raw_create) else create_commit
    )
    _raw_stage = opts.get("stage_all_fn")
    _stage_all_fn: _StageAllFn = cast(
        "_StageAllFn", _raw_stage if callable(_raw_stage) else stage_all
    )
    _raw_has_work = opts.get("has_commit_work_fn")
    _has_commit_work_fn: _HasCommitWorkFn = cast(
        "_HasCommitWorkFn", _raw_has_work if callable(_raw_has_work) else _repo_has_commit_work
    )
    try:
        payload = _read_commit_effect_payload(effect)
        message = _read_commit_effect_message(effect)
        if payload is None or not message:
            logger.error("Commit message file is empty: {}", effect.message_file)
            return PipelineEvent.COMMIT_FAILURE
        if payload.get("type") == "skip" or message.strip().lower().startswith("skip:"):
            logger.info("Commit agent requested skip — skipping commit execution")
            cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SKIPPED
        if not _has_commit_work_fn(repo_root):
            logger.info("Skipping commit because the worktree is empty")
            cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SKIPPED
        _stage_commit_scope(repo_root, payload, _stage_all_fn)
        sha = _create_commit_fn(str(repo_root), message)
        logger.info("Created commit: {}", sha[:8])
        _raw_render = opts.get("render_commit_message_fn")
        _render_commit_fn = cast(
            "_RenderCommitMessageFn",
            _raw_render if callable(_raw_render) else render_commit_message,
        )
        with suppress(Exception):
            _render_commit_fn(repo_root, get_display_context(display))
        if verbosity != Verbosity.QUIET and hasattr(display, "record_artifact_outcome"):
            with suppress(Exception):
                cast("ParallelDisplay", display).record_artifact_outcome(f"sha={sha[:8]}")
        cleanup_commit_message_artifacts(repo_root)
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS


def _read_commit_effect_payload(effect: CommitEffect) -> dict[str, object] | None:
    return read_commit_message_payload_from_path(Path(effect.message_file))


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _stage_commit_scope(
    repo_root: Path,
    payload: dict[str, object],
    stage_all_fn: _StageAllFn,
) -> None:
    include_paths = _commit_include_paths(repo_root, payload)
    if include_paths is None:
        stage_all_fn(str(repo_root))
        return
    stage_files(str(repo_root), include_paths)


def _commit_include_paths(repo_root: Path, payload: dict[str, object]) -> list[str] | None:
    raw_files = payload.get("files")
    if isinstance(raw_files, list):
        return [path for path in raw_files if isinstance(path, str)]
    raw_excluded = payload.get("excluded_files")
    if not isinstance(raw_excluded, list):
        return None
    excluded = {
        path.strip()
        for item in raw_excluded
        if isinstance(item, dict)
        and isinstance((path := item.get("path")), str)
        and path.strip()
    }
    changed = _changed_commit_paths(repo_root)
    return [path for path in changed if path not in excluded]


def _changed_commit_paths(repo_root: Path) -> list[str]:
    status_output = cast("str", Repo(repo_root).git.status("--porcelain"))
    status_lines = status_output.splitlines()
    changed: list[str] = []
    for line in status_lines:
        if len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in changed:
            changed.append(path)
    return changed


def _repo_has_commit_work(repo_root: Path) -> bool:
    return Repo(repo_root).is_dirty(untracked_files=True)


def cleanup_commit_message_artifacts(repo_root: Path) -> None:
    """Remove commit message artifacts left by a prior commit phase."""
    delete_commit_message_artifacts(repo_root)


def should_early_skip_commit(workspace_root: Path) -> bool:
    """Return True iff the worktree is clean and the commit phase should be skipped early.

    Fails open (returns False) when git state cannot be inspected so the pipeline
    falls back to the late-skip guard in execute_commit_effect().
    """
    try:
        return not _repo_has_commit_work(workspace_root)
    except Exception:
        return False


def commit_effect(workspace_root: Path) -> CommitEffect:
    """Build a CommitEffect pointing at the standard commit message artifact path."""
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    **opts: object,
) -> None:
    """Remove stale per-phase artifacts before invoking an agent.

    Planning artifacts are an exception: fresh-vs-preserve invalidation is
    owned by prompt materialization, which has the semantic context to
    distinguish fresh planning from loopback, retry, and resume. Clearing plan
    outputs again here reintroduces a second, less-informed authority and can
    delete the live plan handoff on non-fresh planning entries.
    """
    drain = cast("str | None", opts.get("drain"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    effective_drain = drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if policy_bundle is not None
        else None
    )
    if required_artifact is not None and required_artifact.artifact_type == "plan":
        return
    for path in phase_output_artifact_paths(phase, drain=drain, policy_bundle=policy_bundle):
        workspace.remove(path)


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
    """Return paths of all output artifacts produced by a phase."""
    paths: list[str] = []
    effective_drain = drain or phase
    ra = (
        build_required_artifacts(policy_bundle.artifacts).get(effective_drain)
        if policy_bundle is not None
        else None
    )
    if ra is not None:
        paths.append(ra.json_path)
        if ra.markdown_path is not None:
            paths.append(ra.markdown_path)
    if policy_bundle is not None:
        phase_def = policy_bundle.pipeline.phases.get(phase)
        if phase_def is not None:
            if phase_def.parallelization is not None:
                paths.append(".agent/artifacts/parallel_development_summary.json")
            if phase_def.role == "commit" and ra is None:
                paths.append(COMMIT_MESSAGE_ARTIFACT)
    return tuple(paths)


def default_mcp_capabilities_for_phase(
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    """Return the default MCP capability set for a given phase."""
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
            agents_policy=agents_policy,
        ).capabilities
    )


repo_has_commit_work = _repo_has_commit_work
recovery_error_parts = _recovery_error_parts
retryable_agent_failure_reason = _retryable_agent_failure_reason
resolve_recovery_session_id = _resolve_recovery_session_id
recovery_context_lines = _recovery_context_lines
retry_prompt_file_for_context = _retry_prompt_file_for_context
