"""Manual smoke tests for expensive agent-runtime checks.

These smoke tests are intentionally excluded from the verify pipeline because they
consume live agent tokens. They exist to help operators validate real-world agent
behavior, especially interactive-Claude parity, when changing the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from rich.panel import Panel
from rich.table import Table

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    OpenCodeResumableExitError,
    build_invoke_options_from_config,
    extract_session_id,
    invoke_agent,
)
from ralph.agents.parsers import get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.mcp.protocol.session import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV, AgentSession
from ralph.mcp.server.lifecycle import SessionBridgeLike, start_mcp_server
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name
from ralph.pipeline import runner as runner_module
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_PROMPT_FILE = _SMOKE_RELATIVE_DIR / "PROMPT.md"
_INTERACTIVE_AGENT = "claude/haiku"
_SMOKE_RUN_ID = "interactive-claude-smoke"
_SMOKE_IDLE_TIMEOUT_SECONDS = 30.0
_SMOKE_MAX_SESSION_SECONDS = 120.0
_SMOKE_MAX_TURNS = 5
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
_MAX_VISIBLE_OUTPUT_LINES = 80
_HEADLESS_SEMANTIC_GUIDE = (
    "session capture, tool activity, completion signal, parser events, and tmp/ artifact creation"
)
_PERMISSION_PROMPT_MARKERS = (
    "allow?",
    "approve",
    "permission",
    "y/n",
)
_CRASH_MARKERS = (
    "traceback",
    "fatal",
    "segmentation fault",
    "panic",
    "crash",
)


@dataclass(frozen=True)
class SmokeRunResult:
    """Observed results from the interactive Claude smoke run."""

    agent_name: str
    transport: str
    output_file: Path
    file_created: bool
    session_id: str | None
    explicit_completion_seen: bool
    raw_line_count: int
    parsed_event_count: int
    tool_activity_seen: bool
    artifact_submitted: bool
    meaningful_output_lines: list[str]
    errors: list[str]


def _submit_artifact_tool_name_for_transport(transport: AgentTransport | None) -> str:
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        return claude_tool_name(SUBMIT_ARTIFACT_TOOL)
    return SUBMIT_ARTIFACT_TOOL


def _build_smoke_prompt(output_relpath: str, *, submit_artifact_tool_name: str) -> str:
    """Return the prompt used for the parity smoke test."""

    return (
        "Create a small JavaScript todo list implementation at "
        f"`{output_relpath}`.\n\n"
        "Requirements:\n"
        "- Keep it tiny: one file only.\n"
        "- Export a small in-memory todo list API.\n"
        "- Do not touch files outside tmp/.\n"
        "- Use the headless semantic guide as a rubric: session capture, tool activity, "
        "completion signal, parser events, and tmp artifact creation.\n"
        f"- Call `{submit_artifact_tool_name}` with "
        f"artifact_type=\"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}\" "
        "and report what worked and what broke.\n"
        "- When finished, call declare_complete.\n"
    )


def _count_parsed_events(config: AgentConfig, lines: list[str]) -> int:
    parser = get_parser(config.json_parser)
    return sum(1 for _ in parser.parse(iter(lines)))


def _tool_activity_seen(config: AgentConfig, lines: list[str]) -> bool:
    strategy = strategy_for_transport(config.transport)
    for line in lines:
        signal = strategy.classify_activity_line(line)
        if signal is not None and signal.kind.value == "tool_use":
            return True
    return False


def _meaningful_output_lines(config: AgentConfig, lines: list[str]) -> list[str]:
    parser = get_parser(config.json_parser)
    collected: list[str] = []
    for parsed in parser.parse(iter(lines)):
        content = parsed.content.strip()
        if parsed.type in {"text", "thinking", "tool_use", "tool_result", "error"} and content:
            collected.append(f"{parsed.type}: {content}")
        if len(collected) >= _MAX_MEANINGFUL_OUTPUT_LINES:
            break
    return collected


def _detect_break_indicators(lines: list[str]) -> list[str]:
    errors: list[str] = []
    lowered = [line.strip().lower() for line in lines]
    if any(any(marker in line for marker in _PERMISSION_PROMPT_MARKERS) for line in lowered):
        errors.append("unexpected permission prompt observed in transcript")
    if any(any(marker in line for marker in _CRASH_MARKERS) for line in lowered):
        errors.append("crash-like transcript output observed")
    return errors


def _start_smoke_bridge(repo_root: Path, *, config: UnifiedConfig) -> SessionBridgeLike:
    workspace_scope = resolve_workspace_scope(repo_root)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    session_mcp_plan = build_session_mcp_plan(
        transport=None,
        drain="development",
        workspace_path=repo_root,
        agents_policy=agents_policy,
    )
    session = AgentSession(
        session_id=f"smoke-{uuid4().hex[:8]}",
        run_id=str(uuid4()),
        drain="development",
        capabilities=set(session_mcp_plan.capabilities),
        model_identity=session_mcp_plan.model_identity,
        stored_capability_profile=session_mcp_plan.capability_profile,
    )
    workspace = FsWorkspace(repo_root)
    return start_mcp_server(session, workspace, extra_env=session_mcp_plan.server_env)


def _smoke_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return {
        MCP_ENDPOINT_ENV: bridge.agent_endpoint_uri(),
        MCP_RUN_ID_ENV: _SMOKE_RUN_ID,
    }


def _with_session_id(options: InvokeOptions, session_id: str | None) -> InvokeOptions:
    return InvokeOptions(
        model_flag=options.model_flag,
        session_id=session_id,
        verbose=options.verbose,
        show_progress=options.show_progress,
        workspace_path=options.workspace_path,
        extra_env=options.extra_env,
        idle_timeout_seconds=options.idle_timeout_seconds,
        drain_window_seconds=options.drain_window_seconds,
        max_waiting_on_child_seconds=options.max_waiting_on_child_seconds,
        idle_poll_interval_seconds=options.idle_poll_interval_seconds,
        parent_exit_grace_seconds=options.parent_exit_grace_seconds,
        descendant_wait_timeout_seconds=options.descendant_wait_timeout_seconds,
        descendant_wait_poll_seconds=options.descendant_wait_poll_seconds,
        process_exit_wait_seconds=options.process_exit_wait_seconds,
        max_session_seconds=options.max_session_seconds,
        waiting_status_interval_seconds=options.waiting_status_interval_seconds,
        suspect_waiting_on_child_seconds=options.suspect_waiting_on_child_seconds,
        child_progress_ttl_seconds=options.child_progress_ttl_seconds,
        child_heartbeat_ttl_seconds=options.child_heartbeat_ttl_seconds,
        child_stale_label_ttl_seconds=options.child_stale_label_ttl_seconds,
        child_exit_reconcile_seconds=options.child_exit_reconcile_seconds,
        max_waiting_on_child_no_progress_seconds=options.max_waiting_on_child_no_progress_seconds,
        pure=options.pure,
        system_prompt_file=options.system_prompt_file,
        waiting_listener=options.waiting_listener,
        required_artifact=options.required_artifact,
        explicit_completion_seen=options.explicit_completion_seen,
        captured_session_id=options.captured_session_id,
        initial_session_id=options.initial_session_id,
        settings_json=options.settings_json,
        stop_sentinel_path=options.stop_sentinel_path,
    )


def _run_smoke_agent(  # noqa: PLR0912,PLR0913,PLR0915
    agent_name: str,
    config: AgentConfig,
    *,
    workspace_root: Path,
    prompt_file: Path,
    output_file: Path,
    options: InvokeOptions,
    display_context: DisplayContext,
) -> SmokeRunResult:
    all_lines: list[str] = []
    live_output_lines: list[str] = []
    current_session_id: str | None = None
    final_exception: AgentInvocationError | None = None
    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: list[str] = []
        rendered_lines: list[str] = []
        try:
            line_iter = invoke_agent(
                config,
                str(prompt_file),
                options=_with_session_id(options, current_session_id),
            )
            runner_module._stream_parsed_agent_activity(
                line_iter,
                parser_type=str(config.json_parser),
                agent_name=agent_name,
                display=None,
                transport=config.transport,
                display_context=display_context,
                raw_output_sink=raw_lines,
                rendered_output_sink=rendered_lines,
                session_id_sink=lambda session_id: None,
            )
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            break
        except OpenCodeResumableExitError as exc:
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            current_session_id = exc.resumable_session_id or extract_session_id(raw_lines)
            final_exception = exc
            continue
        except AgentInvocationError as exc:
            all_lines.extend(raw_lines or exc.parsed_output)
            live_output_lines.extend(rendered_lines)
            final_exception = exc
            break

    lines = all_lines
    session_id = current_session_id or extract_session_id(lines)
    explicit_completion_seen = any("Task declared complete:" in line for line in lines)
    parsed_event_count = _count_parsed_events(config, lines) if lines else 0
    tool_activity_seen = _tool_activity_seen(config, lines) if lines else False
    submitted_artifact = read_smoke_test_result_artifact(workspace_root) is not None
    meaningful_output_lines = [
        line for line in live_output_lines if line.strip()
    ][:_MAX_MEANINGFUL_OUTPUT_LINES]
    if not meaningful_output_lines:
        meaningful_output_lines = _meaningful_output_lines(config, lines) if lines else []

    errors = _detect_break_indicators(lines)
    if final_exception is not None:
        errors.append(str(final_exception))
    if not output_file.exists():
        errors.append("expected todo-list.js was not created")
    if session_id is None:
        errors.append("session ID was not observed")
    if not explicit_completion_seen:
        errors.append("declare_complete marker was not observed")
    if parsed_event_count == 0:
        errors.append("no parser events were observed")
    if not tool_activity_seen:
        errors.append("no tool activity was observed")
    if not submitted_artifact:
        errors.append("smoke_test_result artifact was not submitted")
    if len(meaningful_output_lines) < _MIN_MEANINGFUL_OUTPUT_LINES:
        errors.append("fewer than 3 meaningful output lines were observed")
    visible_output_count = len([line for line in live_output_lines if line.strip()])
    if visible_output_count > _MAX_VISIBLE_OUTPUT_LINES:
        errors.append(
            "interactive output overran into too many visible lines; "
            "semantic output parity is still insufficient"
        )

    transport_name = config.transport.value if config.transport is not None else "generic"
    return SmokeRunResult(
        agent_name=agent_name,
        transport=transport_name,
        output_file=output_file,
        file_created=output_file.exists(),
        session_id=session_id,
        explicit_completion_seen=explicit_completion_seen,
        raw_line_count=visible_output_count,
        parsed_event_count=parsed_event_count,
        tool_activity_seen=tool_activity_seen,
        artifact_submitted=submitted_artifact,
        meaningful_output_lines=meaningful_output_lines,
        errors=errors,
    )


def _render_smoke_report(results: list[SmokeRunResult]) -> str:
    """Render a human-readable parity report."""

    lines = [
        "Interactive Claude parity smoke report",
        "",
        f"Headless semantic guide: {_HEADLESS_SEMANTIC_GUIDE}",
        "",
    ]
    for result in results:
        lines.append(f"Agent: {result.agent_name} ({result.transport})")
        lines.append("Observed working:")
        working: list[str] = []
        if result.file_created:
            working.append(f"- created {result.output_file}")
        if result.session_id is not None:
            working.append(f"- session ID observed: {result.session_id}")
        if result.explicit_completion_seen:
            working.append("- declare_complete marker observed")
        if result.parsed_event_count > 0:
            working.append(f"- parser emitted {result.parsed_event_count} event(s)")
        if result.tool_activity_seen:
            working.append("- tool activity observed")
        if result.artifact_submitted:
            working.append("- smoke_test_result artifact submitted")
        lines.extend(working or ["- none"])
        lines.append("Observed output:")
        lines.extend([f"- {line}" for line in result.meaningful_output_lines] or ["- none"])
        lines.append("Observed breaks:")
        lines.extend([f"- {error}" for error in result.errors] or ["- No breaks observed"])
        if any("no output" in error.lower() for error in result.errors):
            lines.append(
                "- HUGE RED FLAG: repeated 'idle watchdog: drain window active' logs "
                "before firing mean the interpreter lost semantic visibility while "
                "the watchdog kept doing its job."
            )
        if any("overran" in error.lower() for error in result.errors):
            lines.append(
                "- HUGE RED FLAG: the interactive stream printed too many visible "
                "lines without enough semantic compression, so operator-visible "
                "parity is still broken."
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_smoke_table(results: list[SmokeRunResult], *, display_context: DisplayContext) -> None:
    console = display_context.console
    table = Table(title="Interactive Claude parity smoke test", show_lines=False)
    table.add_column("Agent")
    table.add_column("Transport")
    table.add_column("File")
    table.add_column("Session")
    table.add_column("Parser events")
    table.add_column("Tool activity")
    table.add_column("Artifact")
    table.add_column("Breaks")

    for result in results:
        table.add_row(
            result.agent_name,
            result.transport,
            "yes" if result.file_created else "no",
            result.session_id or "missing",
            str(result.parsed_event_count),
            "yes" if result.tool_activity_seen else "no",
            "yes" if result.artifact_submitted else "no",
            "none" if not result.errors else "; ".join(result.errors),
        )
    console.print(table)
    console.print(Panel(_render_smoke_report(results), title="Detailed report"))


def smoke_interactive_claude_command(*, display_context: DisplayContext | None = None) -> int:
    """Run a token-consuming manual parity smoke test for interactive Claude."""

    ctx = display_context if display_context is not None else make_display_context()
    workspace_scope = resolve_workspace_scope()
    workspace_root = workspace_scope.root
    smoke_dir = workspace_root / _SMOKE_RELATIVE_DIR
    smoke_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = workspace_root / _PROMPT_FILE
    output_file = workspace_root / _SMOKE_OUTPUT_FILE

    config = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(_INTERACTIVE_AGENT)
    if agent_config is None:
        raise RuntimeError(
            f"Smoke test agent '{_INTERACTIVE_AGENT}' is unavailable in the registry"
        )

    submit_artifact_tool_name = _submit_artifact_tool_name_for_transport(agent_config.transport)
    prompt_file.write_text(
        _build_smoke_prompt(
            _SMOKE_OUTPUT_FILE.as_posix(),
            submit_artifact_tool_name=submit_artifact_tool_name,
        ),
        encoding="utf-8",
    )

    bridge = _start_smoke_bridge(workspace_root, config=config)
    try:
        if output_file.exists():
            output_file.unlink()
        options = build_invoke_options_from_config(
            config.general,
            verbose=False,
            show_progress=False,
            workspace_path=workspace_root,
            extra_env=_smoke_bridge_env(bridge),
            pure=agent_config.transport == AgentTransport.OPENCODE,
        )
        options = InvokeOptions(
            model_flag=options.model_flag,
            session_id=options.session_id,
            verbose=options.verbose,
            show_progress=options.show_progress,
            workspace_path=options.workspace_path,
            extra_env=options.extra_env,
            idle_timeout_seconds=_SMOKE_IDLE_TIMEOUT_SECONDS,
            drain_window_seconds=options.drain_window_seconds,
            max_waiting_on_child_seconds=options.max_waiting_on_child_seconds,
            idle_poll_interval_seconds=options.idle_poll_interval_seconds,
            parent_exit_grace_seconds=options.parent_exit_grace_seconds,
            descendant_wait_timeout_seconds=options.descendant_wait_timeout_seconds,
            descendant_wait_poll_seconds=options.descendant_wait_poll_seconds,
            process_exit_wait_seconds=options.process_exit_wait_seconds,
            max_session_seconds=_SMOKE_MAX_SESSION_SECONDS,
            waiting_status_interval_seconds=options.waiting_status_interval_seconds,
            suspect_waiting_on_child_seconds=options.suspect_waiting_on_child_seconds,
            child_progress_ttl_seconds=options.child_progress_ttl_seconds,
            child_heartbeat_ttl_seconds=options.child_heartbeat_ttl_seconds,
            child_stale_label_ttl_seconds=options.child_stale_label_ttl_seconds,
            child_exit_reconcile_seconds=options.child_exit_reconcile_seconds,
            max_waiting_on_child_no_progress_seconds=options.max_waiting_on_child_no_progress_seconds,
            pure=options.pure,
            system_prompt_file=options.system_prompt_file,
            waiting_listener=options.waiting_listener,
            required_artifact=options.required_artifact,
            explicit_completion_seen=options.explicit_completion_seen,
            captured_session_id=options.captured_session_id,
        )
        results = [
            _run_smoke_agent(
                _INTERACTIVE_AGENT,
                agent_config,
                workspace_root=workspace_root,
                prompt_file=prompt_file,
                output_file=output_file,
                options=options,
                display_context=ctx,
            )
        ]
    finally:
        bridge.shutdown()

    _render_smoke_table(results, display_context=ctx)
    return 0 if all(not result.errors for result in results) else 1


__all__ = [
    "SmokeRunResult",
    "_build_smoke_prompt",
    "_render_smoke_report",
    "smoke_interactive_claude_command",
]
