"""CLI surface for manual, token-consuming agent-runtime smoke checks."""

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

from ralph.agents.invoke import (
    _parent_broker_secret,
)
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

# ``SmokeRunParams`` is re-exported so existing tests can still reach it here.
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pro_support.hooks import ProPipelineHooks


_INTERACTIVE_AGENT = "claude/haiku"
_HEADLESS_CLAUDE_AGENT = "claude-headless/haiku"
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
    """Append observed ordered subagent signals for an opted-in smoke run."""
    if not result.subagents_requested:
        return
    signals = (
        (result.subagent_dispatch_seen, "- subagent dispatch observed"),
        (result.subagent_result_seen, "- subagent result observed"),
        (result.post_subagent_activity_seen, "- post-subagent activity observed"),
    )
    working.extend(label for observed, label in signals if observed)


def _subagent_status(result: SmokeRunResult) -> str:
    """Return the table status for the complete opted-in subagent contract.

    S-4: relaxed from "exactly one dispatch" to "at least one dispatch, each
    with a correlated result" -- a real multi-subagent run (e.g. AGY
    dispatching two subagents in one frame) previously reported ``no`` here
    even though every dispatch had its own result and post-activity followed.
    ``subagent_result_seen`` already carries the "every dispatch correlated"
    signal (see ``_subagent_smoke_evidence``), so this only needs >= 1.
    """
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
    """Return the contract facts that back the run's overall verdict (F1).

    The verdict is a pure function of the weakest provenance among
    these required facts — not of ``file_created``, the transport
    ceiling, or any other diagnostic-only signal.

    The three canonical facts (``artifact_submitted``,
    ``explicit_completion_seen``, ``tool_activity_seen``) are always
    returned so the conformance matrix's column set is unchanged. When
    the run requested the multimodal scenario a fourth fact,
    ``multimodal_tool_used``, is added; it does NOT change the column
    set for non-multimodal runs and never silently downgrades a
    multimodal run to a passing verification: a missing fact holds at
    ``ABSENT`` so the run still reports a non-PASS verdict.
    """
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
    """Return the compact table-cell rendering of one graded fact."""
    if not evidence.holds:
        return "no"
    return f"yes ({evidence.provenance.name.lower()})"


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
            working.append(_evidence_line("smoke_test_result artifact submitted", result.artifact_submitted))
        _append_subagent_working_lines(result, working)
        lines.extend(working or ["- none"])
        lines.append("Observed output:")
        lines.extend([f"- {line}" for line in result.meaningful_output_lines] or ["- none"])
        lines.append("Observed breaks:")
        break_lines = [f"- {error}" for error in result.errors]
        if not break_lines and verdict != "PASS":
            below_wire = sum(
                1 for ev in evidence.values() if not ev.holds or ev.provenance is not ev.provenance.WIRE
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
    """Mint a run-scoped ``RALPH_BROKER_SECRET`` when the operator exported none.

    The smoke CLI runs as a fresh ``python -m ralph smoke-interactive-*``
    process. When the operator's environment carries no broker secret,
    ``build_session_bridge`` reads ``None`` and every wire-ledger append is
    a documented no-op -- so a live run that genuinely dialled Ralph's MCP
    tools (the measured 2026-08-17 Kimi smoke: file created, artifact
    submitted, completion declared) still graded every required fact below
    WIRE and exited 1. Minting a random, process-local secret here lets the
    run's receipts, sentinel, and ledger rows HMAC-bind to this run so the
    shared gate can grade them WIRE. The anti-forgery boundary is
    preserved: ``_subprocess_env`` strips the variable from the agent's
    environment, so the secret never reaches the model. An
    operator-exported value is always honored unchanged.

    Reads the secret through the sanctioned composition-root accessor
    ``_parent_broker_secret`` (the ``audit_di_seam`` allowlist entry for
    ``agents/invoke/_process_reader.py``) so the drift gate's
    ``RALPH_*`` env-var boundary keeps exactly one canonical reader.
    """
    if not _parent_broker_secret():
        os.environ["RALPH_BROKER_SECRET"] = secrets.token_hex(32)


def _resolve_smoke_agent_name(
    agent_name: str | None,
    transport: AgentTransport,
    config: UnifiedConfig,
    registry: AgentRegistry,
) -> str:
    """Return the explicit ``--agent`` value, or the operator's configured default.

    An explicit ``--agent`` always wins. When the operator passed none, the
    default comes from their OWN ``[agent_chains]`` -- the same aliases the
    live pipeline runs -- rather than a hardcoded provider/model literal that
    the pipeline never runs and that goes stale when the provider retires it.
    See :mod:`ralph.cli.commands.smoke_agent_defaults`.
    """
    if agent_name is not None:
        return agent_name
    return resolve_default_smoke_agent(transport, config, registry.get)


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
    """Run the interactive smoke harness and report parity.

    A missing multimodal fact is a hard break, not a downgrade to the basic scenario.
    """
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

    # THE one place any RALPH_*_BINARY override is applied. Every transport in
    # the override table is handled identically here, so a transport can never
    # be silently omitted (a per-command application was dead code: the
    # harness re-resolves everything from ``agent_name`` and discarded it).
    overridden_config = apply_smoke_binary_override(agent_config)
    if overridden_config is not agent_config:
        agent_config = overridden_config
        config = apply_smoke_binary_overrides_to_config(config)
        # A dynamic ``<transport>/<model>`` alias resolves from the built-ins,
        # not from ``config.agents``, and ``run_smoke_plumbing`` re-resolves
        # the alias against the config it is handed. Injecting the overridden
        # config under the exact alias is what makes the override reach the
        # spawned process for EVERY transport, not just AGY.
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})
        if agent_config.transport is AgentTransport.AGY and is_mock_agy_override():
            # The bundled mock writes artifacts under the workspace; real
            # wrappers manage their own cwd.
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
    # Replace only this transport's row in the durable conformance matrix.
    record_conformance_matrix(
        workspace_root, transport=result.transport, evidence=_required_evidence(result)
    )
    verdict_label, _ = grade_verdict(_required_evidence(result))
    # All errors, including incomplete subagent contracts, fail the run.
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    # Preserve the unadorned machine-readable EXIT_CODE=N contract.
    _smoke_emit_exit_code_line(exit_code)
    return exit_code


def _smoke_emit_exit_code_line(exit_code: int) -> None:
    """Emit the literal machine-readable ``EXIT_CODE=N`` contract line."""
    with contextlib.suppress(Exception):
        sys.stdout.write(f"EXIT_CODE={exit_code}\n")
        sys.stdout.flush()


def smoke_interactive_claude_command(
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    """Run a token-consuming manual parity smoke test for interactive Claude.

    ``multimodal=True`` drives the run from the multimodal-aware prompt
    (criterion 5). See
    :func:`smoke_harness_agent_command` for the wider contract.
    """
    return smoke_harness_agent_command(
        _INTERACTIVE_AGENT,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )


def smoke_headless_claude_command(
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    """Run a token-consuming manual parity smoke test for headless Claude.

    Thin pass-through to ``smoke_harness_agent_command`` with the
    default headless-Claude alias ``claude-headless/haiku``. The
    command exposes the same ``--subagents`` / ``--subagent-prompt-file``
    options as ``smoke_interactive_claude`` so the two transports
    share one scenario surface. The headless transport does NOT
    emit a visible TUI; the harness detects the subagent dispatch
    via the parsed tool metadata (see
    ``ralph.pipeline.plumbing.smoke_evidence.SmokeRunResult``).

    The command is OUTSIDE ``make verify`` per the headless smoke
    exclusion (the harness only runs when an operator explicitly
    invokes it).
    """
    return smoke_harness_agent_command(
        _HEADLESS_CLAUDE_AGENT,
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
    """Run the manual AGY end-to-end smoke harness via the PTY contract.

    This drives the live ``agy`` binary (or the ``RALPH_AGY_BINARY`` override
    when set). With no ``--agent``, the alias comes from the operator's own
    ``[agent_chains]`` (the first AGY entry the pipeline would run), falling
    back to bare ``agy`` -- which passes no ``--model``, so AGY uses the model
    the operator configured. Use ``--agent`` to pin a published
    ``agy/<model>`` alias.
    """
    agy_binary = smoke_transport_binary(AgentTransport.AGY, "agy")
    # filesystem-read-ok: external binary existence check via PATH
    # override (RALPH_AGY_BINARY); operator-controlled system path,
    # not project-filesystem state.
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
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.AGY, config, registry)
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
    """Run the manual smoke harness for the Codex CLI.

    Bare ``codex`` passes no ``--model``, so Codex uses whatever model
    the operator configured -- a default that cannot go stale as OpenAI
    retires model ids. To pin one, pass the dynamic ``codex/<model>``
    alias: the command builder strips the leading ``codex/`` and passes
    ``<model>`` to ``codex exec``. This
    command lives for parity with the other interactive smoke
    commands; the production harness uses the same
    ``smoke_harness_agent_command`` plumbing with a ``cmd_override``
    redirect seam (set the operator-config ``[agents.<name>].cmd``
    to the multimodal stub path before invoking).

    Like every other ``smoke-interactive-*`` command this consumes
    live tokens and is therefore OUTSIDE ``make verify``; it only
    runs when an operator invokes it.
    """
    if shutil.which("codex") is None:
        logger.error(
            "codex binary not found. Install Codex CLI and ensure `codex` is on PATH."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.CODEX, config, registry)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with 'codex' or a "
            "codex/<model> alias.",
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
    """Run the manual smoke harness for the Pi coding agent.

    The alias follows the dynamic ``pi/<model>`` pattern (e.g.
    ``pi/<model>``); bare ``pi`` uses the base pi harness layout.
    The production harness uses the same
    ``smoke_harness_agent_command`` plumbing with a ``cmd_override``
    redirect seam.

    Like every other ``smoke-interactive-*`` command this consumes
    live tokens and is therefore OUTSIDE ``make verify``; it only
    runs when an operator invokes it.
    """
    if shutil.which("pi") is None:
        logger.error(
            "pi binary not found. Install Pi and ensure `pi` is on PATH."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.PI, config, registry)
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
    """Run the manual PTY smoke test for a Nanocoder interactive alias."""
    if shutil.which("nanocoder") is None:
        logger.error(
            "nanocoder binary not found. Install Nanocoder and ensure `nanocoder` is on PATH."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_name = _resolve_smoke_agent_name(
        agent_name, AgentTransport.NANOCODER, config, registry
    )
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
    """Run the manual end-to-end smoke harness via the Cursor headless contract.

    This drives the live ``agent`` binary (or the ``RALPH_CURSOR_BINARY``
    override when set).  With no ``--agent``, the alias comes from the
    operator's own ``[agent_chains]`` (the first Cursor entry the
    pipeline would run), falling back to bare ``cursor`` -- which passes
    no ``--model``, so Cursor uses its documented Auto routing.  The
    command is OUTSIDE ``make verify`` per the cursor non-goal of no
    live-token-consuming smoke tests in verify (the harness only runs
    when an operator explicitly invokes it).
    """
    cursor_binary = smoke_transport_binary(AgentTransport.CURSOR, "agent")
    # filesystem-read-ok: external binary existence check via PATH
    # override (RALPH_CURSOR_BINARY); operator-controlled system path,
    # not project-filesystem state.
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
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.CURSOR, config, registry)
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
    """Run the manual end-to-end smoke harness via the Kimi headless contract.

    This drives the live ``kimi`` binary (or the ``RALPH_KIMI_BINARY``
    override when set).  With no ``--agent``, the alias comes from the
    operator's own ``[agent_chains]`` (the first Kimi entry the pipeline
    would run), falling back to bare ``kimi`` -- which passes no ``-m``,
    so Kimi Code uses the model configured in
    ``~/.kimi-code/config.toml``.  To pin one, pass the full configured
    id (the CLI rejects a bare ``-m kimi-for-coding`` with "Model ... is
    not configured in config.toml"), e.g.
    ``--agent kimi/kimi-code/kimi-for-coding``.  The command is OUTSIDE
    ``make verify`` -- it consumes live tokens and only runs when an
    operator explicitly invokes it.
    """
    kimi_binary = smoke_transport_binary(AgentTransport.KIMI, "kimi")
    # filesystem-read-ok: external binary existence check via PATH
    # override (RALPH_KIMI_BINARY); operator-controlled system path,
    # not project-filesystem state.
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
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.KIMI, config, registry)
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
    """Run the manual smoke harness against a live ``opencode`` provider/model.

    With no ``--agent``, the alias comes from the operator's own
    ``[agent_chains]`` (the first OpenCode entry the pipeline would run),
    falling back to bare ``opencode`` -- which passes no ``--model``, so
    OpenCode uses the operator's configured default. To pin one, the alias
    carries BOTH the provider and the model (``opencode/<provider>/<model>``),
    so a single ``--agent`` value selects the full routing target -- e.g.
    ``--agent 'opencode/minimax/MiniMax-M3'`` or
    ``--agent 'opencode/kimi-for-coding/k2p5'``. Run ``opencode models`` to
    list the provider/model pairs your credentials can reach. The command
    builder strips the leading ``opencode/`` and passes ``<provider>/<model>``
    to ``opencode run --model``.

    Pair with ``--subagents`` to drive the native subagent (``task``) tool and
    assert the full dispatch -> result -> post-subagent-activity lifecycle is
    visible to the parser. That lifecycle is what feeds the idle watchdog's
    ``subagent_output`` channel; a parser that cannot see subagent progress
    leaves the watchdog blind while a subagent works.

    Like the other smoke commands this consumes live tokens and is therefore
    OUTSIDE ``make verify``; it only runs when an operator invokes it.
    """
    opencode_binary = smoke_transport_binary(AgentTransport.OPENCODE, "opencode")
    # filesystem-read-ok: external binary existence check via PATH
    # override (RALPH_OPENCODE_BINARY); operator-controlled system path,
    # not project-filesystem state.
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
    agent_name = _resolve_smoke_agent_name(agent_name, AgentTransport.OPENCODE, config, registry)
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
