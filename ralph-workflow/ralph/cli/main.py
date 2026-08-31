"""Ralph Workflow CLI entry point - typer application with rich-click help styling.

This module provides the main CLI application for Ralph Workflow, using typer
for argument parsing and rich-click for enhanced help output.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path as RuntimePath
from typing import TYPE_CHECKING, Annotated, Protocol, cast

import rich_click as click
import typer
import typer.testing
from loguru import logger

from ralph._build_meta import build_provenance_line, flavored_version
from ralph.api.opencode import list_providers as fetch_providers
from ralph.cli._capability_summary import print_capability_summary
from ralph.cli._cli_override_input import CLIOverrideInput
from ralph.cli.commands.check_policy import check_policy_command
from ralph.cli.commands.cleanup import cleanup
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.contribute import contribute
from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.commands.explain import explain_command
from ralph.cli.commands.init import init_command
from ralph.cli.commands.run import RunPipelineRequest, run_pipeline
from ralph.cli.commands.smoke import (
    smoke_headless_claude_command,
    smoke_interactive_agy_command,
    smoke_interactive_ccs_command,
    smoke_interactive_claude_command,
    smoke_interactive_codex_command,
    smoke_interactive_cursor_command,
    smoke_interactive_kimi_command,
    smoke_interactive_nanocoder_command,
    smoke_interactive_opencode_command,
    smoke_interactive_pi_command,
)
from ralph.cli.commands.star import star
from ralph.cli.commands.workspace_health import workspace_health
from ralph.config.bootstrap import (
    ensure_global_agents_config,
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
    ensure_local_configs,
    regenerate_all,
)
from ralph.config.enums import Verbosity
from ralph.config.loader import ConfigTomlError, load_config
from ralph.config.welcome import emit_first_run_welcome
from ralph.display.context import DisplayContext
from ralph.display.context import make_display_context as _make_display_context
from ralph.display.excepthook import install_sanitizing_excepthook
from ralph.display.log_sink import make_sanitizing_log_sink, make_stderr_log_sink
from ralph.display.parallel_display import resolve_active_display
from ralph.display.terminal_restore import (
    _resolve_fd,
    restore_terminal,
    restore_terminal_modes,
    snapshot_terminal_modes,
    terminal_restore_sequence,
)
from ralph.onboarding import init_help_text, init_local_config_help_text
from ralph.pipeline import checkpoint as ckpt
from ralph.policy.loader import load_policy, load_policy_for_workspace_scope
from ralph.policy.validation import validate_agent_chains_satisfiable, validate_chain_agents_on_path
from ralph.process._spawn_env import sanitize_process_environment
from ralph.project_policy.policy_mode import PolicyMode
from ralph.visual.judgement_tier import run_on_demand_judgement
from ralph.workspace.scope import (
    PROJECT_SCOPE_LABEL,
    WORKTREE_SCOPE_LABEL,
    resolve_workspace_scope,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import ModuleType

    from rich.console import Console

    from ralph.agents.registry import AgentRegistry
    from ralph.cli._cli_overrides import CLIOverrides
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay


if TYPE_CHECKING:

    class _CommandMain(Protocol):
        def __call__(
            self,
            *,
            args: Sequence[str] | None = None,
            prog_name: str | None = None,
            complete_var: str | None = None,
            standalone_mode: bool = True,
            windows_expand_args: bool = True,
        ) -> object: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> AgentRegistry: ...

    class _ValidateCustomMcpServersFn(Protocol):
        def __call__(self, workspace_root: RuntimePath) -> int: ...


click.rich_click.USE_RICH_MARKUP = True
click.rich_click.USE_MARKDOWN = True

app = typer.Typer(
    name="ralph",
    help="[bold]Ralph Workflow[/bold] - Multi-agent AI orchestration pipeline.\n\n"
    "Ralph Workflow orchestrates AI coding agents to implement changes based on PROMPT.md.\n"
    "It runs a developer agent for code implementation across multiple planning and\n"
    "development iterations, automatically staging and committing the final result.",
    add_completion=True,
    rich_markup_mode="rich",
    suggest_commands=True,
)

_typer_get_command = typer.main.get_command


def _as_click_command(command: object) -> click.Command:
    """Bridge across typer versions that expose different Command types."""
    return cast(
        "click.Command", command
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


def _get_cli_context() -> DisplayContext:
    """Resolve a fresh DisplayContext for the current terminal environment."""
    return _make_display_context()


_LONG_DEVELOPER_ITERS = 5
_THOROUGH_DEVELOPER_ITERS = 10


def _prepare_init_args(args: Sequence[str] | None) -> list[str] | None:
    """Normalize a missing --init label before Click parsing."""
    if args is None:
        args = sys.argv[1:]

    normalized_args: list[str] = list(args)

    for index, arg in enumerate(normalized_args):
        if arg == "--init":
            next_arg = normalized_args[index + 1] if index + 1 < len(normalized_args) else None
            if next_arg is None or next_arg.startswith("-"):
                normalized_args.insert(index + 1, "")
            break

    return normalized_args


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_agent_registry_factory() -> _AgentRegistryFactory:
    return cast(
        "_AgentRegistryFactory",
        _module_attr(import_module("ralph.agents.registry"), "AgentRegistry"),
    )


def _load_validate_custom_mcp_servers() -> _ValidateCustomMcpServersFn:
    return cast(
        "_ValidateCustomMcpServersFn",
        _module_attr(import_module("ralph.pipeline.runner"), "validate_custom_mcp_servers"),
    )


def _set_command_main(command: click.Command, callback: _CommandMain) -> None:
    cast("dict[str, object]", command.__dict__)["main"] = callback


def _set_typer_testing_get_command(
    callback: Callable[[typer.Typer], click.Command],
) -> None:
    cast("dict[str, object]", typer.testing.__dict__)["_get_command"] = callback


def _get_command_with_optional_init(typer_instance: typer.Typer) -> click.Command:
    command = _as_click_command(_typer_get_command(typer_instance))
    if typer_instance is app:
        original_main: _CommandMain = command.main

        def patched_main(
            *,
            args: Sequence[str] | None = None,
            prog_name: str | None = None,
            complete_var: str | None = None,
            standalone_mode: bool = True,
            windows_expand_args: bool = True,
        ) -> object:
            try:
                return original_main(
                    args=_prepare_init_args(args),
                    prog_name=prog_name,
                    complete_var=complete_var,
                    standalone_mode=standalone_mode,
                    windows_expand_args=windows_expand_args,
                )
            except click.ClickException as exc:
                # rich_click exceptions are not in typer's class hierarchy and
                # therefore bypass typer's own ClickException handler, which
                # would normally call sys.exit(e.exit_code).  Replicate it here
                # so callers (including typer's CliRunner) see the correct exit
                # code instead of falling through to the generic except-Exception
                # branch that produces exit code 1.
                if not standalone_mode:
                    raise
                exc.show()
                sys.exit(exc.exit_code)

        _set_command_main(command, patched_main)
    return command


object.__setattr__(typer.main, "get_command", _get_command_with_optional_init)
_set_typer_testing_get_command(_get_command_with_optional_init)


def version_callback(version: bool, ctx: DisplayContext | None = None) -> None:
    """Route ``--version`` through the shared display welcome banner.

    A dev build adds the checkout it was installed from. ``rdev`` is a single
    machine-wide launcher that any checkout or worktree can take over, so the
    version alone does not identify which sources are actually running.
    """
    if version:
        from ralph.display.parallel_display import resolve_active_display

        resolved_ctx = ctx if ctx is not None else _get_cli_context()
        display = resolve_active_display(None, resolved_ctx)
        display.emit_welcome_banner(version=flavored_version())
        provenance = build_provenance_line()
        if provenance:
            display.emit_status(provenance)
        raise typer.Exit()


def _config_path(config: str | None) -> RuntimePath | None:
    """Convert CLI config string into a Path when provided."""
    if config is None:
        return None

    return RuntimePath(config)


def resolve_effective_verbosity(
    verbosity: Verbosity,
    *,
    quiet: bool,
    debug: bool,
) -> Verbosity:
    """Compute the verbosity to use for the run.

    ``--quiet`` and ``--debug`` take precedence. Absent those, the default
    is ``Verbosity.VERBOSE`` so Ralph Workflow is visibly active by default. The
    legacy ``--verbosity normal`` input is mapped to VERBOSE to preserve
    wrapper scripts that passed ``normal`` explicitly.
    """
    if quiet:
        return Verbosity.QUIET
    if debug:
        return Verbosity.DEBUG
    if verbosity == Verbosity.NORMAL:
        return Verbosity.VERBOSE
    return verbosity


def _try_load_registry() -> AgentRegistry | None:
    """Attempt to load the agent registry; returns None on failure."""
    try:
        cfg = load_config(None, {})
        registry_type = _load_agent_registry_factory()
        return registry_type.from_config(cfg)
    except Exception:
        return None


def _bootstrap_global_configs(
    *, display_context: DisplayContext, emit_welcome: bool = True
) -> None:
    """Create user-global configs, optionally leaving onboarding to the caller."""
    results = [
        ensure_global_config(),
        ensure_global_agents_config(),
        ensure_global_mcp_config(),
        *ensure_global_policy_configs(),
    ]
    registry = None
    if any(r.action in {"created", "regenerated"} for r in results):
        registry = _try_load_registry()
    if emit_welcome:
        emit_first_run_welcome(
            results,
            agent_registry=registry,
            display_context=display_context,
        )


def _bootstrap_global_configs_or_exit(
    display_context: DisplayContext, *, emit_welcome: bool = True
) -> None:
    """Run ``bootstrap_global_configs`` and render the envelope on config error.

    ``load_toml`` raises ``ConfigTomlError`` when a pre-existing
    user-global TOML is malformed (the migration path in
    ``ensure_global_config`` reads the existing file). Render the
    existing what/why/fix envelope and exit 1 instead of letting the
    raw ``ValueError`` propagate as a traceback.
    """
    try:
        if emit_welcome:
            bootstrap_global_configs(display_context=display_context)
        else:
            _bootstrap_global_configs(display_context=display_context, emit_welcome=False)
    except ConfigTomlError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=1) from None


def _bootstrap_for_command(
    *, init_requested: bool, regenerate_config: bool, display_context: DisplayContext
) -> None:
    """Bootstrap commands that need global config before their dedicated handler."""
    if not init_requested:
        _bootstrap_global_configs_or_exit(
            display_context,
            emit_welcome=not regenerate_config,
        )


def _handle_init(
    *, template: str | None, config: str | None, display_context: DisplayContext
) -> None:
    """Run init and preserve the setup-time TOML error envelope."""
    try:
        init_command(template, _config_path(config), display_context=display_context)
    except ConfigTomlError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=1) from None


def _handle_regenerate_config(*, display_context: DisplayContext) -> None:
    """Regenerate globals and refresh only project-local configs already present."""
    display = resolve_active_display(None, display_context)
    agent_dir: RuntimePath | None
    try:
        scope = resolve_workspace_scope()
        agent_dir = scope.local_config_path.parent
    except Exception as exc:
        logger.debug("Workspace scope unavailable, skipping local regenerate: {}", exc)
        agent_dir = None
    results = regenerate_all(agent_dir=agent_dir)
    if results:
        created_or_regenerated = [r for r in results if r.action in {"created", "regenerated"}]
        if created_or_regenerated:
            emit_first_run_welcome(results, is_regenerate=True, display_context=display_context)
        else:
            display.emit_status("No configs needed regeneration (all files up-to-date)")
    else:
        display.emit_status("No configs found to regenerate")


def _init_telemetry() -> None:
    # Opt-out guard: RALPH_DISABLE_TELEMETRY=1 or
    # [general] telemetry_enabled=false skips Sentry entirely.
    # Runs as the first statement so no sentry call, ID generation, or
    # config-file write happens when the user has opted out.
    from ralph.telemetry._sentry import is_telemetry_disabled, is_telemetry_disabled_by_config

    if is_telemetry_disabled() or is_telemetry_disabled_by_config():
        return

    try:
        import atexit

        from ralph.telemetry._sentry import (
            finalize_session,
            init_sentry,
            record_session_start,
            set_environment_context,
            set_session_wallclock_start,
        )
        from ralph.telemetry._user_identity import generate_session_id, get_or_create_user_id

        user_id = get_or_create_user_id()
        session_id = generate_session_id()
        init_sentry(user_id, session_id)
        set_environment_context()
        record_session_start()
        set_session_wallclock_start()
        atexit.register(finalize_session)
    except Exception as exc:
        logger.warning("Telemetry unavailable: {}", exc)


def _record_cli_command(ctx: typer.Context) -> None:
    """Forward the invoked subcommand (or the literal ``pipeline``) as a privacy-safe tag.

    This is the single CLI chokepoint for the ``command`` telemetry tag. The
    value is drawn from a closed vocabulary: either ``ctx.invoked_subcommand``
    (a registered Typer command name — a developer-defined identifier, not
    user-supplied free text) or the literal ``"pipeline"`` when no
    subcommand is invoked (the default run path). The ``if ctx.invoked_subcommand:
    return`` guard later short-circuits subcommand dispatch, so this single
    call covers both paths. Opt-out-aware and fail-soft.
    """
    try:
        from ralph.telemetry._sentry import is_telemetry_disabled, record_command_invocation
    except Exception:
        logger.warning("Telemetry command-invocation forwarding unavailable", exc_info=True)
        return
    if is_telemetry_disabled():
        return
    try:
        record_command_invocation(ctx.invoked_subcommand or "pipeline")
    except Exception:
        logger.warning("Telemetry command-invocation forwarding failed", exc_info=True)


def _handle_generate_local_config(
    *, display_context: DisplayContext, scope_name: str | None = None
) -> None:
    """Create the local config set in the automatic or requested workspace layer."""
    display = resolve_active_display(None, display_context)
    scope = resolve_workspace_scope()
    is_worktree_scope = scope.is_linked_worktree and scope_name != PROJECT_SCOPE_LABEL
    target_dir = (
        scope.worktree_config_path.parent if is_worktree_scope else scope.project_config_path.parent
    )
    scope_label = WORKTREE_SCOPE_LABEL if is_worktree_scope else PROJECT_SCOPE_LABEL
    results = ensure_local_configs(target_dir)
    if any(result.action in {"created", "regenerated"} for result in results):
        emit_first_run_welcome(results, display_context=display_context)
    else:
        display.emit_status(f"Local config files already exist in: {target_dir}")
    display.emit_status(f"Local config scope: {scope_label}; directory: {target_dir}")
    if is_worktree_scope:
        display.emit_status(f"Inherits project config: {scope.project_config_path}")


def _handle_early_exit_flags(
    *,
    version: bool,
    explain_policy: bool,
    explain_policy_dir: str | None,
    check_policy: bool,
    counter_overrides: dict[str, int] | None = None,
) -> None:
    """Handle version and explain-policy early-exit flags before any bootstrap."""
    if version:
        version_callback(version)
    if explain_policy:
        policy_dir = RuntimePath(explain_policy_dir) if explain_policy_dir else None
        raise typer.Exit(code=explain_command(policy_dir))
    if check_policy:
        policy_dir = RuntimePath(explain_policy_dir) if explain_policy_dir else None
        raise typer.Exit(code=check_policy_command(policy_dir, counter_overrides=counter_overrides))


def _handle_force_init_skills(*, workspace_root: RuntimePath) -> None:
    """Run the ``--force-init-skills`` early-exit branch.

    Extracted from ``main()`` to keep its branch / statement count under
    the ruff ``PLR0912`` / ``PLR0915`` limits. Reinstalls baseline skills
    and surfaces any failure codes with the ``ralph --force-init-skills``
    remediation hint (re-printing the hint is harmless on the force path
    because the user explicitly asked for it).
    """
    from ralph.skills.manager import SkillManager

    display_context = _get_cli_context()
    manager = SkillManager()
    cap_state, failures = manager.reinstall_baseline_skills(workspace_root=workspace_root)
    print_capability_summary(display_context.console, cap_state, workspace_root=workspace_root)
    if failures:
        resolve_active_display(None, display_context).emit_skill_failure_warning(failures)


"""Typer callback for the ``ralph`` CLI entry point.

The handler is the console-script entry point declared in
``pyproject.toml`` (``ralph = ralph.cli.main:main``). It binds the
~33 flags exposed by the user-facing CLI and dispatches the pipeline
subcommand (or one of the bundled support subcommands such as
``--init``, ``--diagnose``, ``--generate-commit``, ``--init-skills``,
``--install-skills``, ``--skill-status``, ``--version``, ``--help``).

Args:
    ctx: Typer context carrying subcommand resolution and shared state.
    config: ``--config/-c`` path to the ralph configuration file.
    developer_iters: ``--developer-iters/-D`` maximum developer agent
        iterations per run.
    quick: ``--quick/-Q`` single-developer-iteration shortcut.
    long_run: ``--long/-L`` five-iteration preset.
    thorough: ``--thorough/-T`` ten-iteration preset.
    counter: ``--counter NAME=VALUE`` repeatable policy-counter override.
    developer_agent: ``--developer-agent/-a`` developer agent name.
    developer_model: ``--developer-model`` model flag for the developer
        agent.
    planner_agent: ``--planner-agent`` planner agent name.
    planner_model: ``--planner-model`` model flag for the planner agent.
    reviewer_agent: ``--reviewer-agent`` reviewer agent name.
    reviewer_model: ``--reviewer-model`` model flag for the reviewer.
    use_existing_pr: ``--use-existing-pr/-U`` reuse an existing PR
        instead of opening a fresh one.
    auto_pr: ``--auto-pr/-A`` automatically open a pull request after
        the run finishes successfully.
    pr_target: ``--pr-target`` target branch for the auto-PR.
    worktree_path: ``--worktree-path`` custom worktree path override.
    init: ``--init`` scaffold ``PROMPT.md`` and user-global config for
        the current project; it does not create project-local config.
    init_force_skills: ``--force-init-skills`` reinstall baseline skills.
    init_skills: ``--init-skills`` install bundled skills into the
        supported agent roots.
    install_skills: ``--install-skills`` install skills only (no
        ``.agent/`` scaffold).
    skill_status: ``--skill-status`` print installed-skill summary.
    diagnose: ``--diagnose`` pre-flight check (agents, MCP, capabilities).
    generate_commit: ``--generate-commit`` draft a commit message from
        the staged change set (dogfooded per AGENTS.md).
    base_branch: ``--base-branch`` base branch for ``--generate-commit``.
    max_commits: ``--max-commits`` cap the number of commits returned
        by ``--generate-commit``.
    exclude_globs: ``--exclude-globs`` repeatable glob patterns to skip
        when staging the commit payload.
    pipeline: ``--pipeline/-p`` path to the policy pipeline file.
    state: ``--state/-s`` path to the run state file.
    workspace: ``--workspace/-w`` workspace root override.
    target: ``--target/-t`` target repository path (defaults to ``cwd``).
    agent_timeout: ``--agent-timeout`` agent timeout in seconds.
    resume: ``--resume/-r`` resume from a checkpoint.
    checkpoint: ``--checkpoint`` save a checkpoint at the end of the run.
    no_progress: ``--no-progress`` suppress progress reporting.
    verbose: ``--verbose/-v`` increase log verbosity.
    version: ``--version`` print the package version and exit.
    help: ``--help/-h`` show the Typer-generated help text and exit.

Returns:
    ``None``. The CLI exit code is whatever the underlying subcommand
    returns (typically ``0`` on success, ``1`` on a verify-failure or
    pipeline error). ``--version`` and ``--help`` exit before any
    pipeline side effect.

Side effects:
    Invokes the configured pipeline (planning, development, review, fix
    cycles) which spawns agent subprocesses and writes artifacts under
    ``.agent/``. ``--init`` creates ``PROMPT.md`` and seeds ``.gitignore``;
    it never creates project-local TOMLs. Only the explicit local-config
    aliases create the supported local TOML set. Skill subcommands may mutate
    supported agent roots. ``--diagnose`` prints but does not mutate. ``--version``
    and ``--help`` are read-only.
"""


class _CLIRestoreState:
    registered: bool = False
    signals_registered: bool = False


_CLI_RESTORE_STATE = _CLIRestoreState()


def ensure_cli_terminal_restore(
    *,
    register_fn: Callable[[Callable[[], None]], None] | None = None,
    signal_getter: Callable[[int], object] | None = None,
    signal_setter: Callable[[int, object], object] | None = None,
) -> None:
    """Snapshot TTY modes and register normal and termination restoration once."""
    if not _CLI_RESTORE_STATE.registered:
        snapshot_terminal_modes()
        reg = register_fn if register_fn is not None else atexit.register
        reg(restore_terminal)
        install_sanitizing_excepthook()
        _CLI_RESTORE_STATE.registered = True
    if _CLI_RESTORE_STATE.signals_registered or threading.current_thread() is not threading.main_thread():
        return
    getter = signal_getter if signal_getter is not None else signal.getsignal
    setter = signal_setter if signal_setter is not None else signal.signal
    signums: tuple[int, ...] = ()
    if hasattr(signal, "SIGTERM"):
        signums += (signal.SIGTERM,)
    if hasattr(signal, "SIGHUP"):
        signums += (signal.SIGHUP,)
    if hasattr(signal, "SIGQUIT"):
        signums += (signal.SIGQUIT,)
    for signum in signums:
        previous = getter(signum)

        def _restore_then_delegate(received: int, frame: object, *, previous: object = previous) -> None:
            fd = _resolve_fd(None)
            if fd is not None:
                with contextlib.suppress(Exception):
                    os.write(fd, terminal_restore_sequence().encode())
            with contextlib.suppress(Exception):
                restore_terminal_modes(fd=fd)
            if callable(previous):
                previous(received, frame)
            else:
                setter(received, signal.SIG_DFL)
                signal.raise_signal(received)

        setter(signum, _restore_then_delegate)
    _CLI_RESTORE_STATE.signals_registered = True


def reset_cli_restore_state() -> None:
    """Reset CLI restore registration state for unit tests."""
    _CLI_RESTORE_STATE.registered = False
    _CLI_RESTORE_STATE.signals_registered = False


def main(
    ctx: typer.Context,
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to configuration file",
        ),
    ] = None,
    developer_iters: Annotated[
        int | None,
        typer.Option(
            "--developer-iters",
            "-D",
            min=1,
            help="Maximum developer agent iterations per run.",
        ),
    ] = None,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick",
            "-Q",
            help="Quick mode: run a single developer iteration (equivalent to -D 1).",
        ),
    ] = False,
    long_run: Annotated[
        bool,
        typer.Option(
            "--long",
            "-L",
            help=(
                "Long mode: run five developer iterations "
                f"(equivalent to -D {_LONG_DEVELOPER_ITERS})."
            ),
        ),
    ] = False,
    thorough: Annotated[
        bool,
        typer.Option(
            "--thorough",
            "-T",
            help=(
                "Thorough mode: run ten developer iterations "
                f"(equivalent to -D {_THOROUGH_DEVELOPER_ITERS})."
            ),
        ),
    ] = False,
    counter: Annotated[
        list[str] | None,
        typer.Option(
            "--counter",
            help="Override a policy-declared budget counter: NAME=VALUE (repeatable)",
        ),
    ] = None,
    developer_agent: Annotated[
        str | None,
        typer.Option(
            "--developer-agent",
            "-a",
            help="Developer agent name",
        ),
    ] = None,
    developer_model: Annotated[
        str | None,
        typer.Option(
            "--developer-model",
            help="Model flag for developer agent",
        ),
    ] = None,
    verbosity: Annotated[
        Verbosity,
        typer.Option(
            "--verbosity",
            "-v",
            help=(
                "Output verbosity (quiet, normal, verbose, full, debug). "
                "Default: verbose. Use --quiet to silence non-error output."
            ),
        ),
    ] = Verbosity.VERBOSE,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress all output except errors"),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable debug output"),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume", "-r", help="Resume from checkpoint"),
    ] = False,
    no_resume: Annotated[
        bool,
        typer.Option("--no-resume", help="Ignore existing checkpoint"),
    ] = False,
    unsafe_mode: Annotated[
        bool | None,
        typer.Option(
            "--unsafe-mode",
            help=(
                "Merge Ralph Workflow MCP into the agent existing MCP config"
                " instead of overwriting it"
            ),
        ),
    ] = None,
    inspect_checkpoint: Annotated[
        bool,
        typer.Option("--inspect-checkpoint", help="Show checkpoint contents as raw JSON"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run without invoking agents"),
    ] = False,
    list_agents: Annotated[
        bool,
        typer.Option("--list-agents", help="List configured agents"),
    ] = False,
    list_providers: Annotated[
        bool,
        typer.Option("--list-providers", help="List available providers"),
    ] = False,
    diagnose: Annotated[
        bool,
        typer.Option("--diagnose", "-d", help="Run diagnostics"),
    ] = False,
    check_config: Annotated[
        bool,
        typer.Option("--check-config", "-C", help="Validate configuration"),
    ] = False,
    check_mcp: Annotated[
        bool,
        typer.Option(
            "--check-mcp",
            help="Validate custom MCP servers and agent wiring then exit",
        ),
    ] = False,
    init: Annotated[
        str | None,
        typer.Option(
            "--init",
            help=init_help_text(),
        ),
    ] = None,
    regenerate_config: Annotated[
        bool,
        typer.Option(
            "--regenerate-config",
            help="Rewrite global configs and refresh existing local configs only"
            " (overwritten files are backed up to <name>.bak)",
        ),
    ] = False,
    force_init_skills: Annotated[
        bool,
        typer.Option(
            "--force-init-skills",
            help=(
                "Re-run baseline skill installation (user-global + project-scope) "
                "and exit. Pairs with --init for an explicit re-init; standalone "
                "forces the recheck path on a normal ralph run."
            ),
        ),
    ] = False,
    generate_local_config: Annotated[
        bool,
        typer.Option(
            "--init-local-config",
            "--generate-local-config",
            help=init_local_config_help_text(),
        ),
    ] = False,
    scope_name: Annotated[
        str | None,
        typer.Option(
            "--scope",
            help=(
                "Local-config scope: {worktree,project}; defaults to worktree in linked "
                "worktrees and project otherwise"
            ),
        ),
    ] = None,
    generate_commit_msg: Annotated[
        bool,
        typer.Option("--generate-commit-msg", help="Generate commit message"),
    ] = False,
    generate_commit: Annotated[
        bool,
        typer.Option("--generate-commit", help="Generate and apply commit"),
    ] = False,
    show_commit_msg: Annotated[
        bool,
        typer.Option(
            "--show-commit-msg",
            help=(
                "Show commit message; may be empty after --generate-commit "
                "because the artifact is deleted"
            ),
        ),
    ] = False,
    git_user_name: Annotated[
        str | None,
        typer.Option("--git-user-name", help="Git user name for commits"),
    ] = None,
    git_user_email: Annotated[
        str | None,
        typer.Option("--git-user-email", help="Git user email for commits"),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Show version"),
    ] = False,
    explain_policy: Annotated[
        bool,
        typer.Option(
            "--explain-policy",
            help="Print a human-readable explanation of the active policy and exit",
        ),
    ] = False,
    explain_policy_dir: Annotated[
        str | None,
        typer.Option(
            "--explain-policy-dir",
            hidden=True,
            help="Policy directory to explain or check (default: bundled defaults)",
        ),
    ] = None,
    parallel_worker_manifest: Annotated[
        str | None,
        typer.Option(
            "--parallel-worker-manifest",
            hidden=True,
            help="Internal worker bootstrap manifest path.",
        ),
    ] = None,
    check_policy: Annotated[
        bool,
        typer.Option(
            "--check-policy",
            help="Validate the active policy and print a summary, then exit",
        ),
    ] = False,
    redo_policy: Annotated[
        bool,
        typer.Option(
            "--redo-policy",
            help=(
                "Delete the project's quality-policy documents and regenerate "
                "them from scratch. Combine with --policy-only to exit afterwards"
            ),
        ),
    ] = False,
    run_policy_agents: Annotated[
        bool,
        typer.Option(
            "--run-policy-agents",
            help=(
                "Audit the EXISTING policy with the policy agents; nothing is "
                "overwritten unless the review rejects it. Combine with "
                "--policy-only to exit afterwards"
            ),
        ),
    ] = False,
    policy_only: Annotated[
        bool,
        typer.Option(
            "--policy-only",
            help=(
                "Exit once the policy work is done instead of continuing into "
                "the development run. Modifies --redo-policy / --run-policy-agents"
            ),
        ),
    ] = False,
) -> None:
    """Run the Ralph Workflow multi-agent pipeline or execute a sub-operation.

    The handler is the ``ralph`` console script entry point declared in
    ``pyproject.toml`` (``ralph = ralph.cli.main:app``). It is the single
    Typer callback that fans out to ~12 early-exit branches
    (``--version``, ``--init``, ``--diagnose``, ``--check-mcp``,
    ``--check-config``, ``--init-local-config``, ``--inspect-checkpoint``,
    ``--list-agents``, ``--list-providers``, ``--generate-commit*``,
    ``--explain-policy``, ``--check-policy``) and
    then to the main pipeline invocation.

    Primary flags:

    - ``--init [label]`` — scaffold ``PROMPT.md`` and user-global
      configuration. It never creates project-local TOMLs; use
      ``--init-local-config`` (or ``--generate-local-config``) for the
      advanced explicit local override set.
    - ``--diagnose`` / ``-d`` — pre-flight check of agent CLIs, MCP
      servers, and capability bundles; never starts a real run.
    - ``--generate-commit`` / ``--generate-commit-msg`` — build the
      commit artifact from the latest development_result; ``--generate-commit``
      applies the commit. Always dogfood this for the AGENTS.md commit
      rule rather than hand-rolling ``git commit``.
    - ``--quick`` / ``-Q``, ``--long`` / ``-L``, and ``--thorough`` /
      ``-T`` — mutually exclusive depth presets that map to
      developer-iteration counts (1, 5, and 10 respectively).
    - ``--developer-iters`` / ``-D``, ``--reviewer-reviews`` / ``-R`` —
      explicit iteration caps (overridden by the depth presets).
    - ``--resume`` / ``-r`` and ``--no-resume`` — checkpoint handling.
    - ``--counter NAME=VALUE`` (repeatable) — override a policy-declared
      budget counter; the name must be declared in ``pipeline.toml`` or
      the run is rejected.

    Pipeline-invocation side effect: when none of the early-exit branches
    fire, the handler builds a ``CLIOverrides`` bundle, calls
    ``bootstrap_global_configs`` + ``configure_logging``, resolves the
    effective developer-iteration count, and dispatches to
    ``run_pipeline``. The run writes ``.agent/checkpoint.json`` and
    emits a finish-receipt on success.

    Args:
        ctx: Typer context (carries the global CLI state; not
            directly consumed by this handler).
        config: ``--config`` / ``-c`` path to an explicit configuration
            file.
        developer_iters: ``--developer-iters`` / ``-D`` developer-agent
            iteration cap.
        quick: ``--quick`` / ``-Q`` single-iteration preset.
        long_run: ``--long`` / ``-L`` five-iteration preset.
        thorough: ``--thorough`` / ``-T`` ten-iteration preset.
        counter: ``--counter`` repeatable ``NAME=VALUE`` overrides.
        developer_agent: ``--developer-agent`` / ``-a`` agent name.
        developer_model: ``--developer-model`` model flag.
        verbosity: ``--verbosity`` / ``-v`` output verbosity
            (quiet / normal / verbose / full / debug).
        quiet: ``--quiet`` / ``-q`` suppress non-error output.
        debug: ``--debug`` enable debug output.
        resume: ``--resume`` / ``-r`` resume from checkpoint.
        no_resume: ``--no-resume`` ignore any existing checkpoint.
        unsafe_mode: ``--unsafe-mode`` merge Ralph Workflow's MCP config into
            the agent's existing config instead of overwriting.
        inspect_checkpoint: ``--inspect-checkpoint`` print checkpoint
            JSON and exit.
        dry_run: ``--dry-run`` run without invoking agents.
        list_agents: ``--list-agents`` print configured agents and exit.
        list_providers: ``--list-providers`` print providers and exit.
        diagnose: ``--diagnose`` / ``-d`` pre-flight check.
        check_config: ``--check-config`` / ``-C`` validate config.
        check_mcp: ``--check-mcp`` validate custom MCP servers.
        init: ``--init [PATH]`` scaffold ``PROMPT.md`` and global config.
        regenerate_config: ``--regenerate-config`` rewrite global config
            and refresh only local config files already present (backs up
            overwritten files to ``<name>.bak``).
        force_init_skills: ``--force-init-skills`` re-run baseline
            skill install.
        generate_local_config: ``--init-local-config`` /
            ``--generate-local-config`` create the complete advanced
            project-local config override set.
        generate_commit_msg: ``--generate-commit-msg`` build commit
            message artifact.
        generate_commit: ``--generate-commit`` build and apply commit.
        show_commit_msg: ``--show-commit-mg`` show the commit message.
        git_user_name: ``--git-user-name`` git user name for commits.
        git_user_email: ``--git-user-email`` git user email for commits.
        version: ``--version`` / ``-V`` print version and exit.
        explain_policy: ``--explain-policy`` print human-readable policy
            and exit.
        explain_policy_dir: ``--explain-policy-dir`` (hidden) policy
            directory to explain.
        parallel_worker_manifest: ``--parallel-worker-manifest`` (hidden)
            internal worker bootstrap manifest path.
        check_policy: ``--check-policy`` validate active policy and exit.

    Returns:
        ``None``. The handler exits via ``typer.Exit`` or via the
        underlying ``run_pipeline`` return code; it never returns
        normally on success.

    Side effects:
        Bootstrap global config / MCP config / policy configs; write
        ``.agent/checkpoint.json``; spawn the configured agent CLI;
        write artifact files via the canonical MCP path; emit
        ``declare_complete`` on success. Bounded subprocesses are
        routed through ``ralph.process.manager``.
    """
    removed_malloc_debug_vars = sanitize_process_environment()
    ensure_cli_terminal_restore()
    if removed_malloc_debug_vars:
        logger.debug(
            "Stripped inherited malloc-debug environment variables: {}",
            ", ".join(removed_malloc_debug_vars),
        )

    # Parse --counter NAME=VALUE entries early so --check-policy can validate them.
    counter_overrides = _parse_counter_overrides(list(counter) if counter else [])

    _handle_early_exit_flags(
        version=version,
        explain_policy=explain_policy,
        explain_policy_dir=explain_policy_dir,
        check_policy=check_policy,
        counter_overrides=counter_overrides,
    )

    _validate_mode_flags(
        quick=quick,
        long_run=long_run,
        thorough=thorough,
        resume=resume,
        no_resume=no_resume,
    )
    policy_mode = _resolve_policy_mode(
        redo_policy=redo_policy,
        run_policy_agents=run_policy_agents,
        policy_only=policy_only,
    )

    verbosity = resolve_effective_verbosity(verbosity, quiet=quiet, debug=debug)

    _cli_ctx = _get_cli_context()

    # Configure logging before bootstrap/init so setup never leaks loader DEBUG lines.
    configure_logging(verbosity, console_sink=make_sanitizing_log_sink(_cli_ctx))
    _bootstrap_for_command(
        init_requested=init is not None or "--init" in sys.argv[1:],
        regenerate_config=regenerate_config,
        display_context=_cli_ctx,
    )
    _init_telemetry()
    _record_cli_command(ctx)

    # Mode presets imply developer iteration counts and override explicit -D when supplied.
    effective_developer_iters = _resolve_effective_developer_iters(
        quick=quick,
        long_run=long_run,
        thorough=thorough,
        developer_iters=developer_iters,
    )

    counter_overrides = _counter_overrides_with_developer_iters(
        counter_overrides, effective_developer_iters
    )

    # Load configuration
    cli_overrides = _build_cli_overrides(
        CLIOverrideInput(
            developer_agent=developer_agent,
            developer_model=developer_model,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            developer_iters=effective_developer_iters,
            unsafe_mode=unsafe_mode,
        ),
    )

    # Check for early exit commands
    exit_code = handle_list_agents(config, cli_overrides, list_agents, display_context=_cli_ctx)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_list_providers(list_providers, display_context=_cli_ctx)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_check_config(config, cli_overrides, check_config, console=_cli_ctx.console)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_check_mcp(check_mcp, display_context=_cli_ctx)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    if diagnose:
        exit_code = diagnose_command(_config_path(config), cli_overrides, display_context=_cli_ctx)
        raise typer.Exit(code=exit_code)

    if init is not None:
        _handle_init(template=init, config=config, display_context=_cli_ctx)
        raise typer.Exit()

    if regenerate_config:
        _handle_regenerate_config(display_context=_cli_ctx)
        raise typer.Exit()

    if force_init_skills:
        _handle_force_init_skills(
            workspace_root=RuntimePath.cwd(),
        )
        raise typer.Exit()

    if generate_local_config:
        if scope_name not in {None, WORKTREE_SCOPE_LABEL, PROJECT_SCOPE_LABEL}:
            raise typer.BadParameter(
                f"must be one of: {WORKTREE_SCOPE_LABEL}, {PROJECT_SCOPE_LABEL}",
                param_hint="--scope",
            )
        _handle_generate_local_config(scope_name=scope_name, display_context=_cli_ctx)
        raise typer.Exit()

    if inspect_checkpoint:
        summary = ckpt.inspect()
        display = resolve_active_display(None, _cli_ctx)
        display.emit_status(str(summary))
        raise typer.Exit()

    exit_code = handle_commit_plumbing(
        CommitPlumbingOptions(
            generate_commit_msg=generate_commit_msg,
            generate_commit=generate_commit,
            show_commit_msg=show_commit_msg,
            config_path=_config_path(config),
            cli_overrides=cli_overrides,
        ),
        display_context=_cli_ctx,
    )
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    # If a subcommand was invoked, we're done
    if ctx.invoked_subcommand:
        return

    # Best-effort nag if a newer release is available; never blocks the run.
    from ralph.update_check import maybe_render_update_nag

    maybe_render_update_nag(_cli_ctx)

    # Run the main pipeline
    exit_code = invoke_pipeline(
        config,
        RunPipelineOpts(
            cli_overrides=cli_overrides,
            dry_run=dry_run,
            resume=resume,
            no_resume=no_resume,
            verbosity=verbosity,
            counter_overrides=counter_overrides,
            parallel_worker_manifest=_config_path(parallel_worker_manifest),
            policy_mode=policy_mode,
        ),
        display_context=_cli_ctx,
    )
    raise typer.Exit(code=exit_code)


app.callback(invoke_without_command=True)(main)
app.command()(cleanup)
app.command(name="contribute")(contribute)
app.command(name="workspace-health")(workspace_health)


def visual_judgement(
    target: Annotated[str, typer.Option("--target", help="Policy-declared visual target")],
    intent: Annotated[str, typer.Option("--intent", help="Design intent for the vision verdict")],
) -> None:
    """Request non-blocking, operator-invoked visual judgement evidence."""
    display = resolve_active_display(None, _get_cli_context())
    result = run_on_demand_judgement(target, intent)
    if result.blocker is not None:
        display.emit_warning(f"visual judgement blocked: {result.blocker}")
        raise typer.Exit(code=1)
    display.emit_status(f"submitted on-demand design verdict {result.verdict_id} ({result.status})")
    display.emit_status("on-demand visual judgement is not a blocking verification gate")


app.command(name="visual-judgement")(visual_judgement)


#: Appended to every ``--agent`` help string so ``--help`` states, in one
#: place, where the default actually comes from. The default itself is
#: resolved at call time from the operator's config -- see
#: ``ralph.cli.commands.smoke_agent_defaults``.
_SMOKE_AGENT_DEFAULT_HELP = (
    "Defaults to the first {label} entry in your \\[agent_chains], falling back "
    "to bare `{bare}` (which passes no model, so the CLI uses the model you "
    "configured)."
)


def smoke_interactive_claude(
    agent: str | None = typer.Option(
        None,
        help=(
            "Claude alias to smoke (e.g. claude/sonnet). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Claude", bare="claude")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual PTY/TUI smoke test for interactive Claude using claude/haiku."""
    raise typer.Exit(
        code=smoke_interactive_claude_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-claude")(smoke_interactive_claude)


def smoke_headless_claude(
    agent: str | None = typer.Option(
        None,
        help=(
            "Headless Claude alias to smoke (e.g. claude-headless/sonnet). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="headless Claude", bare="claude-headless")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual NDJSON smoke test for headless Claude using claude-headless/haiku."""
    raise typer.Exit(
        code=smoke_headless_claude_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-headless-claude")(smoke_headless_claude)


def smoke_interactive_agy(
    agent: str | None = typer.Option(
        None,
        help=(
            "AGY model alias to smoke (e.g. agy/gemini-3.6-flash-low). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="AGY", bare="agy")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual PTY smoke test for Google Anti Gravity."""
    raise typer.Exit(
        code=smoke_interactive_agy_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-agy")(smoke_interactive_agy)


def smoke_interactive_nanocoder(
    agent: str | None = typer.Option(
        None,
        help=(
            "Nanocoder alias to smoke (e.g. nanocoder/MiniMax Coding/MiniMax-M3). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Nanocoder", bare="nanocoder")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual PTY smoke test for Nanocoder interactive mode."""
    raise typer.Exit(
        code=smoke_interactive_nanocoder_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-nanocoder")(smoke_interactive_nanocoder)


def smoke_interactive_cursor(
    agent: str | None = typer.Option(
        None,
        help=(
            "Cursor model alias to smoke (e.g. cursor/auto, cursor/gpt-5.3-codex-high). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Cursor", bare="cursor")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual end-to-end smoke test for the Cursor Agent CLI."""
    raise typer.Exit(
        code=smoke_interactive_cursor_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-cursor")(smoke_interactive_cursor)


def smoke_interactive_kimi(
    agent: str | None = typer.Option(
        None,
        help=(
            "Kimi model alias to smoke, as kimi/<model> with the full "
            "configured id (e.g. kimi/kimi-code/kimi-for-coding, "
            "kimi/kimi-code/k3-256k). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Kimi", bare="kimi")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual end-to-end smoke test for the Kimi Code CLI."""
    raise typer.Exit(
        code=smoke_interactive_kimi_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-kimi")(smoke_interactive_kimi)


def smoke_interactive_opencode(
    agent: str | None = typer.Option(
        None,
        help=(
            "OpenCode alias to smoke, as opencode/<provider>/<model> "
            "(e.g. opencode/minimax/MiniMax-M3). Run `opencode models` "
            "to list reachable provider/model pairs. "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="OpenCode", bare="opencode")
        ),
    ),
    provider: str | None = typer.Option(
        None,
        help=(
            "OpenCode provider (e.g. minimax). Requires --model; together they override --agent."
        ),
    ),
    model: str | None = typer.Option(
        None,
        help="OpenCode model (e.g. MiniMax-M3). Requires --provider.",
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual smoke test for OpenCode against a live provider/model."""
    if (provider is None) != (model is None):
        raise click.UsageError("--provider and --model must be given together")
    agent_name = f"opencode/{provider}/{model}" if provider is not None else agent
    raise typer.Exit(
        code=smoke_interactive_opencode_command(
            agent_name=agent_name,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-opencode")(smoke_interactive_opencode)


def smoke_interactive_codex(
    agent: str | None = typer.Option(
        None,
        help=(
            "Codex agent to smoke. Bare 'codex' uses the model from the operator's "
            "Codex config; pass a codex/<model> alias to pin one. "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Codex", bare="codex")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual smoke test for Codex CLI."""
    raise typer.Exit(
        code=smoke_interactive_codex_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-codex")(smoke_interactive_codex)


def smoke_interactive_pi(
    agent: str | None = typer.Option(
        None,
        help=(
            "Pi model alias to smoke (e.g. pi or pi/<model>). "
            + _SMOKE_AGENT_DEFAULT_HELP.format(label="Pi", bare="pi")
        ),
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual smoke test for the Pi coding agent."""
    raise typer.Exit(
        code=smoke_interactive_pi_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-pi")(smoke_interactive_pi)


def smoke_interactive_ccs(
    agent: str = typer.Option(
        "ccs/glm",
        help="CCS alias to smoke (e.g. ccs/glm).",
    ),
    subagents: bool = typer.Option(
        False,
        "--subagents",
        help="Require native subagent dispatch, result, and later main-agent activity.",
    ),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help=(
            "Drive the run from a multimodal-aware prompt that exercises the project's read_media / "
            "read_image endpoints. The run is graded WIRE only when the agent issues the "
            "verified media tool calls (criterion 5)."
        ),
    ),
    subagent_prompt_file: Annotated[
        RuntimePath | None,
        typer.Option(
            "--subagent-prompt-file",
            help="UTF-8 delegated-task prompt file; requires --subagents.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
) -> None:
    """Run the manual smoke test for a CCS (Claude Code Switch) alias."""
    raise typer.Exit(
        code=smoke_interactive_ccs_command(
            agent_name=agent,
            display_context=_get_cli_context(),
            subagents=subagents,
            subagent_prompt_file=subagent_prompt_file,
            multimodal=multimodal,
        )
    )


app.command(name="smoke-interactive-ccs")(smoke_interactive_ccs)
app.command()(star)


#: Depth presets are mutually exclusive: each pins ``developer_iters`` to a
#: different value, so any two of them are a contradiction.
_DEPTH_PRESET_FLAGS: tuple[tuple[str, str], ...] = (
    ("quick", "--quick/-Q"),
    ("long_run", "--long/-L"),
    ("thorough", "--thorough/-T"),
)


def _validate_mode_flags(
    *, quick: bool, long_run: bool, thorough: bool, resume: bool, no_resume: bool
) -> None:
    if resume and no_resume:
        raise click.UsageError(
            "Conflicting flags: --resume and --no-resume cannot be used together"
        )
    selected = {"quick": quick, "long_run": long_run, "thorough": thorough}
    names = [label for key, label in _DEPTH_PRESET_FLAGS if selected[key]]
    if len(names) > 1:
        raise click.UsageError(f"{' and '.join(names)} cannot be used together")


#: (redo_policy, run_policy_agents, policy_only) -> the selected policy mode.
#: ``--policy-only`` is a MODIFIER, not a mode: it says "exit after the policy
#: work" and composes with either action flag.
_POLICY_MODES: dict[tuple[bool, bool], PolicyMode] = {
    (True, False): PolicyMode.REDO,
    (True, True): PolicyMode.REDO_ONLY,
    (False, False): PolicyMode.RUN_AGENTS,
    (False, True): PolicyMode.RUN_AGENTS_ONLY,
}


def _resolve_policy_mode(
    *,
    redo_policy: bool,
    run_policy_agents: bool,
    policy_only: bool,
) -> PolicyMode:
    """Map the policy flags onto a single :class:`PolicyMode`.

    ``--redo-policy`` (wipe and regenerate) and ``--run-policy-agents`` (audit in
    place) are mutually exclusive ACTIONS -- one destroys the existing policy and
    the other preserves it, so asking for both is a contradiction.
    ``--policy-only`` is a MODIFIER that composes with either.
    """
    if redo_policy and run_policy_agents:
        raise click.UsageError(
            "Conflicting flags: --redo-policy and --run-policy-agents cannot be "
            "used together (--redo-policy wipes the policy, --run-policy-agents "
            "audits it in place)"
        )
    if not redo_policy and not run_policy_agents:
        if policy_only:
            raise click.UsageError(
                "--policy-only modifies --redo-policy or --run-policy-agents; "
                "it does nothing on its own"
            )
        return PolicyMode.NORMAL
    return _POLICY_MODES[(redo_policy, policy_only)]


def _counter_overrides_with_developer_iters(
    counter_overrides: dict[str, int],
    developer_iters: int | None,
) -> dict[str, int]:
    """Fold an explicit developer-iteration count into the counter overrides.

    ``-D`` and the depth presets set the dev-cycle budget, which reaches a
    FRESH run through the config. A RESUMED run adopts the checkpoint's caps,
    so the instruction reaches it only as an explicit counter override; without
    this the operator's request was silently dropped and the run ended at the
    next final commit. ``None`` means the operator specified nothing. An
    explicit ``--counter iteration=N`` is the more specific instruction and
    wins.
    """
    if developer_iters is None:
        return counter_overrides
    return {"iteration": developer_iters, **counter_overrides}


def _resolve_effective_developer_iters(
    *, quick: bool, long_run: bool, thorough: bool, developer_iters: int | None
) -> int | None:
    if quick:
        return 1
    if long_run:
        return _LONG_DEVELOPER_ITERS
    if thorough:
        return _THOROUGH_DEVELOPER_ITERS
    return developer_iters


def _handle_list_agents(
    config: str | None,
    cli_overrides: dict[str, object],
    list_agents: bool,
    *,
    display_context: DisplayContext,
) -> int | None:
    """Handle --list-agents flag; returns exit code or None to continue."""
    if not list_agents:
        return None
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        cfg = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        agents: Mapping[str, AgentConfig] = cfg.agents
        _display = resolve_active_display(None, display_context)
        _display.emit_agents_table(agents)
        return 0
    except Exception as e:
        logger.error("Failed to list agents: {}", e)
        return 1


def _handle_list_providers(
    list_providers: bool,
    *,
    display_context: DisplayContext,
) -> int | None:
    """Handle --list-providers flag; returns exit code or None to continue."""
    if not list_providers:
        return None
    try:
        providers = fetch_providers()
        _display = resolve_active_display(None, display_context)
        _display.emit_providers_table(providers)
        return 0
    except Exception as e:
        logger.error("Failed to list providers: {}", e)
        return 1


def _handle_check_config(
    config: str | None,
    cli_overrides: dict[str, object],
    check_config: bool,
    *,
    display_context: DisplayContext,
) -> int | None:
    """Handle --check-config flag; returns exit code or None to continue."""
    if not check_config:
        return None
    display = resolve_active_display(None, display_context)
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config_value = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = _load_agent_registry_factory().from_config(config_value)
        if config_path is not None:
            bundle = load_policy(config_path.parent, config=config_value)
        else:
            if workspace_scope is None:
                raise RuntimeError("workspace scope is required for the active configuration")
            bundle = load_policy_for_workspace_scope(workspace_scope, config=config_value)
        validate_agent_chains_satisfiable(bundle, registry)
        validate_chain_agents_on_path(bundle.agents)
        display.emit_status("Configuration is valid")
        return 0
    except Exception as e:
        logger.error("Configuration is invalid: {}", e)
        return 1


def _handle_check_mcp(check_mcp: bool, *, display_context: DisplayContext) -> int | None:
    """Handle --check-mcp flag; returns exit code or None to continue."""
    if not check_mcp:
        return None
    display = resolve_active_display(None, display_context)
    validate_custom_mcp_servers = _load_validate_custom_mcp_servers()

    try:
        workspace_scope = resolve_workspace_scope()
        rc = validate_custom_mcp_servers(workspace_scope.root)
    except Exception as e:
        logger.error("MCP validation failed: {}", e)
        return 1
    if rc == 0:
        display.emit_status("MCP servers validated successfully")
    else:
        display.emit_warning("MCP validation failed — see logs")
    return rc


def _handle_commit_plumbing(
    options: CommitPlumbingOptions,
    *,
    display_context: DisplayContext,
) -> int | None:
    """Handle commit plumbing commands; returns exit code or None to continue."""
    if not (options.generate_commit_msg or options.generate_commit or options.show_commit_msg):
        return None

    commit_plumbing(options=options, display_context=display_context)
    return 0


@dataclass(frozen=True)
class _RunPipelineOpts:
    cli_overrides: dict[str, object]
    dry_run: bool
    resume: bool
    no_resume: bool
    verbosity: Verbosity = Verbosity.VERBOSE
    counter_overrides: dict[str, int] | None = None
    parallel_worker_manifest: RuntimePath | None = None
    policy_mode: PolicyMode = PolicyMode.NORMAL


def _run_pipeline(
    config: str | None,
    opts: _RunPipelineOpts,
    *,
    display_context: DisplayContext,
) -> int:
    """Run the main pipeline."""
    # Direct env-var check (no ralph.telemetry._sentry import needed).
    # When ``RALPH_DISABLE_TELEMETRY`` is truthy, never import _sentry so the
    # hot path executes without paying the sentry_sdk import cost.
    _raw_disable = os.environ.get("RALPH_DISABLE_TELEMETRY", "")
    _telemetry_enabled = _raw_disable.strip().lower() not in {"1", "true", "yes", "on"}

    def _set_outcome(outcome: str) -> None:
        if not _telemetry_enabled:
            return
        try:
            from ralph.telemetry._sentry import set_session_outcome
        except Exception:
            logger.warning("Telemetry outcome update unavailable", exc_info=True)
            return
        try:
            set_session_outcome(outcome)
        except Exception:
            logger.warning("Telemetry outcome update failed", exc_info=True)

    display = resolve_active_display(None, display_context)
    try:
        request = RunPipelineRequest(
            config_path=_config_path(config),
            cli_overrides=opts.cli_overrides,
            dry_run=opts.dry_run,
            resume=opts.resume and not opts.no_resume,
            verbosity=opts.verbosity,
            counter_overrides=opts.counter_overrides or {},
            parallel_worker_manifest=opts.parallel_worker_manifest,
            policy_mode=opts.policy_mode,
        )
        exit_code = run_pipeline(request, display_context=display_context)
        _set_outcome("success" if exit_code == 0 else "failure")
        return exit_code
    except KeyboardInterrupt:
        display.emit_warning("\nInterrupted by user")
        try:
            from ralph.interrupt import handle_keyboard_interrupt_at_cli

            handle_keyboard_interrupt_at_cli()
        except Exception:
            logger.warning("Interrupt dispatcher failed during outer CLI catch", exc_info=True)
        _set_outcome("interrupted")
        return 130
    except ConfigTomlError as e:
        # A malformed TOML raised during the run path: surface the
        # existing what/why/fix envelope via the display, NOT via
        # ``logger.exception`` (which would print a raw traceback).
        display.emit_warning(str(e))
        _set_outcome("failure")
        return 1
    except Exception as e:
        logger.exception("Pipeline failed: {}")
        display.emit_warning(f"Error: {e}")
        _set_outcome("failure")
        return 1


def _configure_logging(
    verbosity: Verbosity, *, console_sink: Callable[[str], None] | None = None
) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbosity: CLI verbosity branch. Each branch maps to a
            loguru level / format pair.
        console_sink: Optional callable that replaces the raw
            terminal sink. When ``None`` (default) the
            library/worker fallback ``make_stderr_log_sink`` is used,
            which strips terminal-control constructs before writing
            to the process error stream. The CLI's ``main()`` call
            site passes ``make_sanitizing_log_sink(_cli_ctx)`` so
            the rich Live status bar is the single painter of the
            terminal.
    """
    # Remove default handler
    logger.remove()

    sink = console_sink if console_sink is not None else make_stderr_log_sink()

    if verbosity == Verbosity.QUIET:
        logger.add(sink, level="ERROR")
    elif verbosity in {Verbosity.NORMAL, Verbosity.VERBOSE}:
        logger.add(sink, level="INFO")
    elif verbosity == Verbosity.FULL:
        logger.add(sink, level="DEBUG", format="{time:HH:mm:ss} {level} {message}")
    else:  # DEBUG
        logger.add(
            sink,
            level="TRACE",
            format="{time:HH:mm:ss} {level} {name}:{function}:{line} {message}",
        )


def _parse_counter_overrides(raw_entries: list[str]) -> dict[str, int]:
    """Parse NAME=VALUE counter override strings; raises UsageError on malformed input."""
    result: dict[str, int] = {}
    for entry in raw_entries:
        if "=" not in entry:
            raise click.UsageError(f"--counter: invalid format {entry!r} — expected NAME=VALUE")
        name, _, raw_value = entry.partition("=")
        name = name.strip()
        if not name:
            raise click.UsageError(f"--counter: blank counter name in {entry!r}")
        try:
            value = int(raw_value)
        except ValueError:
            raise click.UsageError(
                f"--counter {name!r}: value {raw_value!r} is not a valid integer"
            ) from None
        result[name] = value
    return result


def _build_cli_overrides(
    input: CLIOverrideInput,
) -> dict[str, object]:
    """Build CLI overrides dictionary from CLIOverrideInput."""
    overrides: CLIOverrides = {
        "general": {
            "git_user_name": None,
            "git_user_email": None,
            "execution": {},
            "workflow": {},
        },
        "developer_agent": None,
        "developer_model": None,
    }

    if input.developer_agent is not None:
        overrides["developer_agent"] = input.developer_agent

    if input.developer_model is not None:
        overrides["developer_model"] = input.developer_model

    if input.git_user_name is not None:
        overrides["general"]["git_user_name"] = input.git_user_name

    if input.git_user_email is not None:
        overrides["general"]["git_user_email"] = input.git_user_email

    if input.developer_iters is not None:
        overrides["general"]["developer_iters"] = input.developer_iters

    if input.unsafe_mode is not None:
        overrides["general"]["workflow"] = {"unsafe_mode": input.unsafe_mode}

    return dict(overrides)


# Public aliases — test-accessible names and monkeypatch interception points.
init_telemetry = _init_telemetry
record_cli_command = _record_cli_command
bootstrap_global_configs = _bootstrap_global_configs
configure_logging = _configure_logging
handle_commit_plumbing = _handle_commit_plumbing
handle_list_agents = _handle_list_agents
handle_list_providers = _handle_list_providers
parse_counter_overrides = _parse_counter_overrides
counter_overrides_with_developer_iters = _counter_overrides_with_developer_iters
prepare_init_args = _prepare_init_args
build_cli_overrides = _build_cli_overrides
RunPipelineOpts = _RunPipelineOpts
invoke_pipeline = _run_pipeline
LONG_DEVELOPER_ITERS = _LONG_DEVELOPER_ITERS
THOROUGH_DEVELOPER_ITERS = _THOROUGH_DEVELOPER_ITERS


def _resolve_display_from_legacy(
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> ParallelDisplay:
    """Resolve a display from either a console or a context, or build a default.

    When a legacy ``console`` is provided but no ``display_context``, build a
    context whose console is the supplied one so the test's stream is honored.
    """
    if display_context is not None:
        return resolve_active_display(None, display_context)
    if console is not None:
        ctx = _make_display_context(console=console, env={})
        return resolve_active_display(None, ctx)
    return resolve_active_display(None, _get_cli_context())


def handle_check_mcp(
    check_mcp: bool,
    *,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> int | None:
    """Public wrapper that accepts a legacy ``console`` kwarg for backward compat."""
    if not check_mcp:
        return None
    display = _resolve_display_from_legacy(console=console, display_context=display_context)
    validate_custom_mcp_servers = _load_validate_custom_mcp_servers()
    try:
        workspace_scope = resolve_workspace_scope()
        rc = validate_custom_mcp_servers(workspace_scope.root)
    except Exception as e:
        logger.error("MCP validation failed: {}", e)
        return 1
    if rc == 0:
        display.emit_status("MCP servers validated successfully")
    else:
        display.emit_warning("MCP validation failed \u2014 see logs")
    return rc


def handle_check_config(
    config: str | None,
    cli_overrides: dict[str, object],
    check_config: bool,
    *,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> int | None:
    """Public wrapper for the ``--check-config`` short-circuit; accepts legacy
    ``console=`` and new ``display_context=`` kwargs.
    """
    if not check_config:
        return None
    display = _resolve_display_from_legacy(console=console, display_context=display_context)
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config_value = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = _load_agent_registry_factory().from_config(config_value)
        if config_path is not None:
            bundle = load_policy(config_path.parent, config=config_value)
        else:
            if workspace_scope is None:
                raise RuntimeError("workspace scope is required for the active configuration")
            bundle = load_policy_for_workspace_scope(workspace_scope, config=config_value)
        validate_agent_chains_satisfiable(bundle, registry)
        validate_chain_agents_on_path(bundle.agents)
        display.emit_status("Configuration is valid")
        return 0
    except Exception as e:
        logger.error("Configuration is invalid: {}", e)
        return 1



if __name__ == "__main__":
    app()
