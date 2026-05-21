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

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from loguru import logger

from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._commands import (
    _agent_transport,
    _build_command,
    _build_opencode_command,
    _command_for_log,
    _interactive_stop_hook_settings,
    _interactive_stop_sentinel_path,
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
from ralph.agents.invoke._session import _bounded_output_lines, extract_session_id
from ralph.agents.invoke._types import (
    InvokeOptions,
    ResolvedInvocationRuntime,
    _AgentRunCtx,
    _BuildCommandOptions,
    _ProcessReaderCtx,
    _PtyExtras,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV
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
from ralph.mcp.tools.names import claude_tool_name
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    set_upstream_mcp_config,
)
from ralph.mcp.transport.common import (
    merge_mcp_toml_into_upstreams as _merge_mcp_toml_into_upstreams,
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

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from ralph.agents.timeout_clock import Clock
    from ralph.config.models import AgentConfig


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


def _start_workspace_monitor(workspace_path: Path | None) -> WorkspaceMonitor | None:
    """Start workspace monitoring if path provided."""
    if workspace_path is None:
        return None
    monitor = WorkspaceMonitor(workspace_path)
    monitor.start()
    return monitor


def _stop_workspace_monitor(monitor: WorkspaceMonitor | None) -> None:
    """Stop workspace monitoring."""
    if monitor is not None:
        monitor.stop()


def _prepare_interactive_claude_options(opts: InvokeOptions, config: AgentConfig) -> InvokeOptions:
    if _agent_transport(config) != AgentTransport.CLAUDE_INTERACTIVE:
        return opts
    session_id = opts.session_id or opts.initial_session_id or str(uuid4())
    sentinel_path = opts.stop_sentinel_path or _interactive_stop_sentinel_path(session_id)
    settings_json = opts.settings_json or _interactive_stop_hook_settings(sentinel_path)
    return InvokeOptions(
        model_flag=opts.model_flag,
        session_id=opts.session_id,
        verbose=opts.verbose,
        show_progress=opts.show_progress,
        workspace_path=opts.workspace_path,
        extra_env=opts.extra_env,
        idle_timeout_seconds=opts.idle_timeout_seconds,
        drain_window_seconds=opts.drain_window_seconds,
        max_waiting_on_child_seconds=opts.max_waiting_on_child_seconds,
        idle_poll_interval_seconds=opts.idle_poll_interval_seconds,
        parent_exit_grace_seconds=opts.parent_exit_grace_seconds,
        descendant_wait_timeout_seconds=opts.descendant_wait_timeout_seconds,
        descendant_wait_poll_seconds=opts.descendant_wait_poll_seconds,
        process_exit_wait_seconds=opts.process_exit_wait_seconds,
        max_session_seconds=opts.max_session_seconds,
        waiting_status_interval_seconds=opts.waiting_status_interval_seconds,
        suspect_waiting_on_child_seconds=opts.suspect_waiting_on_child_seconds,
        child_progress_ttl_seconds=opts.child_progress_ttl_seconds,
        child_heartbeat_ttl_seconds=opts.child_heartbeat_ttl_seconds,
        child_stale_label_ttl_seconds=opts.child_stale_label_ttl_seconds,
        child_exit_reconcile_seconds=opts.child_exit_reconcile_seconds,
        max_waiting_on_child_no_progress_seconds=opts.max_waiting_on_child_no_progress_seconds,
        pure=opts.pure,
        system_prompt_file=opts.system_prompt_file,
        waiting_listener=opts.waiting_listener,
        required_artifact=opts.required_artifact,
        explicit_completion_seen=opts.explicit_completion_seen,
        captured_session_id=opts.captured_session_id,
        initial_session_id=session_id,
        settings_json=settings_json,
        stop_sentinel_path=sentinel_path,
        permission_prompt_listener=opts.permission_prompt_listener,
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
    opts = _prepare_interactive_claude_options(options or InvokeOptions(), config)
    runtime = resolve_invocation_runtime(
        config,
        opts.extra_env,
        opts.workspace_path,
        system_prompt_file=opts.system_prompt_file,
    )
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
    execution_strategy = strategy_for_transport(
        _agent_transport(config),
        label_scope=label_scope,
        registry=registry,
    )
    liveness_probe = DefaultLivenessProbe(registry=registry)
    monitor = _start_workspace_monitor(opts.workspace_path)
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
        monitor=monitor,
        required_artifact=opts.required_artifact,
        clock=_clock,
        evaluate_completion_fn=evaluate_completion,
    )
    try:
        transport = _agent_transport(config)
        if transport == AgentTransport.CLAUDE_INTERACTIVE:
            extras = _PtyExtras(
                expected_session_id=opts.session_id or opts.initial_session_id,
                stop_sentinel_path=opts.stop_sentinel_path,
            )
            lines_iter = run_pty_and_read_lines(cmd, ctx, extras)
        else:
            lines_iter = run_subprocess_and_read_lines(cmd, ctx)
        yield from lines_iter

        _log_workspace_completion(monitor)
    finally:
        _stop_workspace_monitor(monitor)


def resolve_invocation_runtime(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    _base_env: Mapping[str, str] | None = None,
    system_prompt_file: str | None = None,
) -> ResolvedInvocationRuntime:
    """Build the runtime configuration needed to launch an agent.

    Resolves transport-specific environment variables, MCP server configuration,
    and endpoint address from ``config`` and ``extra_env``.  Returns a
    ``ResolvedInvocationRuntime`` whose fields are ready to pass to the
    subprocess launcher.
    """
    _env = _base_env if _base_env is not None else cast("Mapping[str, str]", os.environ)
    runtime_env = dict(extra_env or {})
    server_env: dict[str, str] = {}
    endpoint = runtime_env.get(MCP_ENDPOINT_ENV)

    transport = _agent_transport(config)
    if transport == AgentTransport.OPENCODE:
        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)
        provider_config, upstreams = build_opencode_provider_config(
            runtime_env.get("OPENCODE_CONFIG_CONTENT") or _env.get("OPENCODE_CONFIG_CONTENT"),
            endpoint,
        )
        runtime_env["OPENCODE_CONFIG_CONTENT"] = provider_config
        effective_mcp = effective_session_mcp_plan_from_servers(
            mcp_toml_as_upstreams(workspace_path),
            agent_upstream_servers=upstreams,
        )
        set_upstream_mcp_config(runtime_env, effective_mcp.effective_servers)
        set_upstream_mcp_config(server_env, effective_mcp.effective_servers)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )
    if transport == AgentTransport.CODEX:
        if not endpoint and system_prompt_file is None:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)
        codex_home, upstreams = prepare_codex_home_with_upstreams(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
            system_prompt_file=system_prompt_file,
        )
        runtime_env["CODEX_HOME"] = codex_home
        effective_mcp = effective_session_mcp_plan_from_servers(
            mcp_toml_as_upstreams(workspace_path),
            agent_upstream_servers=upstreams,
        )
        set_upstream_mcp_config(runtime_env, effective_mcp.effective_servers)
        set_upstream_mcp_config(server_env, effective_mcp.effective_servers)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        if endpoint:
            effective_mcp = effective_session_mcp_plan_from_servers(
                mcp_toml_as_upstreams(workspace_path),
                agent_upstream_servers=load_existing_claude_upstream_servers(workspace_path),
            )
            set_upstream_mcp_config(runtime_env, effective_mcp.effective_servers)
            set_upstream_mcp_config(server_env, effective_mcp.effective_servers)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )

    if not endpoint:
        return ResolvedInvocationRuntime(agent_env=runtime_env or None)

    raise UnsupportedMcpTransportError(
        f"Agent transport '{transport}' does not declare how to receive Ralph MCP wiring"
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
    try:
        visible_tool_names = discover_http_mcp_tool_names(endpoint)
    except (PreflightError, ValueError) as exc:
        logger.warning("Failed to discover Ralph MCP tools for provider allowlist: {}", exc)
        return ()
    return tuple(claude_tool_name(tool_name) for tool_name in visible_tool_names)


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
build_opencode_command = _build_opencode_command
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
    "bounded_output_lines",
    "build_command",
    "build_invoke_options_from_config",
    "build_opencode_command",
    "check_agent_available",
    "check_process_result",
    "command_for_log",
    "discover_http_mcp_tool_names",
    "extract_choice_menu_state",
    "extract_session_id",
    "get_process_manager",
    "interactive_auto_response_for_prompt",
    "invoke_agent",
    "is_permission_prompt_line",
    "pending_vt_snapshot_line",
    "permission_prompt_action_message",
    "plan_choice_menu_response",
    "policy_from_options",
    "provider_allowed_mcp_tool_names",
    "read_lines_from_process",
    "resolve_invocation_runtime",
    "run_pty_and_read_lines",
    "run_subprocess_and_read_lines",
    "shutil",
    "subprocess",
    "wait_for_descendants_then_recheck",
]
