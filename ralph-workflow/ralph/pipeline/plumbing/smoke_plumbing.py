"""Smoke-test plumbing: shared core for the interactive-Claude parity check.

This module is the single owner of the smoke-test agent-invocation loop.
The CLI surface in :mod:`ralph.cli.commands.smoke` stays thin (option
parsing, report rendering, exit codes only).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    OpenCodeResumableExitError,
    extract_transport_session_id,
    invoke_agent,
)
from ralph.agents.parsers import get_parser
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineCore, PipelineDeps
from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.pipeline.session_bridge import build_session_bridge
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.pro_support.hooks import ProPipelineHooks

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_INTERACTIVE_AGENT = "claude/haiku"
_SMOKE_RUN_ID = "interactive-claude-smoke"
_AGY_SMOKE_RELATIVE_DIR = Path("tmp/interactive-agy-smoke")
_AGY_SMOKE_OUTPUT_FILE = _AGY_SMOKE_RELATIVE_DIR / "todo-list.js"


@dataclass(frozen=True)
class SmokeHarnessSpec:
    """Layout specification for an interactive smoke harness."""

    agent_name: str
    relative_dir: Path
    output_file: Path
    run_id: str


def resolve_smoke_harness_spec(agent_name: str) -> SmokeHarnessSpec:
    """Return the smoke harness layout for ``agent_name``.

    The ``claude/haiku`` branch preserves the legacy layout so existing
    on-disk artifacts and tests are not orphaned. The ``agy/<model>`` branch
    uses a separate ``tmp/interactive-agy-smoke`` directory so the two
    harnesses can run side by side without collisions.
    """
    if agent_name == _INTERACTIVE_AGENT:
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_SMOKE_RELATIVE_DIR,
            output_file=_SMOKE_OUTPUT_FILE,
            run_id=_SMOKE_RUN_ID,
        )
    if agent_name.startswith("agy/"):
        model = agent_name.removeprefix("agy/")
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model).strip("-")
        run_id = f"interactive-agy-smoke-{sanitized}"
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_AGY_SMOKE_RELATIVE_DIR,
            output_file=_AGY_SMOKE_OUTPUT_FILE,
            run_id=run_id,
        )
    raise ValueError(f"No smoke harness spec defined for agent '{agent_name}'")
_SMOKE_IDLE_TIMEOUT_SECONDS = 30.0
_SMOKE_MAX_SESSION_SECONDS = 120.0
# Per-agent session ceiling overrides. AGY's default --print-timeout is 5m
# (measured in tmp/agy-source-of-truth.txt); give it a 6m ceiling so the smoke
# harness does not kill a run that AGY still considers active.
_AGENT_SESSION_CEILINGS: dict[str, float] = {
    "claude": 120.0,
    "agy": 360.0,
}
_SMOKE_MAX_TURNS = 5
_SMOKE_TRANSCRIPT_MAX_LINES = 400
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
_MAX_VISIBLE_OUTPUT_LINES = 80

# Crash-detector patterns are anchored to specific error signatures so that
# incidental words like "crash" in an agent's planning prose do not poison the
# smoke report.
_CRASH_PATTERNS = (
    re.compile(r"^Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"^thread .* panicked at", re.IGNORECASE),
    re.compile(
        r"segmentation fault \(core dumped\)|SIGSEGV|Aborted \(core dumped\)",
        re.IGNORECASE,
    ),
    re.compile(r"^fatal:\s", re.IGNORECASE),
)

# AGY plain-text tool-call markers. The captured AGY transcript at
# tmp/agy-live-transcript.txt did not contain these markers in the current
# binary version, but detecting them keeps the parser aligned with other
# headless agents that emit plain-text tool announcements.
_AGY_TOOL_USE_PATTERNS = (
    re.compile(r"^(?:Calling tool|Using tool|Tool call):\s*(\S+)", re.IGNORECASE),
    re.compile(r"^(?:rag_tap|Read|Write|Edit|Glob|Grep|Bash|LS)\s*\(", re.IGNORECASE),
)
_AGY_TOOL_MARKER_RE = re.compile(
    "|".join(f"(?:{p.pattern})" for p in _AGY_TOOL_USE_PATTERNS),
    re.IGNORECASE,
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
    if any(pattern.search(line) for line in lowered for pattern in _CRASH_PATTERNS):
        errors.append("crash-like transcript output observed")
    return errors


def _execute_smoke_turns(
    params: SmokeRunParams,
    current_session_id: str | None,
    run_id: str = _SMOKE_RUN_ID,
) -> tuple[list[str], list[str], str | None, AgentInvocationError | None]:
    """Execute smoke test turns and return collected lines and state."""
    all_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    live_output_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    final_exception: AgentInvocationError | None = None
    workspace_scope = resolve_workspace_scope(params.workspace_root)

    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        rendered_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        observed_session_id: str | None = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id

        effect = InvokeAgentEffect(
            agent_name=params.agent_name,
            phase="development",
            prompt_file=str(params.prompt_file),
            drain="development",
        )
        pipeline_deps = params.pipeline_deps
        if pipeline_deps is None:
            raise RuntimeError("SmokeRunParams.pipeline_deps is required")
        try:
            event = execute_agent_effect(
                effect,
                params.unified_config,
                pipeline_deps,
                workspace_scope,
                bridge=cast("RestartAwareMcpBridge", params.bridge),
                display_context=params.display_context,
                run_id=run_id,
                raw_output_sink=raw_lines,
                rendered_output_sink=rendered_lines,
                set_session_id_cb=_capture_session_id,
                invoke_agent=invoke_agent,
                raise_resumable_exit=True,
            )
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            current_session_id = observed_session_id or extract_transport_session_id(
                tuple(raw_lines)
            )
            final_exception = None
            if event == PipelineEvent.AGENT_SUCCESS:
                break
            # Non-success event from the shared core ends the turn loop.
            break
        except OpenCodeResumableExitError as exc:
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            current_session_id = (
                exc.resumable_session_id
                or observed_session_id
                or extract_transport_session_id(tuple(raw_lines))
            )
            final_exception = exc
            continue
        except AgentInvocationError as exc:
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            merged_output = list(raw_lines)
            for line in exc.parsed_output:
                if line not in merged_output:
                    merged_output.append(line)
            if merged_output:
                exc.parsed_output = merged_output
            final_exception = exc
            break

    return list(all_lines), list(live_output_lines), current_session_id, final_exception


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
    tool_activity_seen: bool | None = None,
) -> list[str]:
    """Detect errors in smoke run results."""
    errors = _detect_break_indicators(lines)
    if final_exception is not None:
        errors.append(str(final_exception))
    if not params.output_file.exists():
        errors.append("expected todo-list.js was not created")
    if session_id is None and params.config.transport != AgentTransport.AGY:
        errors.append("session ID was not observed")

    explicit_completion_seen = any("Task declared complete:" in line for line in lines)
    if not explicit_completion_seen and params.config.transport == AgentTransport.AGY:
        artifact = read_smoke_test_result_artifact(params.workspace_root)
        if isinstance(artifact, dict):
            observed_breaks = artifact.get("observed_breaks")
            headless_checks = artifact.get("headless_guide_checks")
            if (
                isinstance(observed_breaks, list)
                and isinstance(headless_checks, list)
                and len(observed_breaks) == 0
                and len(headless_checks) >= 1
            ):
                explicit_completion_seen = True
    if not explicit_completion_seen:
        errors.append("declare_complete marker was not observed")

    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    if parsed_event_count == 0:
        errors.append("no parser events were observed")

    if tool_activity_seen is None:
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


def _agy_tool_activity_seen(lines: list[str], workspace_root: Path) -> bool:
    """AGY-specific tool-activity fallback using the persisted artifact."""
    artifact = read_smoke_test_result_artifact(workspace_root)
    if isinstance(artifact, dict):
        checks = artifact.get("headless_guide_checks")
        if isinstance(checks, list) and "tool activity" in checks:
            return True
    return any(_AGY_TOOL_MARKER_RE.search(line) for line in lines)


def _run_smoke_agent(
    params: SmokeRunParams,
    run_id: str = _SMOKE_RUN_ID,
) -> SmokeRunResult:
    """Run the smoke agent and return results."""
    all_lines, live_output_lines, current_session_id, final_exception = _execute_smoke_turns(
        params, None, run_id=run_id
    )

    lines = all_lines
    session_id = current_session_id or extract_transport_session_id(tuple(lines))
    explicit_completion_seen = any("Task declared complete:" in line for line in lines)
    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    tool_activity_seen = _tool_activity_seen(params.config, lines) if lines else False
    if not tool_activity_seen and params.config.transport == AgentTransport.AGY:
        tool_activity_seen = _agy_tool_activity_seen(lines, params.workspace_root)
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
        tool_activity_seen=tool_activity_seen,
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
    output_file: Path | None = None,
    display_context: DisplayContext | None = None,
    pipeline_core: PipelineCore | None = None,
    bridge_factory: BridgeFactory | None = None,
    pipeline_deps: PipelineDeps | None = None,
    pro_hooks: ProPipelineHooks | None = None,
) -> SmokeRunResult:
    """Run the interactive smoke test for ``agent_name`` and return the result.

    Callers may supply either the modular ``pipeline_core`` + ``bridge_factory``
    surface or the legacy extended ``pipeline_deps`` bundle. When
    ``pipeline_deps`` is provided it is used for backward compatibility and
    its ``core`` and ``bridge_factory`` are derived automatically. When both
    are omitted, production defaults are built through
    :class:`DefaultPipelineFactory` so the plumbing-direct-call path shares
    the same composition root as the main pipeline; ``pro_hooks`` is forwarded
    so a Pro subclassed factory is honored.
    """
    spec = resolve_smoke_harness_spec(agent_name)
    if pipeline_deps is not None:
        if display_context is None:
            display_context = pipeline_deps.display_context
        effective_pipeline_deps = pipeline_deps
        effective_core = pipeline_deps.core
        effective_bridge_factory = pipeline_deps.bridge_factory
    elif pipeline_core is not None:
        if display_context is None:
            display_context = pipeline_core.display_context
        effective_bridge_factory = bridge_factory or build_session_bridge
        effective_pipeline_deps = PipelineDeps(
            core=pipeline_core,
            bridge_factory=effective_bridge_factory,
        )
        effective_core = pipeline_core
    else:
        if display_context is None:
            raise ValueError(
                "display_context is required when pipeline_deps and pipeline_core are not provided"
            )
        effective_pipeline_deps = DefaultPipelineFactory().build(
            config, display_context, pro_hooks=pro_hooks
        )
        display_context = effective_pipeline_deps.display_context
        effective_core = effective_pipeline_deps.core
        effective_bridge_factory = effective_pipeline_deps.bridge_factory

    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise RuntimeError(
            f"Smoke test agent '{agent_name}' is unavailable in the registry"
        )

    effective_output_file = output_file if output_file is not None else spec.output_file

    agents_policy = None
    if pipeline_deps is not None and pipeline_deps.policy_bundle is not None:
        agents_policy = pipeline_deps.policy_bundle.agents
    if agents_policy is None:
        workspace_scope = resolve_workspace_scope(workspace_root)
        agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)

    with with_bridge_lifetime(
        effective_core,
        effective_bridge_factory,
        repo_root=workspace_root,
        drain="development",
        session_id_prefix="smoke",
        agents_policy=agents_policy,
    ) as bridge:
        if effective_output_file.exists():
            effective_output_file.unlink()
        _clear_smoke_artifact(workspace_root)

        # Honor per-agent session ceilings so AGY's longer --print-timeout is not
        # cut off by the legacy 120s default. See _AGENT_SESSION_CEILINGS.
        agent_prefix = agent_name.split("/", maxsplit=1)[0]
        session_ceiling = _AGENT_SESSION_CEILINGS.get(
            agent_prefix, _SMOKE_MAX_SESSION_SECONDS
        )
        smoke_general = config.general.model_copy(
            update={
                "agent_idle_timeout_seconds": _SMOKE_IDLE_TIMEOUT_SECONDS,
                "agent_max_session_seconds": session_ceiling,
            }
        )
        smoke_config = config.model_copy(update={"general": smoke_general})

        results = [
            _run_smoke_agent(
                SmokeRunParams(
                    agent_name=agent_name,
                    config=agent_config,
                    unified_config=smoke_config,
                    workspace_root=workspace_root,
                    prompt_file=prompt_file,
                    output_file=effective_output_file,
                    options=InvokeOptions(),
                    display_context=display_context,
                    bridge=bridge,
                    pipeline_deps=effective_pipeline_deps,
                ),
                run_id=spec.run_id,
            )
        ]

    return results[0]


__all__ = [
    "SmokeHarnessSpec",
    "SmokeRunResult",
    "_build_smoke_prompt",
    "_execute_smoke_turns",
    "_run_smoke_agent",
    "resolve_smoke_harness_spec",
    "run_smoke_plumbing",
]
