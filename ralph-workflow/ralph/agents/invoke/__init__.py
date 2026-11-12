from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from loguru import logger

from ralph.agents._broker_secret import _parent_broker_secret
from ralph.agents.catalog import default_catalog
from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import strategy_for_command
from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._bounded_lines_queue import BoundedLinesQueue
from ralph.agents.invoke._commands import (
    _agent_transport,
    _build_command,
    _command_for_log,
    _interactive_stop_sentinel_path,
    _merge_interactive_settings_json,
    check_agent_available,
)
from ralph.agents.invoke._completion import (
    CompletionCheckOptions,
    _wait_for_descendants_then_recheck,
    check_process_result,
)
from ralph.agents.invoke._errors import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    AgyIncompleteExitError,
    BrokenAgentExitError,
    InactivityTimeoutOpts,
    InteractivePermissionPromptError,
    MissingCredentialsError,
    OpenCodeResumableExitError,
    PiContextExhaustedExitError,
    PiProviderFailureExitError,
    SupervisionInfrastructureError,
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
    ProcessLineReader,
    _raise_on_relay_health_error,
    _read_lines_from_process,
    _run_subprocess_and_read_lines,
    check_broken_agent_timer,
    collect_r7_diagnostic_fields,
)
from ralph.agents.invoke._pty_helpers import (
    _extract_choice_menu_state,
    _interactive_auto_response_for_prompt,
    _is_permission_prompt_line,
    _pending_vt_snapshot_line,
    _permission_prompt_action_message,
    _plan_choice_menu_response,
)
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._pty_reader import _run_pty_and_read_lines as _run_pty_and_read_lines_impl
from ralph.agents.invoke._quota_detection import raise_if_quota_exhausted
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.agents.invoke._session import (
    TURN_BOUNDARY_MARKER,
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_from_line,
    extract_transport_text_session_id,
    extract_visible_tui_transport_session_id,
    is_canonical_session_text_line,
    is_whole_canonical_session_line,
    starts_with_canonical_session_marker,
)
from ralph.agents.invoke._session_resume import (
    fresh_session_options,
    recovery_action_for_failure_reason,
    resolve_resume_session_id,
)
from ralph.agents.invoke._types import (
    AgentRunCtx,
    InvokeOptions,
    ProcessReaderCtx,
    PtyExtras,
    ResolvedInvocationRuntime,
    _BuildCommandOptions,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    _normalize_workspace_change_weights,
)
from ralph.agents.registry import AgentRegistry
from ralph.agents.spec import AgentSpec
from ralph.api.opencode import opencode_model_id_from_flag, validate_local_model_support
from ralph.config._agent_overrides import agent_environment_value, opencode_binary_override
from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.canonical_submit import _clear_fallback_artifacts
from ralph.mcp.artifacts.completion_receipts import clear_run_receipts
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.state_db import RunStateDB
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
from ralph.mcp.transport.agy import load_existing_agy_upstream_servers
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    set_upstream_mcp_config,
)
from ralph.mcp.transport.common import (
    merge_mcp_toml_into_upstreams as _merge_mcp_toml_into_upstreams,
)
from ralph.mcp.transport.cursor import (
    load_existing_cursor_upstream_servers,
)
from ralph.mcp.transport.kimi import load_existing_kimi_upstream_servers
from ralph.mcp.transport.nanocoder import (
    build_nanocoder_mcp_config,
    load_existing_nanocoder_upstream_servers,
)
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.mcp.transport.pi import PI_MCP_EXTENSION_ENV
from ralph.pipeline.events.agent_failure_event import AgentFailureEvent
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import DefaultLivenessProbe
from ralph.process.manager import get_process_manager
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from ralph.agents.timeout_clock import Clock
    from ralph.config.models import AgentConfig
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace


def _make_child_registry(opts: InvokeOptions) -> ChildLivenessRegistry:
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
    *,
    factory: Callable[[Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None] | None = (
        None
    ),
) -> WorkspaceMonitor | None:
    if workspace_path is None:
        return None
    if factory is not None:
        monitor = factory(workspace_path, classifier)
        if monitor is None:
            return None
        monitor.start()
        return monitor
    monitor = WorkspaceMonitor(workspace_path, classifier=classifier)
    monitor.start()
    return monitor


def _stop_workspace_monitor(monitor: WorkspaceMonitor | None) -> None:
    if monitor is not None:
        monitor.stop()


def _shared_interactive_pty_extras(
    transport: AgentTransport,
    opts: InvokeOptions,
    prompt_file: str,
) -> PtyExtras:
    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return PtyExtras(
            expected_session_id=opts.session_id or opts.initial_session_id,
            stop_sentinel_path=opts.stop_sentinel_path,
            permission_prompt_listener=opts.permission_prompt_listener,
            input_prompt=DEFAULT_FILE_BACKEND.read_text(Path(prompt_file), encoding="utf-8"),
        )
    if transport == AgentTransport.NANOCODER:
        return PtyExtras(
            expected_session_id=opts.session_id,
            input_prompt=DEFAULT_FILE_BACKEND.read_text(Path(prompt_file), encoding="utf-8"),
        )
    return PtyExtras(
        expected_session_id=opts.session_id,
        input_prompt=DEFAULT_FILE_BACKEND.read_text(Path(prompt_file), encoding="utf-8"),
    )


def _run_shared_interactive_pty(
    cmd: list[str],
    ctx: AgentRunCtx,
    opts: InvokeOptions,
    transport: AgentTransport,
    prompt_file: str,
) -> Iterator[str]:
    yield from run_pty_and_read_lines(
        cmd,
        ctx,
        _shared_interactive_pty_extras(transport, opts, prompt_file),
    )


def _clear_session_completion_sentinel(workspace_path: Path, run_id: str) -> None:
    try:
        db = RunStateDB(workspace_path)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            try:
                db.delete_completion_sentinel(run_id)
                db.clear_run_receipts(run_id)
            except (OSError, RuntimeError, sqlite3.Error):
                with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                    db.mark_completion_sentinel_cleared(run_id)
                with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                    db.clear_run_receipts(run_id)
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()
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


_CURSOR_AUTH_TOKEN_KEYS = frozenset(
    {"accessToken", "token", "refreshToken", "cursorAuth/accessToken", "cursorAuth/refreshToken"}
)


def _has_cursor_file_credentials(config_home: Path | None, home: Path | None = None) -> bool:
    auth_paths: list[Path] = []
    if config_home is not None:
        auth_paths.append(config_home / "cursor" / "auth.json")
    if home is not None:
        auth_paths.append(home / ".cursor" / "auth.json")
    for auth_path in auth_paths:
        try:
            payload: object = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            credential_payload = cast("dict[str, object]", payload)
            if any(
                isinstance(token := credential_payload.get(key), str) and token.strip()
                for key in _CURSOR_AUTH_TOKEN_KEYS
            ):
                return True
    return False


def _observe_probe_before(
    opts: InvokeOptions,
) -> tuple[WorkspaceChangeProbe | None, Workspace | None, str]:
    probe = opts.workspace_probe
    if probe is None:
        return None, None, ""
    probe_workspace = opts.workspace
    if probe_workspace is None and opts.workspace_path is not None:
        probe_workspace = FsWorkspace(opts.workspace_path)
    probe_phase = opts.phase or ""
    if probe_workspace is not None:
        probe.observe_before(probe_phase, probe_workspace)
    return probe, probe_workspace, probe_phase


def _capture_failure_observation(
    probe: WorkspaceChangeProbe | None,
    probe_workspace: Workspace | None,
    probe_phase: str,
    opts: InvokeOptions,
    exc: BaseException,
) -> AgentFailureEvent:
    workspace_changed: bool | None = None
    if probe is not None and probe_workspace is not None and probe_phase:
        workspace_changed = bool(probe.has_changed(probe_phase, probe_workspace))
    observation = AgentFailureEvent(
        phase=probe_phase,
        agent=opts.agent_name or "",
        artifact_valid=False,
        workspace_changed=workspace_changed,
        reason=type(exc).__name__,
    )
    sink = opts.failure_observation_sink
    if sink is not None:
        sink(observation)
    return observation


def _run_pty_execution(
    cmd: list[str],
    ctx: AgentRunCtx,
    opts: InvokeOptions,
    transport: AgentTransport,
    prompt_file: str,
) -> Iterator[str]:
    if transport in {
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.NANOCODER,
    }:
        yield from _run_shared_interactive_pty(cmd, ctx, opts, transport, prompt_file)
    elif transport == AgentTransport.AGY:
        run_id = (opts.extra_env or {}).get(str(MCP_RUN_ID_ENV)) or str(uuid4())
        if opts.workspace_path is not None:
            _clear_session_completion_sentinel(opts.workspace_path, run_id)
        yield from run_pty_and_read_lines(
            cmd,
            ctx,
            PtyExtras(expected_session_id=run_id),
        )
    else:
        yield from run_pty_and_read_lines(cmd, ctx, PtyExtras())


def invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: InvokeOptions | None = None,
    _clock: Clock | None = None,
) -> Iterator[str]:
    base_opts = options or InvokeOptions()
    _fail_for_missing_credentials(config, base_opts, env_getter=agent_environment_value)
    _fail_for_unsupported_local_opencode_model(config, base_opts)
    runtime = resolve_invocation_runtime(
        config,
        base_opts.extra_env,
        base_opts.workspace_path,
        master_prompt_file=base_opts.master_prompt_file,
        unsafe_mode=base_opts.unsafe_mode,
    )
    monitor: WorkspaceMonitor | None = None
    try:
        runtime_env = runtime.agent_env
        if _agent_transport(config) == AgentTransport.CURSOR:
            cursor_config_home = Path(runtime_env["XDG_CONFIG_HOME"]) if runtime_env else None
            cursor_home = Path(runtime_env["HOME"]) if runtime_env else None
            has_cursor_auth = _has_cursor_file_credentials(cursor_config_home, cursor_home)
            if not (runtime_env and runtime_env.get("CURSOR_API_KEY")) and not has_cursor_auth:
                raise MissingCredentialsError(
                    config.cmd.split()[0],
                    "CURSOR_API_KEY not set and no file-backed Cursor login projected: "
                    "set CURSOR_API_KEY or run 'AGENT_CLI_CREDENTIAL_STORE=file agent login' "
                    "once (keychain logins are never used by unattended runs)",
                )
        opts = _prepare_interactive_claude_options(base_opts, config)
        mcp_endpoint = runtime.mcp_endpoint
        allowed_mcp_tool_names = provider_allowed_mcp_tool_names(config, mcp_endpoint)
        cmd = _build_command(
            config,
            prompt_file,
            options=_BuildCommandOptions(
                model_flag=opts.model_flag,
                session_id=opts.session_id,
                verbose=opts.verbose,
                mcp_endpoint=mcp_endpoint,
                allowed_mcp_tool_names=allowed_mcp_tool_names,
                unsafe_mode=opts.unsafe_mode,
                master_prompt_file=opts.master_prompt_file,
                workspace_path=opts.workspace_path,
                initial_session_id=opts.initial_session_id,
                settings_json=opts.settings_json,
                stop_sentinel_path=opts.stop_sentinel_path,
                pi_mcp_extension_path=runtime_env.get(PI_MCP_EXTENSION_ENV)
                if runtime_env is not None
                else None,
            ),
        )
        logger.info("Invoking agent: {}", _command_for_log(config, cmd, prompt_file))

        label_scope = None
        if runtime_env is not None:
            label_scope = runtime_env.get(str(AGENT_LABEL_SCOPE_ENV))
        registry = _make_child_registry(opts)
        transport_for_registry = _agent_transport(config)
        if opts.subagent_pid_registry is not None and opts.subagent_pid_source is not None:
            subagent_pid_source = opts.subagent_pid_source
            _subagent_pid_registry = opts.subagent_pid_registry
        else:
            (
                _subagent_pid_registry,
                subagent_pid_source,
            ) = AgentRegistry().build_subagent_pid_registry(transport_for_registry)
        execution_strategy = strategy_for_command(
            config.cmd,
            transport_for_registry,
            label_scope=label_scope,
            registry=registry,
            subagent_pid_source=subagent_pid_source,
        )
        liveness_probe = DefaultLivenessProbe(registry=registry)
        monitor = _start_workspace_monitor(
            opts.workspace_path,
            classifier=WorkspaceChangeClassifier(
                weights=_normalize_workspace_change_weights(opts.workspace_change_weights)
            )
            if opts.workspace_path is not None
            else None,
            factory=opts.workspace_monitor_factory,
        )
        policy = _policy_from_options(opts)

        ctx = AgentRunCtx(
            config=config,
            show_progress=opts.show_progress,
            extra_env=runtime_env,
            workspace_path=opts.workspace_path,
            policy=policy,
            execution_strategy=execution_strategy,
            liveness_probe=liveness_probe,
            process_teardown=opts.process_teardown,
            waiting_listener=opts.waiting_listener,
            pre_output_listener=opts.pre_output_listener,
            monitor=monitor,
            required_artifact=opts.required_artifact,
            requires_completion_evidence=opts.requires_completion_evidence,
            clock=_clock,
            evaluate_completion_fn=evaluate_completion,
            connectivity_state_provider=opts.connectivity_state_provider,
            is_waiting_state_provider=opts.is_waiting_state_provider,
            input_prompt=DEFAULT_FILE_BACKEND.read_text(Path(prompt_file), encoding="utf-8"),
            relay_activity_sink_register=opts.relay_activity_sink_register,
            relay_health_error=opts.relay_health_error,
            workspace_probe=opts.workspace_probe,
            workspace=opts.workspace,
            phase=opts.phase,
        )
        ctx = replace(ctx, expected_session_id=opts.session_id)

        transport = _agent_transport(config)
        support = default_catalog().get(config.cmd)
        requires_pty = transport == AgentTransport.AGY
        if support is not None and isinstance(support.spec, AgentSpec):
            requires_pty = requires_pty or support.spec.requires_pty

        probe, probe_workspace, probe_phase = _observe_probe_before(opts)
        try:
            if requires_pty:
                yield from _run_pty_execution(cmd, ctx, opts, transport, prompt_file)
            else:
                yield from run_subprocess_and_read_lines(cmd, ctx)
        except BaseException as exc:
            _capture_failure_observation(probe, probe_workspace, probe_phase, opts, exc)
            raise

        _log_workspace_completion(monitor)
    finally:
        if runtime.cleanup is not None:
            runtime.cleanup()
        _stop_workspace_monitor(monitor)


def _fail_for_missing_credentials(
    config: AgentConfig,
    options: InvokeOptions,
    env_getter: Callable[[str], str | None] | None = None,
) -> None:
    transport = _agent_transport(config)
    required_env_var: str | None = None
    if transport in {AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE}:
        if config.cmd.split()[:1] in (["ccs"], ["claude"]):
            return
        required_env_var = "ANTHROPIC_API_KEY"
    elif transport == AgentTransport.OPENCODE:
        model_id = opencode_model_id_from_flag(options.model_flag or config.model_flag)
        provider = model_id.split("/", maxsplit=1)[0] if model_id is not None else ""
        required_env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(provider)
    elif transport == AgentTransport.CURSOR:
        return
    if required_env_var is None:
        return
    extra_env = options.extra_env or {}
    if extra_env.get(required_env_var):
        return
    getter = env_getter if env_getter is not None else os.environ.get
    if getter(required_env_var):
        return
    raise MissingCredentialsError(config.cmd.split()[0], f"{required_env_var} not set")


def _fail_for_unsupported_local_opencode_model(
    config: AgentConfig,
    options: InvokeOptions,
    env_getter: Callable[[str], str | None] | None = None,
) -> None:
    if _agent_transport(config) != AgentTransport.OPENCODE:
        return
    model_id = opencode_model_id_from_flag(options.model_flag or config.model_flag)
    if model_id is None:
        return

    if opencode_binary_override(env_getter):
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
    master_prompt_file: str | None = None,
    unsafe_mode: bool = False,
) -> ResolvedInvocationRuntime:
    resolver_cls = RUNTIME_RESOLVERS[_agent_transport(config)]
    return resolver_cls().resolve(
        config,
        extra_env,
        workspace_path,
        base_env=_base_env,
        master_prompt_file=master_prompt_file,
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
    command_parts = config.cmd.split()
    agent_name = command_parts[0] if command_parts else "claude"
    try:
        canonical_names = _canonical_http_mcp_tool_names(endpoint)
    except (PreflightError, ValueError) as exc:
        message = (
            "Refusing to launch: Ralph could not discover its MCP tools at "
            f"{endpoint}, so Claude's native-tool restriction cannot be built "
            f"({exc}). Launching anyway would strip the operator's own MCP "
            "servers (--strict-mcp-config) while leaving every native Claude "
            "tool enabled, which is the opposite of the intended posture and "
            "contradicts the prompt Ralph sends. Retry once the Ralph MCP "
            "server is reachable."
        )
        logger.error(message)
        raise AgentInvocationError(agent_name, 1, message) from exc
    return tuple(claude_tool_name(tool_name) for tool_name in canonical_names)


def _canonical_http_mcp_tool_names(endpoint: str) -> tuple[str, ...]:
    return canonicalize_tool_names(discover_http_mcp_tool_names(endpoint))


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


discover_http_mcp_tool_names = _discover_http_mcp_tool_names


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
IdleStreamTimeoutError = _IdleStreamTimeoutError
read_lines_from_process = _read_lines_from_process
raise_on_relay_health_error = _raise_on_relay_health_error
wait_for_descendants_then_recheck = _wait_for_descendants_then_recheck
policy_from_options = _policy_from_options
merge_mcp_toml_into_upstreams = _merge_mcp_toml_into_upstreams

__all__ = [
    "TURN_BOUNDARY_MARKER",
    "AgentInactivityTimeoutError",
    "AgentInvocationError",
    "AgentRunCtx",
    "AgyIncompleteExitError",
    "BoundedLinesQueue",
    "BrokenAgentExitError",
    "BuildCommandOptions",
    "CompletionCheckOptions",
    "IdleStreamTimeoutError",
    "InactivityTimeoutOpts",
    "InteractivePermissionPromptError",
    "InvokeOptions",
    "InvokeRuntimeOptions",
    "MissingCredentialsError",
    "OpenCodeResumableExitError",
    "PiContextExhaustedExitError",
    "PiProviderFailureExitError",
    "ProcessLineReader",
    "ProcessReaderCtx",
    "PtyExtras",
    "PtyLineReader",
    "ResolvedInvocationRuntime",
    "SupervisionInfrastructureError",
    "UnsupportedMcpTransportError",
    "WatchdogFireReason",
    "WorkspaceMonitor",
    "_parent_broker_secret",
    "bounded_output_lines",
    "build_command",
    "build_invoke_options_from_config",
    "build_nanocoder_mcp_config",
    "build_opencode_provider_config",
    "check_agent_available",
    "check_broken_agent_timer",
    "check_process_result",
    "collect_r7_diagnostic_fields",
    "command_for_log",
    "discover_http_mcp_tool_names",
    "extract_choice_menu_state",
    "extract_transport_session_id",
    "extract_transport_session_id_from_line",
    "extract_transport_text_session_id",
    "extract_visible_tui_transport_session_id",
    "fresh_session_options",
    "get_process_manager",
    "interactive_auto_response_for_prompt",
    "invoke_agent",
    "is_canonical_session_text_line",
    "is_permission_prompt_line",
    "is_whole_canonical_session_line",
    "load_existing_agy_upstream_servers",
    "load_existing_claude_upstream_servers",
    "load_existing_cursor_upstream_servers",
    "load_existing_kimi_upstream_servers",
    "load_existing_nanocoder_upstream_servers",
    "pending_vt_snapshot_line",
    "permission_prompt_action_message",
    "plan_choice_menu_response",
    "policy_from_options",
    "prepare_codex_home_with_upstreams",
    "provider_allowed_mcp_tool_names",
    "raise_if_quota_exhausted",
    "raise_on_relay_health_error",
    "read_lines_from_process",
    "recovery_action_for_failure_reason",
    "resolve_invocation_runtime",
    "resolve_resume_session_id",
    "run_pty_and_read_lines",
    "run_subprocess_and_read_lines",
    "shutil",
    "starts_with_canonical_session_marker",
    "subprocess",
    "wait_for_descendants_then_recheck",
]
