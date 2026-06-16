"""Subprocess-based agent invocation with streaming NDJSON parsing.

This module handles invoking AI agents as subprocesses, parsing their
streaming NDJSON output, and managing the lifecycle of the process.

Key features:
- Line-by-line streaming from subprocess stdout to parser
- tqdm progress bar (or rich when TTY)
- loguru structured logging for every NDJSON line
- watchdog workspace monitoring for file-change events during execution
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from loguru import logger

from ralph.agents.catalog import default_catalog
from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import strategy_for_command
from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._commands import (
    _agent_transport,
    _build_command,
    _command_for_log,
    _interactive_stop_sentinel_path,
    _merge_interactive_settings_json,
    check_agent_available,
)
from ralph.agents.invoke._completion import (
    _check_process_result,
    _CompletionCheckOptions,
    _wait_for_descendants_then_recheck,
)
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    InteractivePermissionPromptError,
    OpenCodeResumableExitError,
    UnsupportedMcpTransportError,
    _IdleStreamTimeoutError,
)
from ralph.agents.invoke._options import (
    InvokeRuntimeOptions,
    _log_workspace_completion,
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.agents.invoke._process_reader import (
    _read_lines_from_process,
    _run_subprocess_and_read_lines,
)
from ralph.agents.invoke._pty_helpers import (
    _extract_choice_menu_state,
    _interactive_auto_response_for_prompt,
    _is_permission_prompt_line,
    _pending_vt_snapshot_line,
    _permission_prompt_action_message,
    _plan_choice_menu_response,
)
from ralph.agents.invoke._pty_reader import _run_pty_and_read_lines as _run_pty_and_read_lines_impl
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.agents.invoke._session import (
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_from_line,
    extract_visible_tui_transport_session_id,
)
from ralph.agents.invoke._session_resume import (
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import (
    InvokeOptions,
    ResolvedInvocationRuntime,
    _AgentRunCtx,
    _BuildCommandOptions,
    _ProcessReaderCtx,
    _PtyExtras,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    _normalize_workspace_change_weights,
)
from ralph.agents.spec import AgentSpec
from ralph.api.opencode import validate_local_model_support
from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.canonical_submit import _clear_fallback_artifacts
from ralph.mcp.artifacts.completion_receipts import clear_run_receipts
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.startup import (
    PreflightError,
    ensure_no_preflight_error,
    extract_preflight_tool_names,
    initialize_request,
    initialized_notification,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from ralph.mcp.session_plan import effective_session_mcp_plan_from_servers
from ralph.mcp.tool_contract import canonicalize_tool_names
from ralph.mcp.tools.names import (
    claude_tool_name,
)
from ralph.mcp.transport.agy import (
    agy_workspace_mcp_endpoint,
    load_existing_agy_upstream_servers,
)
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    set_upstream_mcp_config,
)
from ralph.mcp.transport.common import (
    merge_mcp_toml_into_upstreams as _merge_mcp_toml_into_upstreams,
)
from ralph.mcp.transport.nanocoder import (
    build_nanocoder_mcp_config,
    load_existing_nanocoder_upstream_servers,
)
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import DefaultLivenessProbe
from ralph.process.manager import get_process_manager
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
)

_MODELED_FLAG_PARTS = 2

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from ralph.agents.timeout_clock import Clock
    from ralph.config.models import AgentConfig
    from ralph.mcp.upstream.config import UpstreamMcpServer


def _make_child_registry(opts: InvokeOptions) -> ChildLivenessRegistry:
    """Create a new per-invoke ChildLivenessRegistry using config-driven TTL values."""
    return ChildLivenessRegistry(
        progress_ttl=opts.child_progress_ttl_seconds
        if opts.child_progress_ttl_seconds is not None
        else CHILD_PROGRESS_TTL_SECONDS,
        heartbeat_ttl=opts.child_heartbeat_ttl_seconds
        if opts.child_heartbeat_ttl_seconds is not None
        else CHILD_HEARTBEAT_TTL_SECONDS,
        stale_label_ttl=opts.child_stale_label_ttl_seconds
        if opts.child_stale_label_ttl_seconds is not None
        else CHILD_STALE_LABEL_TTL_SECONDS,
        exit_reconcile=opts.child_exit_reconcile_seconds
        if opts.child_exit_reconcile_seconds is not None
        else CHILD_EXIT_RECONCILE_SECONDS,
    )


def _start_workspace_monitor(
    workspace_path: Path | None,
    classifier: WorkspaceChangeClassifier | None = None,
) -> WorkspaceMonitor | None:
    """Start workspace monitoring if path provided.

    Args:
        workspace_path: Workspace directory to monitor.
        classifier: Optional ``WorkspaceChangeClassifier`` used to
            classify each file change into a ``WorkspaceChangeKind``
            and a binary weight. When ``None`` (or omitted), the
            monitor uses the legacy behavior: every file change is
            recorded as ``OTHER / 1.0`` activity. When provided,
            events with weight ``0.0`` are dropped before the
            ``on_event`` callback fires; events with weight ``1.0``
            are passed to the callback together with their
            ``(kind, weight)`` tuple.
    """
    if workspace_path is None:
        return None
    monitor = WorkspaceMonitor(workspace_path, classifier=classifier)
    monitor.start()
    return monitor


def _stop_workspace_monitor(monitor: WorkspaceMonitor | None) -> None:
    """Stop workspace monitoring."""
    if monitor is not None:
        monitor.stop()


def _clear_session_completion_sentinel(workspace_path: Path, run_id: str) -> None:
    """Delete this run's completion evidence (sentinel + submission receipts).

    Clearing both together prevents a resumed session that reuses ``run_id`` from
    inheriting stale "completed" / "artifact submitted" signals from a prior
    attempt.

    Also clears fallback artifacts in .agent/tmp/ to prevent a fresh run from
    promoting stale fallback files from previous runs.
    """
    sentinel_path = workspace_path / f".agent/completion_seen_{run_id}.json"
    sentinel_path.unlink(missing_ok=True)
    clear_run_receipts(workspace_path, run_id)
    _clear_fallback_artifacts(workspace_path, run_id)


def _apply_upstream_env(
    upstreams: tuple[UpstreamMcpServer, ...],
    workspace_path: Path | None,
    runtime_env: dict[str, str],
    server_env: dict[str, str],
) -> None:
    effective_mcp = effective_session_mcp_plan_from_servers(
        mcp_toml_as_upstreams(workspace_path),
        agent_upstream_servers=upstreams,
    )
    set_upstream_mcp_config(runtime_env, effective_mcp.effective_servers)
    set_upstream_mcp_config(server_env, effective_mcp.effective_servers)


def _prepare_interactive_claude_options(opts: InvokeOptions, config: AgentConfig) -> InvokeOptions:
    if _agent_transport(config) != AgentTransport.CLAUDE_INTERACTIVE:
        return opts
    session_id = opts.session_id or opts.initial_session_id or str(uuid4())
    sentinel_path = opts.stop_sentinel_path or _interactive_stop_sentinel_path(session_id)
    settings_json = _merge_interactive_settings_json(opts.settings_json, sentinel_path)
    return replace(
        opts,
        initial_session_id=session_id,
        settings_json=settings_json,
        stop_sentinel_path=sentinel_path,
    )


def invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: InvokeOptions | None = None,
    _clock: Clock | None = None,
) -> Iterator[str]:
    """Invoke agent, yield parsed output lines as they arrive.

    Args:
        config: Agent configuration specifying command and flags.
        prompt_file: Path to PROMPT.md file to pass to agent.
        options: Optional invocation options.
        _clock: Injectable Clock for testing; production callers omit this.

    Yields:
        Raw agent output lines (before parsing).

    Raises:
        AgentInvocationError: If agent exits with non-zero code.
    """
    base_opts = options or InvokeOptions()
    _fail_for_unsupported_local_opencode_model(config, base_opts)
    runtime = resolve_invocation_runtime(
        config,
        base_opts.extra_env,
        base_opts.workspace_path,
        system_prompt_file=base_opts.system_prompt_file,
        unsafe_mode=base_opts.unsafe_mode,
    )
    opts = _prepare_interactive_claude_options(base_opts, config)
    runtime_env = runtime.agent_env
    mcp_endpoint = runtime.mcp_endpoint
    allowed_mcp_tool_names = provider_allowed_mcp_tool_names(config, mcp_endpoint)
    cmd = _build_command(
        config,
        prompt_file,
        options=_BuildCommandOptions(
            model_flag=opts.model_flag,
            session_id=opts.session_id,
            verbose=opts.verbose,
            pure=opts.pure,
            mcp_endpoint=mcp_endpoint,
            allowed_mcp_tool_names=allowed_mcp_tool_names,
            unsafe_mode=opts.unsafe_mode,
            system_prompt_file=opts.system_prompt_file,
            workspace_path=opts.workspace_path,
            initial_session_id=opts.initial_session_id,
            settings_json=opts.settings_json,
            stop_sentinel_path=opts.stop_sentinel_path,
        ),
    )
    logger.info("Invoking agent: {}", _command_for_log(config, cmd, prompt_file))

    label_scope = None
    if runtime_env is not None:
        label_scope = runtime_env.get(str(AGENT_LABEL_SCOPE_ENV))
    registry = _make_child_registry(opts)
    execution_strategy = strategy_for_command(
        config.cmd,
        _agent_transport(config),
        label_scope=label_scope,
        registry=registry,
    )
    liveness_probe = DefaultLivenessProbe(registry=registry)
    monitor = _start_workspace_monitor(
        opts.workspace_path,
        classifier=WorkspaceChangeClassifier(
            weights=_normalize_workspace_change_weights(opts.workspace_change_weights)
        )
        if opts.workspace_path is not None
        else None,
    )
    policy = _policy_from_options(opts)

    ctx = _AgentRunCtx(
        config=config,
        show_progress=opts.show_progress,
        extra_env=runtime_env,
        workspace_path=opts.workspace_path,
        policy=policy,
        execution_strategy=execution_strategy,
        liveness_probe=liveness_probe,
        waiting_listener=opts.waiting_listener,
        pre_output_listener=opts.pre_output_listener,
        monitor=monitor,
        required_artifact=opts.required_artifact,
        clock=_clock,
        evaluate_completion_fn=evaluate_completion,
    )
    ctx = replace(ctx, expected_session_id=opts.session_id)
    try:
        transport = _agent_transport(config)
        support = default_catalog().get(config.cmd)
        requires_pty = False
        if support is not None and isinstance(support.spec, AgentSpec):
            requires_pty = support.spec.requires_pty

        if requires_pty:
            if transport == AgentTransport.CLAUDE_INTERACTIVE:
                extras = _PtyExtras(
                    expected_session_id=opts.session_id,
                    stop_sentinel_path=opts.stop_sentinel_path,
                    permission_prompt_listener=opts.permission_prompt_listener,
                )
                yield from run_pty_and_read_lines(cmd, ctx, extras)
            elif transport == AgentTransport.AGY:
                run_id = (opts.extra_env or {}).get(str(MCP_RUN_ID_ENV)) or str(uuid4())
                if opts.workspace_path is not None:
                    _clear_session_completion_sentinel(opts.workspace_path, run_id)
                mcp_ctx = (
                    agy_workspace_mcp_endpoint(
                        opts.workspace_path,
                        runtime.mcp_endpoint,
                        unsafe_mode=base_opts.unsafe_mode,
                    )
                    if runtime.mcp_endpoint and opts.workspace_path
                    else contextlib.nullcontext()
                )
                with mcp_ctx:
                    yield from run_pty_and_read_lines(
                        cmd,
                        ctx,
                        _PtyExtras(expected_session_id=run_id),
                    )
            else:
                yield from run_pty_and_read_lines(cmd, ctx, _PtyExtras())
        else:
            yield from run_subprocess_and_read_lines(cmd, ctx)

        _log_workspace_completion(monitor)
    finally:
        _stop_workspace_monitor(monitor)


def _normalized_opencode_model_id(model_flag: str | None) -> str | None:
    if not model_flag:
        return None
    parts = model_flag.split()
    if len(parts) == _MODELED_FLAG_PARTS and parts[0] in {"-m", "--model"}:
        return parts[1].removeprefix("opencode/")
    if len(parts) == 1:
        return parts[0].removeprefix("opencode/")
    return None


def _fail_for_unsupported_local_opencode_model(
    config: AgentConfig,
    options: InvokeOptions,
) -> None:
    if _agent_transport(config) != AgentTransport.OPENCODE:
        return
    model_id = _normalized_opencode_model_id(options.model_flag or config.model_flag)
    if model_id is None:
        return
    command_name = config.cmd.split()[0]
    message = validate_local_model_support(model_id, command=command_name)
    if message is None:
        return
    raise AgentInvocationError("opencode", 1, message)


def resolve_invocation_runtime(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    _base_env: Mapping[str, str] | None = None,
    system_prompt_file: str | None = None,
    unsafe_mode: bool = False,
) -> ResolvedInvocationRuntime:
    """Build the runtime configuration needed to launch an agent.

    Resolves transport-specific environment variables, MCP server configuration,
    and endpoint address from ``config`` and ``extra_env``.  Returns a
    ``ResolvedInvocationRuntime`` whose fields are ready to pass to the
    subprocess launcher.
    """
    resolver_cls = RUNTIME_RESOLVERS[_agent_transport(config)]
    return resolver_cls().resolve(
        config,
        extra_env,
        workspace_path,
        base_env=_base_env,
        system_prompt_file=system_prompt_file,
        unsafe_mode=unsafe_mode,
    )


def _provider_allowed_mcp_tool_names(
    config: AgentConfig,
    endpoint: str | None,
) -> tuple[str, ...]:
    if endpoint is None or _agent_transport(config) not in (
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
    ):
        return ()
    return tuple(
        claude_tool_name(tool_name) for tool_name in _canonical_http_mcp_tool_names(endpoint)
    )


def _canonical_http_mcp_tool_names(endpoint: str) -> tuple[str, ...]:
    try:
        visible_tool_names = discover_http_mcp_tool_names(endpoint)
    except (PreflightError, ValueError) as exc:
        logger.warning("Failed to discover Ralph MCP tools for provider allowlist: {}", exc)
        return ()
    return canonicalize_tool_names(visible_tool_names)


def _discover_http_mcp_tool_names(endpoint: str) -> list[str]:
    target = parse_http_endpoint(endpoint)
    initialize_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialize_request(),
    )
    ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
    initialized_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialized_notification(),
        session_id=session_id,
    )
    ensure_no_preflight_error(
        "HTTP MCP notifications/initialized", initialized_response.get("error")
    )
    tools_response, _ = post_http_jsonrpc_with_session(
        endpoint,
        target,
        tools_list_request(),
        session_id=session_id,
    )
    ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
    return extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")


# Public aliases — test-accessible names and monkeypatch interception points.
# Internal callers must use the public name so that monkeypatches intercept correctly.
bounded_output_lines = _bounded_output_lines
run_pty_and_read_lines = _run_pty_and_read_lines_impl
run_subprocess_and_read_lines = _run_subprocess_and_read_lines
pending_vt_snapshot_line = _pending_vt_snapshot_line
extract_choice_menu_state = _extract_choice_menu_state
plan_choice_menu_response = _plan_choice_menu_response
permission_prompt_action_message = _permission_prompt_action_message
is_permission_prompt_line = _is_permission_prompt_line
interactive_auto_response_for_prompt = _interactive_auto_response_for_prompt
build_command = _build_command
BuildCommandOptions = _BuildCommandOptions
command_for_log = _command_for_log
provider_allowed_mcp_tool_names = _provider_allowed_mcp_tool_names
discover_http_mcp_tool_names = _discover_http_mcp_tool_names
CompletionCheckOptions = _CompletionCheckOptions
check_process_result = _check_process_result
IdleStreamTimeoutError = _IdleStreamTimeoutError
ProcessReaderCtx = _ProcessReaderCtx
read_lines_from_process = _read_lines_from_process
wait_for_descendants_then_recheck = _wait_for_descendants_then_recheck
policy_from_options = _policy_from_options
merge_mcp_toml_into_upstreams = _merge_mcp_toml_into_upstreams

# Re-export all public types and error classes
__all__ = [
    "AgentInactivityTimeoutError",
    "AgentInvocationError",
    "BuildCommandOptions",
    "CompletionCheckOptions",
    "IdleStreamTimeoutError",
    "InactivityTimeoutOpts",
    "InteractivePermissionPromptError",
    "InvokeOptions",
    "InvokeRuntimeOptions",
    "OpenCodeResumableExitError",
    "ProcessReaderCtx",
    "ResolvedInvocationRuntime",
    "UnsupportedMcpTransportError",
    "WatchdogFireReason",
    "WorkspaceMonitor",
    "agy_workspace_mcp_endpoint",
    "bounded_output_lines",
    "build_command",
    "build_invoke_options_from_config",
    "build_nanocoder_mcp_config",
    "build_opencode_provider_config",
    "check_agent_available",
    "check_process_result",
    "command_for_log",
    "discover_http_mcp_tool_names",
    "extract_choice_menu_state",
    "extract_transport_session_id",
    "extract_transport_session_id_from_line",
    "extract_visible_tui_transport_session_id",
    "fresh_session_options",
    "get_process_manager",
    "interactive_auto_response_for_prompt",
    "invoke_agent",
    "is_permission_prompt_line",
    "load_existing_agy_upstream_servers",
    "load_existing_claude_upstream_servers",
    "load_existing_nanocoder_upstream_servers",
    "pending_vt_snapshot_line",
    "permission_prompt_action_message",
    "plan_choice_menu_response",
    "policy_from_options",
    "prepare_codex_home_with_upstreams",
    "provider_allowed_mcp_tool_names",
    "read_lines_from_process",
    "recovery_action_for_failure_reason",
    "resolve_invocation_runtime",
    "resolve_resume_session_id",
    "run_pty_and_read_lines",
    "run_subprocess_and_read_lines",
    "shutil",
    "subprocess",
    "wait_for_descendants_then_recheck",
]
