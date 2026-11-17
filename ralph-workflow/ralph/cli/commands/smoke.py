from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import sys
from typing import TYPE_CHECKING

import click
from loguru import logger
from rich.table import Table

from ralph.agents.invoke import parent_broker_secret
from ralph.agents.registry import AgentRegistry, agy_alias_help
from ralph.cli.commands._smoke_ccs import smoke_interactive_ccs_command
from ralph.cli.commands.smoke_agent_defaults import resolve_default_smoke_agent
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    apply_smoke_binary_overrides_to_config,
    smoke_transport_binary,
)
from ralph.config.enums import AgentTransport
from ralph.config.loader import load_config
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.server._process_secrecy import protect_broker_secret
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_evidence import (
    PASS,
    Evidence,
    Provenance,
    absent,
    format_verdict,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_multimodal import SMOKE_FIXTURE_RELNAME
from ralph.pipeline.plumbing.smoke_plumbing import (
    _SMOKE_IDLE_TIMEOUT_SECONDS,
    _SMOKE_MAX_SESSION_SECONDS,
    _SMOKE_MAX_TURNS,
    SmokeRunResult,
    _build_smoke_prompt,
    _execute_smoke_turns,
    is_mock_agy_override,
    record_conformance_matrix,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.policy.models import AgentsPolicy
    from ralph.pro_support.hooks import ProPipelineHooks


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
    "smoke_headless_claude_command",
    "smoke_interactive_agy_command",
    "smoke_interactive_ccs_command",
    "smoke_interactive_claude_command",
    "smoke_interactive_codex_command",
    "smoke_interactive_cursor_command",
    "smoke_interactive_kimi_command",
    "smoke_interactive_nanocoder_command",
    "smoke_interactive_pi_command",
]


build_smoke_prompt = _build_smoke_prompt


def _append_subagent_working_lines(result: SmokeRunResult, working: list[str]) -> None:
    if not result.subagents_requested:
        return
    signals = (
        (result.subagent_dispatch_seen, "- subagent dispatch observed"),
        (result.subagent_result_seen, "- subagent result observed"),
        (result.post_subagent_activity_seen, "- post-subagent activity observed"),
    )
    working.extend(label for observed, label in signals if observed)


def _subagent_status(result: SmokeRunResult) -> str:
    if not result.subagents_requested:
        return "not requested"
    complete = (
        result.subagent_dispatch_count >= 1
        and result.subagent_dispatch_seen
        and result.subagent_result_seen
        and result.post_subagent_activity_seen
    )
    return "yes" if complete else "no"


def _required_evidence(result: SmokeRunResult) -> dict[str, Evidence]:
    facts: dict[str, Evidence] = {
        "artifact_submitted": result.artifact_submitted,
        "explicit_completion_seen": result.explicit_completion_seen,
        "tool_activity_seen": result.tool_activity_seen,
    }
    if result.multimodal_requested:
        facts["multimodal_tool_used"] = (
            result.multimodal_tool_used
            if result.multimodal_tool_used is not None
            else absent("multimodal_tool_used was never graded for this run")
        )
    return facts


def _evidence_line(label: str, evidence: Evidence) -> str:
    return f"- {label} observed [{evidence.provenance.name}] ({evidence.detail})"


def _evidence_cell(evidence: Evidence) -> str:
    if not evidence.holds:
        return "no"
    return f"yes ({evidence.provenance.name.lower()})"


def _render_smoke_report(
    results: list[SmokeRunResult],
    *,
    agent_name: str = "claude",
) -> str:
    lines = [
        f"{agent_name} parity smoke report",
        "",
        f"Headless semantic guide: {_HEADLESS_SEMANTIC_GUIDE}",
        "",
    ]
    for result in results:
        evidence = _required_evidence(result)
        verdict = format_verdict(evidence)
        lines.append(f"Agent: {result.agent_name} ({result.transport})")
        lines.append(f"Verdict: {verdict}")
        lines.append(
            f"Ralph tools advertised: {result.transport_evidence_ceiling.name} "
            "(evidence ceiling inferred from the init frame's tool listing)"
        )
        if result.multimodal_requested:
            mult_label = (
                "yes (wire)"
                if result.multimodal_tool_used is not None
                and result.multimodal_tool_used.holds
                and result.multimodal_tool_used.provenance is Provenance.WIRE
                else "no"
            )
            lines.append(f"Multimodal: {mult_label}")
        lines.append("Observed working:")
        working: list[str] = []
        if result.file_created:
            working.append(f"- created {result.output_file}")
        if result.session_id is not None:
            working.append(f"- session ID observed: {result.session_id}")
        if result.explicit_completion_seen.holds:
            working.append(_evidence_line("completion sentinel", result.explicit_completion_seen))
        if result.parsed_event_count > 0:
            working.append(f"- parser emitted {result.parsed_event_count} event(s)")
        if result.tool_activity_seen.holds:
            working.append(_evidence_line("tool activity", result.tool_activity_seen))
        if result.artifact_submitted.holds:
            working.append(
                _evidence_line("smoke_test_result artifact submitted", result.artifact_submitted)
            )
        _append_subagent_working_lines(result, working)
        lines.extend(working or ["- none"])
        lines.append("Observed output:")
        lines.extend([f"- {line}" for line in result.meaningful_output_lines] or ["- none"])
        lines.append("Observed breaks:")
        break_lines = [f"- {error}" for error in result.errors]
        if not break_lines and verdict != "PASS":
            below_wire = sum(
                1
                for ev in evidence.values()
                if not ev.holds or ev.provenance is not ev.provenance.WIRE
            )
            break_lines = [
                f"- {verdict}: {below_wire} of {len(evidence)} required fact(s) graded below WIRE; "
                "no functional break was detected, but this run does not prove the transport reached "
                "Ralph's MCP tools"
            ]
        lines.extend(break_lines or ["- No breaks observed"])
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
    table.add_column("Subagent")
    table.add_column("Multimodal")
    table.add_column("Artifact")
    table.add_column("Verdict")
    table.add_column("Breaks")

    for result in results:
        evidence = _required_evidence(result)
        verdict_label, _ = grade_verdict(evidence)
        if result.errors:
            breaks_cell = "; ".join(result.errors)
        elif verdict_label != PASS:
            breaks_cell = f"degraded verdict: {format_verdict(evidence)}"
        else:
            breaks_cell = "none"
        if result.multimodal_requested:
            mult_cell = _evidence_cell(
                result.multimodal_tool_used
                if result.multimodal_tool_used is not None
                else absent("multimodal_tool_used was never graded for this run")
            )
        else:
            mult_cell = "not requested"
        table.add_row(
            result.agent_name,
            result.transport,
            "yes" if result.file_created else "no",
            result.session_id or "missing",
            str(result.parsed_event_count),
            _evidence_cell(result.tool_activity_seen),
            _subagent_status(result),
            mult_cell,
            _evidence_cell(result.artifact_submitted),
            format_verdict(evidence),
            breaks_cell,
        )
    display.emit_renderable(table)
    display.emit_info_panel(
        title="Detailed report",
        content=_render_smoke_report(results, agent_name=agent_name),
    )


render_smoke_report = _render_smoke_report


def _ensure_smoke_broker_secret() -> None:
    if not parent_broker_secret():
        os.environ["RALPH_BROKER_SECRET"] = secrets.token_hex(32)
    protect_broker_secret()


def _resolve_smoke_agent_name(
    agent_name: str | None,
    transport: AgentTransport,
    agents_policy: AgentsPolicy,
    registry: AgentRegistry,
) -> str | None:
    if agent_name is not None:
        return agent_name
    resolved_agent = resolve_default_smoke_agent(
        transport,
        agents_policy,
        registry.get,
        drain="development",
    )
    if resolved_agent is None:
        logger.error(
            "The effective agents policy development drain has no agent for transport '{}'. "
            "Use --agent to select a compatible agent.",
            transport.value,
        )
        return None
    return resolved_agent


def smoke_harness_agent_command(
    agent_name: str,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    workspace_scope = resolve_workspace_scope()
    workspace_root = workspace_scope.root
    _ensure_smoke_broker_secret()
    if subagent_prompt_file is not None and not subagents:
        raise click.UsageError("--subagent-prompt-file requires --subagents")
    subagent_prompt: str | None = None
    if subagent_prompt_file is not None:
        resolved_prompt_file = subagent_prompt_file.resolve()
        if not resolved_prompt_file.is_relative_to(workspace_root.resolve()):
            raise click.UsageError("--subagent-prompt-file must be inside the workspace")
        try:
            subagent_prompt = resolved_prompt_file.read_text(encoding="utf-8").strip()
        except UnicodeError as exc:
            raise click.UsageError(
                f"subagent prompt file '{subagent_prompt_file}' must contain valid UTF-8"
            ) from exc
        except OSError as exc:
            raise click.UsageError(
                f"unable to read subagent prompt file '{subagent_prompt_file}': {exc}"
            ) from exc
        if not subagent_prompt:
            raise click.UsageError("--subagent-prompt-file must not be empty")
    ctx = display_context if display_context is not None else make_display_context()
    spec = resolve_smoke_harness_spec(agent_name)
    smoke_dir = workspace_root / spec.relative_dir
    smoke_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = workspace_root / spec.relative_dir / "PROMPT.md"
    output_file = workspace_root / spec.output_file

    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise RuntimeError(f"Smoke test agent '{agent_name}' is unavailable in the registry")

    overridden_config = apply_smoke_binary_override(agent_config)
    if overridden_config is not agent_config:
        agent_config = overridden_config
        config = apply_smoke_binary_overrides_to_config(config)
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})
        if agent_config.transport is AgentTransport.AGY and is_mock_agy_override():
            os.environ.setdefault("MOCK_AGY_ARTIFACT_DIR", str(workspace_root))

    submit_artifact_tool_name = submit_artifact_tool_name_for_transport(agent_config.transport)
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        prompt_file,
        _build_smoke_prompt(
            spec.output_file.as_posix(),
            submit_artifact_tool_name=submit_artifact_tool_name,
            transport=agent_config.transport,
            subagents=subagents,
            subagent_prompt=subagent_prompt,
            multimodal=multimodal,
            multimodal_fixture_relpath=SMOKE_FIXTURE_RELNAME if multimodal else None,
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
        subagents=subagents,
        multimodal=multimodal,
    )

    _render_smoke_table([result], display_context=ctx, agent_name=agent_name)
    record_conformance_matrix(
        workspace_root, transport=result.transport, evidence=_required_evidence(result)
    )
    verdict_label, _ = grade_verdict(_required_evidence(result))
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    _smoke_emit_exit_code_line(exit_code)
    return exit_code


def _smoke_emit_exit_code_line(exit_code: int) -> None:
    with contextlib.suppress(Exception):
        sys.stdout.write(f"EXIT_CODE={exit_code}\n")
        sys.stdout.flush()


def _resolve_claude_smoke_agent(
    agent_name: str | None,
    *,
    transport: AgentTransport,
) -> str | None:
    if agent_name is not None:
        return agent_name
    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    return _resolve_smoke_agent_name(None, transport, agents_policy, registry)


def smoke_interactive_claude_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    resolved_agent = _resolve_claude_smoke_agent(
        agent_name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    if resolved_agent is None:
        return 2
    return smoke_harness_agent_command(
        resolved_agent,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_headless_claude_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    resolved_agent = _resolve_claude_smoke_agent(
        agent_name,
        transport=AgentTransport.CLAUDE,
    )
    if resolved_agent is None:
        return 2
    return smoke_harness_agent_command(
        resolved_agent,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_agy_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    agy_binary = smoke_transport_binary(AgentTransport.AGY, "agy")
    if shutil.which(agy_binary) is None and not os.access(agy_binary, os.X_OK):
        logger.error(
            "agy binary not found at '{}'. Install Google Anti Gravity and "
            "ensure `agy` is on PATH, or set RALPH_AGY_BINARY to a valid mock "
            "binary for testing.",
            agy_binary,
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.AGY, agents_policy, registry)
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. {}",
            agent_name,
            agy_alias_help(),
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
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_codex_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    if shutil.which("codex") is None:
        logger.error("codex binary not found. Install Codex CLI and ensure `codex` is on PATH.")
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(
        agent_name, AgentTransport.CODEX, agents_policy, registry
    )
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with 'codex' or a codex/<model> alias.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.CODEX:
        logger.error(
            "Agent '{}' resolves to transport '{}', not CODEX. "
            "Use --agent with 'codex' or a codex/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_pi_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    if shutil.which("pi") is None:
        logger.error("pi binary not found. Install Pi and ensure `pi` is on PATH.")
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.PI, agents_policy, registry)
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with a pi or pi/<model> alias.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.PI:
        logger.error(
            "Agent '{}' resolves to transport '{}', not PI. "
            "Use --agent with a pi or pi/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_nanocoder_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    if shutil.which("nanocoder") is None:
        logger.error(
            "nanocoder binary not found. Install Nanocoder and ensure `nanocoder` is on PATH."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(
        agent_name, AgentTransport.NANOCODER, agents_policy, registry
    )
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with nanocoder or a "
            "nanocoder/<provider>/<model> alias.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.NANOCODER:
        logger.error(
            "Agent '{}' resolves to transport '{}', not NANOCODER.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_cursor_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    cursor_binary = smoke_transport_binary(AgentTransport.CURSOR, "agent")
    if shutil.which(cursor_binary) is None and not os.access(cursor_binary, os.X_OK):
        logger.error(
            "cursor binary not found at '{}'. Install Cursor Agent and "
            "ensure `agent` is on PATH, or set RALPH_CURSOR_BINARY to a "
            "valid wrapper for testing.",
            cursor_binary,
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(
        agent_name, AgentTransport.CURSOR, agents_policy, registry
    )
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with a cursor/<model> alias, "
            "e.g. --agent 'cursor/auto'.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.CURSOR:
        logger.error(
            "Agent '{}' resolves to transport '{}', not CURSOR. "
            "Use --agent with a cursor/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_kimi_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    kimi_binary = smoke_transport_binary(AgentTransport.KIMI, "kimi")
    if shutil.which(kimi_binary) is None and not os.access(kimi_binary, os.X_OK):
        logger.error(
            "kimi binary not found at '{}'. Install Kimi Code and "
            "ensure `kimi` is on PATH, or set RALPH_KIMI_BINARY to a "
            "valid wrapper for testing.",
            kimi_binary,
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.KIMI, agents_policy, registry)
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with a kimi/<model> alias, "
            "e.g. --agent 'kimi/kimi-for-coding'.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.KIMI:
        logger.error(
            "Agent '{}' resolves to transport '{}', not KIMI. "
            "Use --agent with a kimi/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_interactive_opencode_command(
    agent_name: str | None = None,
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    opencode_binary = smoke_transport_binary(AgentTransport.OPENCODE, "opencode")
    if shutil.which(opencode_binary) is None and not os.access(opencode_binary, os.X_OK):
        logger.error(
            "opencode binary not found at '{}'. Install OpenCode and ensure "
            "`opencode` is on PATH, or set RALPH_OPENCODE_BINARY to a valid "
            "wrapper for testing.",
            opencode_binary,
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)
    agent_name = _resolve_smoke_agent_name(
        agent_name, AgentTransport.OPENCODE, agents_policy, registry
    )
    if agent_name is None:
        return 2
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with an "
            "opencode/<provider>/<model> alias, e.g. "
            "--agent 'opencode/minimax/MiniMax-M3'. "
            "Run `opencode models` to list reachable provider/model pairs.",
            agent_name,
        )
        return 2
    if agent_config.transport is None or agent_config.transport != AgentTransport.OPENCODE:
        logger.error(
            "Agent '{}' resolves to transport '{}', not OPENCODE. "
            "Use --agent with an opencode/<provider>/<model> alias.",
            agent_name,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )
