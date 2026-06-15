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

import shutil
from typing import TYPE_CHECKING

from loguru import logger
from rich.table import Table

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    _SMOKE_IDLE_TIMEOUT_SECONDS,
    _SMOKE_MAX_SESSION_SECONDS,
    _SMOKE_MAX_TURNS,
    SmokeRunResult,
    _build_smoke_prompt,
    _execute_smoke_turns,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pro_support.hooks import ProPipelineHooks

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
    "smoke_interactive_agy_command",
    "smoke_interactive_claude_command",
]


build_smoke_prompt = _build_smoke_prompt


def _render_smoke_report(
    results: list[SmokeRunResult],
    *,
    agent_name: str = "claude",
) -> str:
    """Render a human-readable parity report."""
    lines = [
        f"{agent_name} parity smoke report",
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


def _render_smoke_table(
    results: list[SmokeRunResult],
    *,
    display_context: DisplayContext,
    agent_name: str = "claude",
) -> None:
    display = resolve_active_display(None, display_context)
    table = Table(title=f"{agent_name} parity smoke test", show_lines=False)
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
        content=_render_smoke_report(results, agent_name=agent_name),
    )


render_smoke_report = _render_smoke_report


def smoke_harness_agent_command(
    agent_name: str,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> int:
    """Run the interactive smoke harness for ``agent_name`` and report parity."""
    ctx = display_context if display_context is not None else make_display_context()
    workspace_scope = resolve_workspace_scope()
    workspace_root = workspace_scope.root
    spec = resolve_smoke_harness_spec(agent_name)
    smoke_dir = workspace_root / spec.relative_dir
    smoke_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = workspace_root / spec.relative_dir / "PROMPT.md"
    output_file = workspace_root / spec.output_file

    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise RuntimeError(
            f"Smoke test agent '{agent_name}' is unavailable in the registry"
        )

    submit_artifact_tool_name = submit_artifact_tool_name_for_transport(agent_config.transport)
    prompt_file.write_text(
        _build_smoke_prompt(
            spec.output_file.as_posix(),
            submit_artifact_tool_name=submit_artifact_tool_name,
            transport=agent_config.transport,
        ),
        encoding="utf-8",
    )

    deps = DefaultPipelineFactory().build(
        config,
        ctx,
        model_identity=model_identity,
        pro_hooks=pro_hooks,
    )

    result = run_smoke_plumbing(
        config=config,
        workspace_root=workspace_root,
        agent_name=agent_name,
        prompt_file=prompt_file,
        output_file=output_file,
        display_context=ctx,
        pipeline_deps=deps,
    )

    _render_smoke_table([result], display_context=ctx, agent_name=agent_name)
    exit_code = 0 if not result.errors else 1
    print(f"EXIT_CODE={exit_code}")
    return exit_code


def smoke_interactive_claude_command(
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> int:
    """Run a token-consuming manual parity smoke test for interactive Claude."""
    return smoke_harness_agent_command(
        _INTERACTIVE_AGENT,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )


def smoke_interactive_agy_command(
    agent_name: str = "agy/Claude Sonnet 4.6 (Thinking)",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
) -> int:
    """Run the manual AGY end-to-end smoke harness via the PTY contract.

    This drives the live ``agy`` binary. The default alias is the model that
    currently works in this environment (``agy/Claude Sonnet 4.6 (Thinking)``);
    use ``--agent`` to pin a different ``agy/<model>`` alias from ``agy models``.
    """
    if shutil.which("agy") is None:
        logger.error(
            "agy binary not found in PATH. Install Google Anti Gravity and "
            "ensure `agy` is on PATH before running this smoke test."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with an agy/<model> alias, "
            "e.g. --agent 'agy/Claude Sonnet 4.6 (Thinking)'.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.AGY:
        logger.error(
            "Agent '{}' resolves to transport '{}', not AGY. "
            "Use --agent with an agy/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )
