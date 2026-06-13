"""Smoke-test plumbing: shared core for the interactive-Claude parity check.

This module is the single owner of the smoke-test agent-invocation loop.
The CLI surface in :mod:`ralph.cli.commands.smoke` stays thin (option
parsing, report rendering, exit codes only).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    InvokeRuntimeOptions,
    OpenCodeResumableExitError,
    build_invoke_options_from_config,
    extract_transport_session_id,
    invoke_agent,
)
from ralph.agents.invoke._direct_mcp_recovery import (
    default_direct_mcp_retry_limit,
    run_with_direct_mcp_recovery,
)
from ralph.agents.parsers import get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.pipeline.activity_stream import stream_parsed_agent_activity
from ralph.pipeline.factory import build_default_pipeline_deps
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.pipeline.session_bridge import (
    bridge_env_for,
    build_session_bridge,
    reset_tool_registry_callback,
)
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.factory import PipelineDeps

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_INTERACTIVE_AGENT = "claude/haiku"
_SMOKE_RUN_ID = "interactive-claude-smoke"
_SMOKE_IDLE_TIMEOUT_SECONDS = 30.0
_SMOKE_MAX_SESSION_SECONDS = 120.0
_SMOKE_MAX_TURNS = 5
_SMOKE_TRANSCRIPT_MAX_LINES = 400
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
_MAX_VISIBLE_OUTPUT_LINES = 80


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
        f'artifact_type="{SMOKE_TEST_RESULT_ARTIFACT_TYPE}" '
        "and use this exact content schema: "
        'status: one of "passed", "failed", or "partial"; '
        f'output_file: "{output_relpath}"; '
        "observed_working: string[]; observed_breaks: string[]; "
        "headless_guide_checks: string[]; summary: non-empty string.\n"
        "- Do not nest extra objects like rubric/details/metadata inside the artifact content.\n"
        "- When finished, call declare_complete.\n"
    )


def _parser_key_for_config(config: AgentConfig) -> str:
    if config.transport == AgentTransport.CLAUDE_INTERACTIVE:
        return "claude_interactive"
    return config.json_parser


def _count_parsed_events(config: AgentConfig, lines: list[str]) -> int:
    parser = get_parser(_parser_key_for_config(config))
    return sum(1 for _ in parser.parse(iter(lines)))


def _tool_activity_seen(config: AgentConfig, lines: list[str]) -> bool:
    strategy = strategy_for_transport(config.transport)
    for line in lines:
        signal = strategy.classify_activity_line(line)
        if signal is not None and signal.kind.value == "tool_use":
            return True
    return False


def _meaningful_output_lines(config: AgentConfig, lines: list[str]) -> list[str]:
    parser = get_parser(_parser_key_for_config(config))
    collected: list[str] = []
    for parsed in parser.parse(iter(lines)):
        content = parsed.content.strip()
        if parsed.type in {"text", "thinking", "tool_use", "tool_result", "error"} and content:
            collected.append(f"{parsed.type}: {content}")
        if len(collected) >= _MAX_MEANINGFUL_OUTPUT_LINES:
            break
    return collected


def _looks_like_permission_prompt_surface(line: str) -> bool:
    normalized = normalize_vt_text(line).lower()
    if not normalized.strip():
        return False
    if "bypass permissions on" in normalized:
        return False
    has_confirm_footer = "enter to confirm" in normalized or "esc to cancel" in normalized
    prompt_shaped_markers = (
        "claude requested permissions",
        "allow this action?",
        "enable auto mode?",
        "yes, i accept",
        "yes, i trust this folder",
    )
    return has_confirm_footer and any(marker in normalized for marker in prompt_shaped_markers)


def _detect_break_indicators(lines: list[str]) -> list[str]:
    errors: list[str] = []
    if any(_looks_like_permission_prompt_surface(line) for line in lines):
        errors.append("unexpected permission prompt observed in transcript")
    lowered = [line.strip().lower() for line in lines]
    crash_markers = (
        "traceback",
        "fatal",
        "segmentation fault",
        "panic",
        "crash",
    )
    if any(any(marker in line for marker in crash_markers) for line in lowered):
        errors.append("crash-like transcript output observed")
    return errors


def _start_smoke_bridge(
    repo_root: Path,
    *,
    config: UnifiedConfig,
    model_identity: MultimodalModelIdentity | None = None,
) -> SessionBridgeLike:
    workspace_scope = resolve_workspace_scope(repo_root)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    return build_session_bridge(
        workspace_root=repo_root,
        drain="development",
        agents_policy=agents_policy,
        session_id_prefix="smoke",
        model_identity=model_identity,
    )


def _smoke_bridge_env(bridge: SessionBridgeLike) -> dict[str, str]:
    return bridge_env_for(bridge, run_id_label=_SMOKE_RUN_ID)


def _with_session_id(options: InvokeOptions, session_id: str | None) -> InvokeOptions:
    return replace(options, session_id=session_id)


def _execute_smoke_turns(
    params: SmokeRunParams,
    current_session_id: str | None,
) -> tuple[list[str], list[str], str | None, AgentInvocationError | None]:
    """Execute smoke test turns and return collected lines and state."""
    all_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    live_output_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    final_exception: AgentInvocationError | None = None

    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: list[str] = []
        active_session_id = current_session_id
        observed_session_id = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id

        def _run_retry_attempt(
            retry_session_id: str | None,
            capture_session_id: Callable[[str], None],
            bound_session_id: str | None = active_session_id,
        ) -> tuple[list[str], list[str]]:
            return _run_smoke_attempt(
                params,
                _with_session_id(params.options, retry_session_id or bound_session_id),
                session_id_sink=capture_session_id,
            )

        try:
            raw_lines, rendered_lines = run_with_direct_mcp_recovery(
                _run_retry_attempt,
                max_retries=default_direct_mcp_retry_limit(_SMOKE_MAX_TURNS - 1),
                reset_tool_registry=cast(
                    "Callable[[], object] | None",
                    reset_tool_registry_callback(params.bridge),
                ),
                on_retry_failure=all_lines.extend,
                on_session_observed=_capture_session_id,
            )
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            current_session_id = observed_session_id or extract_transport_session_id(raw_lines)
            final_exception = None
            break
        except OpenCodeResumableExitError as exc:
            current_session_id = (
                exc.resumable_session_id
                or observed_session_id
                or extract_transport_session_id(raw_lines)
            )
            final_exception = exc
            continue
        except AgentInvocationError as exc:
            all_lines.extend(exc.parsed_output)
            final_exception = exc
            break

    return list(all_lines), list(live_output_lines), current_session_id, final_exception


def _run_smoke_attempt(
    params: SmokeRunParams,
    options: InvokeOptions,
    *,
    session_id_sink: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str]]:
    raw_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    rendered_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    line_iter = invoke_agent(
        params.config,
        str(params.prompt_file),
        options=options,
    )
    display = ParallelDisplay(
        params.display_context,
        workspace_root=params.workspace_root,
    )
    try:
        stream_parsed_agent_activity(
            line_iter,
            parser_type=str(params.config.json_parser),
            agent_name=params.agent_name,
            display=display,
            transport=params.config.transport,
            display_context=params.display_context,
            raw_output_sink=raw_lines,
            rendered_output_sink=rendered_lines,
            session_id_sink=session_id_sink,
        )
    except AgentInvocationError as exc:
        merged_output = list(raw_lines)
        for line in exc.parsed_output:
            if line not in merged_output:
                merged_output.append(line)
        if merged_output:
            exc.parsed_output = merged_output
        raise
    return list(raw_lines), list(rendered_lines)


def _clear_smoke_artifact(workspace_root: Path) -> None:
    artifact_path = (
        workspace_root / ".agent" / "artifacts" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.json"
    )
    artifact_path.unlink(missing_ok=True)


def _detect_smoke_errors(
    params: SmokeRunParams,
    lines: list[str],
    live_output_lines: list[str],
    session_id: str | None,
    final_exception: AgentInvocationError | None,
) -> list[str]:
    """Detect errors in smoke run results."""
    errors = _detect_break_indicators(lines)
    if final_exception is not None:
        errors.append(str(final_exception))
    if not params.output_file.exists():
        errors.append("expected todo-list.js was not created")
    if session_id is None:
        errors.append("session ID was not observed")

    explicit_completion_seen = any("Task declared complete:" in line for line in lines)
    if not explicit_completion_seen:
        errors.append("declare_complete marker was not observed")

    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    if parsed_event_count == 0:
        errors.append("no parser events were observed")

    tool_activity_seen = _tool_activity_seen(params.config, lines) if lines else False
    if not tool_activity_seen:
        errors.append("no tool activity was observed")

    if not read_smoke_test_result_artifact(params.workspace_root):
        errors.append("smoke_test_result artifact was not submitted")

    meaningful_output = [line for line in live_output_lines if line.strip()]
    if len(meaningful_output) < _MIN_MEANINGFUL_OUTPUT_LINES and lines:
        meaningful_output = _meaningful_output_lines(params.config, lines)
    meaningful_output = meaningful_output[:_MAX_MEANINGFUL_OUTPUT_LINES]
    if len(meaningful_output) < _MIN_MEANINGFUL_OUTPUT_LINES:
        errors.append("fewer than 3 meaningful output lines were observed")

    visible_output_count = len([line for line in live_output_lines if line.strip()])
    if visible_output_count > _MAX_VISIBLE_OUTPUT_LINES:
        errors.append(
            "interactive output overran into too many visible lines; "
            "semantic output parity is still insufficient"
        )
    return errors


def _run_smoke_agent(params: SmokeRunParams) -> SmokeRunResult:
    """Run the smoke agent and return results."""
    all_lines, live_output_lines, current_session_id, final_exception = _execute_smoke_turns(
        params, None
    )

    lines = all_lines
    session_id = current_session_id or extract_transport_session_id(lines)
    explicit_completion_seen = any("Task declared complete:" in line for line in lines)
    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    tool_activity_seen = _tool_activity_seen(params.config, lines) if lines else False
    meaningful_output_lines = [line for line in live_output_lines if line.strip()][
        :_MAX_MEANINGFUL_OUTPUT_LINES
    ]
    if not meaningful_output_lines:
        meaningful_output_lines = _meaningful_output_lines(params.config, lines) if lines else []

    errors = _detect_smoke_errors(
        params,
        lines,
        live_output_lines,
        session_id,
        final_exception,
    )

    config = params.config
    transport_name = config.transport.value if config.transport is not None else "generic"
    return SmokeRunResult(
        agent_name=params.agent_name,
        transport=transport_name,
        output_file=params.output_file,
        file_created=params.output_file.exists(),
        session_id=session_id,
        explicit_completion_seen=explicit_completion_seen,
        raw_line_count=len([line for line in lines if line.strip()]),
        parsed_event_count=parsed_event_count,
        tool_activity_seen=tool_activity_seen,
        artifact_submitted=read_smoke_test_result_artifact(params.workspace_root) is not None,
        meaningful_output_lines=meaningful_output_lines,
        errors=errors,
    )


def run_smoke_plumbing(
    *,
    config: UnifiedConfig,
    workspace_root: Path,
    agent_name: str,
    prompt_file: Path,
    output_file: Path,
    display_context: DisplayContext,
    pipeline_deps: PipelineDeps | None = None,
) -> SmokeRunResult:
    """Run the interactive-Claude smoke test and return the observed result.

    ``pipeline_deps`` carries injectable collaborators. When omitted,
    production defaults are used and the bridge is constructed with the
    same session planning path as the main pipeline.
    """
    pipeline_deps_provided = pipeline_deps is not None
    if pipeline_deps is None:
        pipeline_deps = build_default_pipeline_deps(config, display_context)

    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise RuntimeError(
            f"Smoke test agent '{agent_name}' is unavailable in the registry"
        )

    if pipeline_deps_provided:
        bridge = pipeline_deps.bridge_factory(
            workspace_root=workspace_root,
            drain="development",
            agents_policy=None,
            session_id_prefix="smoke",
            model_identity=pipeline_deps.model_identity,
        )
    else:
        bridge = _start_smoke_bridge(workspace_root, config=config)

    try:
        if output_file.exists():
            output_file.unlink()
        _clear_smoke_artifact(workspace_root)
        options = build_invoke_options_from_config(
            config.general,
            InvokeRuntimeOptions(
                verbose=False,
                show_progress=False,
                workspace_path=workspace_root,
                extra_env=_smoke_bridge_env(bridge),
                pure=agent_config.transport == AgentTransport.OPENCODE,
            ),
        )
        options = replace(
            options,
            idle_timeout_seconds=_SMOKE_IDLE_TIMEOUT_SECONDS,
            max_session_seconds=_SMOKE_MAX_SESSION_SECONDS,
        )
        results = [
            _run_smoke_agent(
                SmokeRunParams(
                    agent_name=agent_name,
                    config=agent_config,
                    workspace_root=workspace_root,
                    prompt_file=prompt_file,
                    output_file=output_file,
                    options=options,
                    display_context=display_context,
                    bridge=bridge,
                )
            )
        ]
    finally:
        bridge.shutdown()

    return results[0]


__all__ = [
    "SmokeRunResult",
    "_build_smoke_prompt",
    "_execute_smoke_turns",
    "_run_smoke_agent",
    "_run_smoke_attempt",
    "run_smoke_plumbing",
]
