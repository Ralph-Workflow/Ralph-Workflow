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

import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import click
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
    _agy_binary_override_env,
    _build_smoke_prompt,
    _cursor_binary_override_env,
    _execute_smoke_turns,
    is_mock_agy_override,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.prompts.materialize import submit_artifact_tool_name_for_transport
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pro_support.hooks import ProPipelineHooks

# Re-export plumbing symbols so existing tests can still reach them.
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams


def get_agy_binary_override() -> str:
    """Return the AGY binary path, honoring ``RALPH_AGY_BINARY``."""
    return _agy_binary_override_env() or "agy"


def get_cursor_binary_override() -> str:
    """Return the Cursor binary path, honoring ``RALPH_CURSOR_BINARY``."""
    return _cursor_binary_override_env() or "agent"


def _resolve_agy_binary_override() -> str | None:
    """Return the validated absolute ``RALPH_AGY_BINARY`` override or ``None``.

    A relative override is resolved against the current working directory so
    downstream :class:`subprocess.Popen` always sees an absolute path. The
    cwd the harness later spawns the AGY binary from is not always the
    directory the operator set ``RALPH_AGY_BINARY`` in (the smoke CLI may
    change cwd before spawning the agent), so a relative override would
    fail with ``FileNotFoundError`` at spawn time. The relative path is
    logged so the operator can see the resolution.

    The path must resolve to a regular file with executable bits set, or
    to a name ``shutil.which`` can locate on ``PATH``. When validation
    fails a WARNING is logged and ``None`` is returned so the caller
    falls back to the real ``agy`` binary on ``PATH``.
    """
    override = _agy_binary_override_env()
    if not override:
        return None
    resolved = Path(override).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
        logger.info(
            "Resolved relative RALPH_AGY_BINARY '{}' to absolute path '{}'",
            override,
            resolved,
        )
    if shutil.which(str(resolved)) is None and not (
        resolved.is_file() and os.access(resolved, os.X_OK)
    ):
        logger.warning(
            "RALPH_AGY_BINARY points to '{}', which is not executable; ignoring override",
            override,
        )
        return None
    return str(resolved)


def _resolve_cursor_binary_override() -> str | None:
    """Return the validated absolute ``RALPH_CURSOR_BINARY`` override or ``None``.

    Mirrors :func:`_resolve_agy_binary_override` for the Cursor
    transport.  A relative override is resolved against the current
    working directory so downstream :class:`subprocess.Popen` always
    sees an absolute path.  The path must resolve to a regular file
    with executable bits set, or to a name ``shutil.which`` can locate
    on ``PATH``.  When validation fails a WARNING is logged and
    ``None`` is returned so the caller falls back to the real
    ``agent`` binary on ``PATH``.

    Unlike AGY there is no bundled mock binary for Cursor, so any
    non-empty override points at a real wrapper, alternate live
    binary, or an operator-wired test stub.  The override is
    applied to the ``cmd`` field verbatim (no shlex-quoting
    shenanigans) so a wrapper script that takes its own flags
    preserves those flags.
    """
    override = _cursor_binary_override_env()
    if not override:
        return None
    resolved = Path(override).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
        logger.info(
            "Resolved relative RALPH_CURSOR_BINARY '{}' to absolute path '{}'",
            override,
            resolved,
        )
    if shutil.which(str(resolved)) is None and not (
        resolved.is_file() and os.access(resolved, os.X_OK)
    ):
        logger.warning(
            "RALPH_CURSOR_BINARY points to '{}', which is not executable; ignoring override",
            override,
        )
        return None
    return str(resolved)


def _maybe_apply_agy_binary_override(agent_config: AgentConfig) -> AgentConfig:
    """Return a copy of ``agent_config`` that uses ``RALPH_AGY_BINARY`` when set.

    Validates the override path (resolving relative paths to absolute) and
    leaves ``agent_config`` unchanged when the path is not executable or
    not a regular file, logging a WARNING in that case. The log message
    distinguishes the deterministic mock binary from a real wrapper or
    alternate live binary path: a mock override logs
    ``mock AGY binary in use``; any other executable override logs
    ``Using RALPH_AGY_BINARY override`` so a genuine live wrapper is not
    misreported as a mock run.
    """
    if agent_config.transport is not AgentTransport.AGY:
        return agent_config
    resolved = _resolve_agy_binary_override()
    if resolved is None:
        return agent_config
    if is_mock_agy_override():
        logger.info("mock AGY binary in use: {}", resolved)
    else:
        logger.info("Using RALPH_AGY_BINARY override: {}", resolved)
    # Quote paths that contain spaces so downstream shlex.split keeps the
    # binary path as a single argv token.
    return agent_config.model_copy(update={"cmd": shlex.quote(resolved)})


def _maybe_apply_cursor_binary_override(agent_config: AgentConfig) -> AgentConfig:
    """Return a copy of ``agent_config`` that uses ``RALPH_CURSOR_BINARY`` when set.

    Validates the override path (resolving relative paths to absolute)
    and leaves ``agent_config`` unchanged when the path is not
    executable or not a regular file, logging a WARNING in that case.

    Unlike the AGY override, the cursor override is shlex.quote()d
    when applied to the cmd field so the wrapper path is preserved
    as a single argv token by downstream shlex.split.  Operators
    that need extra flags on the wrapper can set
    ``[agents.cursor].cmd`` in their config (the CursorCommandBuilder
    honors that override via shlex.split on the config.cmd).
    """
    if agent_config.transport is not AgentTransport.CURSOR:
        return agent_config
    resolved = _resolve_cursor_binary_override()
    if resolved is None:
        return agent_config
    logger.info("Using RALPH_CURSOR_BINARY override: {}", resolved)
    return agent_config.model_copy(update={"cmd": shlex.quote(resolved)})


def _apply_agy_binary_override_to_config(config: UnifiedConfig) -> UnifiedConfig:
    """Return a config copy with AGY agents using ``RALPH_AGY_BINARY`` when set."""
    resolved = _resolve_agy_binary_override()
    if resolved is None:
        return config
    # Quote paths that contain spaces so downstream shlex.split keeps the
    # binary path as a single argv token.
    quoted = shlex.quote(resolved)
    new_agents: dict[str, AgentConfig] = {}
    for name, agent_config in config.agents.items():
        if agent_config.transport is AgentTransport.AGY:
            new_agents[name] = agent_config.model_copy(update={"cmd": quoted})
        else:
            new_agents[name] = agent_config
    return config.model_copy(update={"agents": new_agents})


def _apply_cursor_binary_override_to_config(config: UnifiedConfig) -> UnifiedConfig:
    """Return a config copy with Cursor agents using ``RALPH_CURSOR_BINARY`` when set."""
    resolved = _resolve_cursor_binary_override()
    if resolved is None:
        return config
    quoted = shlex.quote(resolved)
    new_agents: dict[str, AgentConfig] = {}
    for name, agent_config in config.agents.items():
        if agent_config.transport is AgentTransport.CURSOR:
            new_agents[name] = agent_config.model_copy(update={"cmd": quoted})
        else:
            new_agents[name] = agent_config
    return config.model_copy(update={"agents": new_agents})


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
    "smoke_interactive_cursor_command",
    "smoke_interactive_nanocoder_command",
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
    """Return the table status for the complete opted-in subagent contract."""
    if not result.subagents_requested:
        return "not requested"
    complete = (
        result.subagent_dispatch_count == 1
        and result.subagent_dispatch_seen
        and result.subagent_result_seen
        and result.post_subagent_activity_seen
    )
    return "yes" if complete else "no"


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
            if result.transport == "agy":
                working.append("- canonical smoke_test_result receipt observed")
            else:
                working.append("- declare_complete marker observed")
        if result.parsed_event_count > 0:
            working.append(f"- parser emitted {result.parsed_event_count} event(s)")
        if result.tool_activity_seen:
            working.append("- tool activity observed")
        if result.artifact_submitted:
            working.append("- smoke_test_result artifact submitted")
        _append_subagent_working_lines(result, working)
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
    table.add_column("Subagent")
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
            _subagent_status(result),
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
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
) -> int:
    """Run the interactive smoke harness for ``agent_name`` and report parity."""
    workspace_scope = resolve_workspace_scope()
    workspace_root = workspace_scope.root
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

    agy_override = _agy_binary_override_env()
    if agy_override and agent_config.transport is AgentTransport.AGY:
        if is_mock_agy_override():
            logger.info(
                "Using mock AGY binary at '{}' (RALPH_AGY_BINARY)",
                agy_override,
            )
            # The mock binary writes its output files under
            # MOCK_AGY_ARTIFACT_DIR. The harness expects those files under
            # ``workspace_root``, so default the env var to the workspace
            # root when the operator has not set it explicitly. The mock
            # honors an explicit override unchanged. A real wrapper or
            # alternate live binary path does NOT need this default
            # (it manages its own working directory), so we skip the
            # setdefault for non-mock overrides to avoid polluting
            # MOCK_AGY_ARTIFACT_DIR for unrelated tools.
            os.environ.setdefault("MOCK_AGY_ARTIFACT_DIR", str(workspace_root))
        else:
            logger.info(
                "Using RALPH_AGY_BINARY override for AGY transport: '{}'",
                agy_override,
            )
    cursor_override = _cursor_binary_override_env()
    if cursor_override and agent_config.transport is AgentTransport.CURSOR:
        logger.info(
            "Using RALPH_CURSOR_BINARY override for Cursor transport: '{}'",
            cursor_override,
        )
    agent_config = _maybe_apply_agy_binary_override(agent_config)
    agent_config = _maybe_apply_cursor_binary_override(agent_config)
    config = _apply_agy_binary_override_to_config(config)
    config = _apply_cursor_binary_override_to_config(config)
    # Dynamic agy/<model> aliases are resolved from builtins, not from
    # config.agents, so inject the overridden config under the exact
    # agent name to ensure RALPH_AGY_BINARY is honored.
    if agy_override and agent_config.transport is AgentTransport.AGY:
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})

    submit_artifact_tool_name = submit_artifact_tool_name_for_transport(agent_config.transport)
    prompt_file.write_text(
        _build_smoke_prompt(
            spec.output_file.as_posix(),
            submit_artifact_tool_name=submit_artifact_tool_name,
            transport=agent_config.transport,
            subagents=subagents,
            subagent_prompt=subagent_prompt,
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
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
) -> int:
    """Run a token-consuming manual parity smoke test for interactive Claude."""
    return smoke_harness_agent_command(
        _INTERACTIVE_AGENT,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
    )


def smoke_interactive_agy_command(
    agent_name: str = "agy/Gemini 3.5 Flash (Medium)",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
) -> int:
    """Run the manual AGY end-to-end smoke harness via the PTY contract.

    This drives the live ``agy`` binary (or the ``RALPH_AGY_BINARY`` override
    when set). The default alias is ``agy/Gemini 3.5 Flash (Medium)`` because
    that model ships with a generous per-account quota in the
    ``agy models`` list and reliably produces output in the harness
    environment. The 7 live regression tests in
    ``tests/test_agy_live_regression.py`` all use the same default alias so
    the public CLI command and the live regression suite share one
    repo-consistent, directly verified smoke path. Use ``--agent`` to pin a
    different ``agy/<model>`` alias from ``agy models``.
    """
    agy_binary = get_agy_binary_override()
    if shutil.which(agy_binary) is None and not (
        Path(agy_binary).is_file() and os.access(agy_binary, os.X_OK)
    ):
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
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with an agy/<model> alias, "
            "e.g. --agent 'agy/Gemini 3.5 Flash (Medium)'.",
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
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
    )


def smoke_interactive_nanocoder_command(
    agent_name: str = "nanocoder",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
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
    )


def smoke_interactive_cursor_command(
    agent_name: str = "cursor/auto",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
) -> int:
    """Run the manual end-to-end smoke harness via the Cursor headless contract.

    This drives the live ``agent`` binary (or the ``RALPH_CURSOR_BINARY``
    override when set).  The default alias is ``cursor/auto`` because
    that exercises the dynamic-alias path and the documented Auto
    default-routing model.  The command is OUTSIDE ``make verify`` per
    the cursor non-goal of no live-token-consuming smoke tests in
    verify (the harness only runs when an operator explicitly invokes
    it).
    """
    cursor_binary = get_cursor_binary_override()
    if shutil.which(cursor_binary) is None and not (
        Path(cursor_binary).is_file() and os.access(cursor_binary, os.X_OK)
    ):
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
    )


def smoke_interactive_opencode_command(
    agent_name: str = "opencode/minimax-coding-plan/MiniMax-M3",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
) -> int:
    """Run the manual smoke harness against a live ``opencode`` provider/model.

    The alias carries BOTH the provider and the model
    (``opencode/<provider>/<model>``), so one ``--agent`` value selects the
    full routing target -- e.g.
    ``--agent 'opencode/minimax-coding-plan/MiniMax-M3'`` or
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
    if shutil.which("opencode") is None:
        logger.error(
            "opencode binary not found. Install OpenCode and ensure `opencode` is on PATH."
        )
        return 2

    workspace_scope = resolve_workspace_scope()
    config: UnifiedConfig = load_config(None, {}, workspace_scope=workspace_scope)
    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with an "
            "opencode/<provider>/<model> alias, e.g. "
            "--agent 'opencode/minimax-coding-plan/MiniMax-M3'. "
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
    )
