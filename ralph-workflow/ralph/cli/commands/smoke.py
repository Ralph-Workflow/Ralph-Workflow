"""Manual smoke tests for expensive agent-runtime checks.

These smoke tests are intentionally excluded from the verify pipeline because they
consume live agent tokens. They exist to validate the real invoke_agent pipeline
against a live agent runtime, especially interactive-Claude parity, when changing
the runtime. A smoke fix is only valid when it improves the shared runtime path,
not when it special-cases this command alone.

The orchestration core lives in :mod:`ralph.pipeline.plumbing.smoke_plumbing`; this
module is the thin CLI surface (option setup, report rendering, exit codes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.pipeline.factory import build_default_pipeline_deps
from ralph.pipeline.plumbing.smoke_plumbing import (
    _SMOKE_IDLE_TIMEOUT_SECONDS,
    _SMOKE_MAX_SESSION_SECONDS,
    _SMOKE_MAX_TURNS,
    _SMOKE_OUTPUT_FILE,
    _SMOKE_RELATIVE_DIR,
    SmokeRunResult,
    _build_smoke_prompt,
    _execute_smoke_turns,
    run_smoke_plumbing,
)
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig

# Re-export plumbing symbols so existing tests can still reach them.
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

_INTERACTIVE_AGENT = "claude/haiku"
_HEADLESS_SEMANTIC_GUIDE = (
    "session capture, tool activity, completion signal, parser events, and tmp/ artifact creation"
)


__all__ = [
    "MCP_ENDPOINT_ENV",
    "_SMOKE_IDLE_TIMEOUT_SECONDS",
    "_SMOKE_MAX_SESSION_SECONDS",
    "_SMOKE_MAX_TURNS",
    "SmokeRunParams",
    "SmokeRunResult",
    "_execute_smoke_turns",
    "build_smoke_prompt",
    "render_smoke_report",
    "smoke_interactive_claude_command",
]


build_smoke_prompt = _build_smoke_prompt


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
    display = resolve_active_display(None, display_context)
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
    display.emit_renderable(table)
    display.emit_info_panel(
        title="Detailed report",
        content=_render_smoke_report(results),
    )


render_smoke_report = _render_smoke_report


def smoke_interactive_claude_command(*, display_context: DisplayContext | None = None) -> int:
    """Run a token-consuming manual parity smoke test for interactive Claude."""
    ctx = display_context if display_context is not None else make_display_context()
    workspace_scope = resolve_workspace_scope()
    workspace_root = workspace_scope.root
    smoke_dir = workspace_root / _SMOKE_RELATIVE_DIR
    smoke_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = workspace_root / _SMOKE_RELATIVE_DIR / "PROMPT.md"
    output_file = workspace_root / _SMOKE_OUTPUT_FILE

    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(_INTERACTIVE_AGENT)
    if agent_config is None:
        raise RuntimeError(
            f"Smoke test agent '{_INTERACTIVE_AGENT}' is unavailable in the registry"
        )

    submit_artifact_tool_name = submit_artifact_tool_name_for_transport(agent_config.transport)
    prompt_file.write_text(
        _build_smoke_prompt(
            _SMOKE_OUTPUT_FILE.as_posix(),
            submit_artifact_tool_name=submit_artifact_tool_name,
        ),
        encoding="utf-8",
    )

    pipeline_deps = build_default_pipeline_deps(config, ctx)

    result = run_smoke_plumbing(
        config=config,
        workspace_root=workspace_root,
        agent_name=_INTERACTIVE_AGENT,
        prompt_file=prompt_file,
        output_file=output_file,
        display_context=ctx,
        pipeline_deps=pipeline_deps,
    )

    _render_smoke_table([result], display_context=ctx)
    return 0 if not result.errors else 1
