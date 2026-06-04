"""Ralph Workflow CLI entry point - typer application with rich-click help styling.

This module provides the main CLI application for Ralph Workflow, using typer
for argument parsing and rich-click for enhanced help output.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path as RuntimePath
from typing import TYPE_CHECKING, Annotated, Protocol, cast

import rich_click as click
import typer
import typer.testing
from loguru import logger
from rich.text import Text

from ralph import __version__
from ralph.api.opencode import list_providers as fetch_providers
from ralph.cli._cli_override_input import CLIOverrideInput
from ralph.cli.commands.check_policy import check_policy_command
from ralph.cli.commands.cleanup import cleanup
from ralph.cli.commands.contribute import contribute
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.commands.explain import explain_command
from ralph.cli.commands.init import init_command
from ralph.cli.commands.prompt_helper import run_prompt_helper
from ralph.cli.commands.run import RunPipelineRequest, run_pipeline
from ralph.cli.commands.smoke import smoke_interactive_claude_command
from ralph.cli.commands.star import star
from ralph.cli.options import display_agents_table, display_providers_table
from ralph.config.bootstrap import (
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_global_policy_configs,
    ensure_local_configs,
    regenerate_all,
)
from ralph.config.enums import Verbosity
from ralph.config.loader import load_config
from ralph.config.welcome import emit_first_run_welcome
from ralph.display.context import DisplayContext
from ralph.display.context import make_display_context as _make_display_context
from ralph.onboarding import init_help_text, init_local_config_help_text
from ralph.pipeline import checkpoint as ckpt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import ModuleType

    from rich.console import Console

    from ralph.agents.registry import AgentRegistry
    from ralph.cli._cli_overrides import CLIOverrides
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext


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


def _get_cli_context() -> DisplayContext:
    """Resolve a fresh DisplayContext for the current terminal environment."""
    return _make_display_context()


_KNOWN_SUBCOMMANDS: frozenset[str] = frozenset({"cleanup", "star"})
_QUICK_FLAGS: frozenset[str] = frozenset({"-Q", "--quick"})
_THOROUGH_DEVELOPER_ITERS = 10


def _prepare_init_args(args: Sequence[str] | None) -> list[str] | None:
    """Normalize --init and -Q positional text before Click parsing."""
    if args is None:
        args = sys.argv[1:]

    normalized_args: list[str] = list(args)

    for index, arg in enumerate(normalized_args):
        if arg == "--init":
            next_arg = normalized_args[index + 1] if index + 1 < len(normalized_args) else None
            if next_arg is None or next_arg.startswith("-"):
                normalized_args.insert(index + 1, "")
            break

    normalized_args = _inject_quick_prompt(normalized_args)
    return normalized_args


def _inject_quick_prompt(args: list[str]) -> list[str]:
    """Inject --prompt before bare positional text when -Q/--quick is present."""
    if not any(a in _QUICK_FLAGS for a in args):
        return args
    if "--prompt" in args or "-P" in args:
        return args
    result: list[str] = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            result.append(arg)
            continue
        # Options with values consume the next arg; skip it to avoid treating it as a prompt.
        if arg in {
            "--config",
            "-c",
            "--developer-iters",
            "-D",
            "--counter",
            "--developer-agent",
            "-a",
            "--developer-model",
            "--verbosity",
            "-v",
            "--init",
            "--git-user-name",
            "--git-user-email",
            "--explain-policy-dir",
        }:
            result.append(arg)
            skip_next = True
            continue
        if not arg.startswith("-") and arg not in _KNOWN_SUBCOMMANDS:
            # Bare positional text: inject --prompt before it
            result.append("--prompt")
            result.append(arg)
            return result + list(args[i + 1 :])
        result.append(arg)
    return result


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
    command = _typer_get_command(typer_instance)
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
            return original_main(
                args=_prepare_init_args(args),
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
            )

        _set_command_main(command, patched_main)
    return command


typer.main.get_command = _get_command_with_optional_init
_set_typer_testing_get_command(_get_command_with_optional_init)


def version_callback(version: bool, ctx: DisplayContext | None = None) -> None:
    """Print version information."""
    if version:
        c = ctx.console if ctx is not None else _get_cli_context().console
        version_text = Text()
        version_text.append("Ralph Workflow", style="theme.banner.title")
        version_text.append(" version ")
        version_text.append(__version__, style="theme.banner.version")
        c.print(version_text)
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


def _bootstrap_global_configs(*, display_context: DisplayContext) -> None:
    """Create user-global config files from bundled templates if they don't exist."""
    results = [
        ensure_global_config(),
        ensure_global_mcp_config(),
        *ensure_global_policy_configs(),
    ]
    registry = None
    if any(r.action in {"created", "regenerated"} for r in results):
        registry = _try_load_registry()
    emit_first_run_welcome(
        display_context.console,
        results,
        agent_registry=registry,
        display_context=display_context,
    )


def _handle_regenerate_config(*, display_context: DisplayContext) -> None:
    """Regenerate global and local configs from bundled defaults, backing up existing files."""
    c = display_context.console
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
            emit_first_run_welcome(c, results, is_regenerate=True, display_context=display_context)
        else:
            msg = "No configs needed regeneration (all files up-to-date)"
            c.print(Text(msg, style="theme.text.muted"))
    else:
        c.print(Text("No configs found to regenerate", style="theme.text.muted"))


def _init_telemetry() -> None:
    try:
        from ralph.telemetry._sentry import init_sentry
        from ralph.telemetry._user_identity import generate_session_id, get_or_create_user_id

        user_id = get_or_create_user_id()
        session_id = generate_session_id()
        init_sentry(user_id, session_id)
    except Exception as exc:
        logger.warning("Telemetry unavailable: {}", exc)


def _handle_generate_local_config(*, display_context: DisplayContext) -> None:
    """Create the full project-local config override set from the global config set."""
    console = display_context.console
    scope = resolve_workspace_scope()
    results = ensure_local_configs(scope.local_config_path.parent)
    if any(result.action in {"created", "regenerated"} for result in results):
        emit_first_run_welcome(console, results, display_context=display_context)
        return
    text = Text("Local config files already exist in: ", style="theme.text.muted")
    text.append(str(scope.local_config_path.parent))
    console.print(text)


def _handle_prompt_helper(
    config: str | None,
    cli_overrides: dict[str, object],
) -> None:
    """Handle --prompt-helper early-exit before pipeline."""
    config_path = _config_path(config)
    workspace_scope = None if config_path is not None else resolve_workspace_scope()
    workspace_root = workspace_scope.root if workspace_scope else RuntimePath.cwd()
    cfg = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
    run_prompt_helper(cfg, workspace_root)


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


def main(
    ctx: typer.Context,
    prompt: Annotated[
        str | None,
        typer.Option(
            "--prompt",
            "-P",
            help="Inline prompt text for quick runs (use with --quick/-Q)",
        ),
    ] = None,
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
            help="Rewrite global and local configs from bundled defaults"
            " (existing files are backed up to <name>.bak)",
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
    prompt_helper: Annotated[
        bool,
        typer.Option(
            "--prompt-helper",
            help=(
                "Launch interactive prompt-refinement helper. "
                "Starts a PM-style agent that helps turn a vague idea into PROMPT.md."
            ),
        ),
    ] = False,
) -> None:
    """Run the Ralph Workflow multi-agent pipeline or execute a sub-operation."""
    # Parse --counter NAME=VALUE entries early so --check-policy can validate them.
    counter_overrides = _parse_counter_overrides(list(counter) if counter else [])

    _handle_early_exit_flags(
        version=version,
        explain_policy=explain_policy,
        explain_policy_dir=explain_policy_dir,
        check_policy=check_policy,
        counter_overrides=counter_overrides,
    )

    _validate_mode_flags(quick=quick, thorough=thorough, resume=resume, no_resume=no_resume)

    verbosity = resolve_effective_verbosity(verbosity, quiet=quiet, debug=debug)

    _cli_ctx = _get_cli_context()
    _console = _cli_ctx.console

    bootstrap_global_configs(display_context=_cli_ctx)
    _init_telemetry()

    # Set up logging based on verbosity
    configure_logging(verbosity)

    _validate_prompt_flags(prompt, quick)

    # Mode presets imply developer iteration counts and override explicit -D when supplied.
    effective_developer_iters = _resolve_effective_developer_iters(
        quick=quick,
        thorough=thorough,
        developer_iters=developer_iters,
    )

    # Load configuration
    cli_overrides = _build_cli_overrides(
        CLIOverrideInput(
            developer_agent=developer_agent,
            developer_model=developer_model,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            developer_iters=effective_developer_iters,
        ),
    )

    # Check for early exit commands
    exit_code = handle_list_agents(config, cli_overrides, list_agents, display_context=_cli_ctx)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_list_providers(list_providers, display_context=_cli_ctx)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_check_config(config, cli_overrides, check_config, console=_console)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = handle_check_mcp(check_mcp, console=_console)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    if diagnose:
        exit_code = diagnose_command(_config_path(config), cli_overrides, display_context=_cli_ctx)
        raise typer.Exit(code=exit_code)

    if init is not None:
        init_command(init, _config_path(config), display_context=_cli_ctx)
        raise typer.Exit()

    if regenerate_config:
        _handle_regenerate_config(display_context=_cli_ctx)
        raise typer.Exit()

    if generate_local_config:
        _handle_generate_local_config(display_context=_cli_ctx)
        raise typer.Exit()

    if inspect_checkpoint:
        summary = ckpt.inspect()
        _console.print(summary)
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

    # Handle --prompt-helper before pipeline
    if prompt_helper:
        _handle_prompt_helper(config, cli_overrides)
        raise typer.Exit()

    # If a subcommand was invoked, we're done
    if ctx.invoked_subcommand:
        return

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
            inline_prompt=prompt,
            parallel_worker_manifest=_config_path(parallel_worker_manifest),
        ),
        display_context=_cli_ctx,
    )
    raise typer.Exit(code=exit_code)


app.callback(invoke_without_command=True)(main)
app.command()(cleanup)
app.command(name="contribute")(contribute)


def smoke_interactive_claude() -> None:
    """Run the manual PTY/TUI smoke test for interactive Claude using claude/haiku."""
    raise typer.Exit(code=smoke_interactive_claude_command(display_context=_get_cli_context()))


app.command(name="smoke-interactive-claude")(smoke_interactive_claude)
app.command()(star)


def _validate_mode_flags(*, quick: bool, thorough: bool, resume: bool, no_resume: bool) -> None:
    if resume and no_resume:
        raise click.UsageError(
            "Conflicting flags: --resume and --no-resume cannot be used together"
        )
    if quick and thorough:
        raise click.UsageError("--quick/-Q and --thorough/-T cannot be used together")


def _validate_prompt_flags(prompt: str | None, quick: bool) -> None:
    if prompt is not None and not quick:
        raise click.UsageError(
            "--prompt requires --quick/-Q. Usage: ralph -Q --prompt 'your prompt here'"
        )


def _resolve_effective_developer_iters(
    *, quick: bool, thorough: bool, developer_iters: int | None
) -> int | None:
    if quick:
        return 1
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
        display_agents_table(agents, display_context=display_context)
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
        display_providers_table(providers, display_context=display_context)
        return 0
    except Exception as e:
        logger.error("Failed to list providers: {}", e)
        return 1


def _handle_check_config(
    config: str | None,
    cli_overrides: dict[str, object],
    check_config: bool,
    *,
    console: Console | None = None,
) -> int | None:
    """Handle --check-config flag; returns exit code or None to continue."""
    if not check_config:
        return None
    c = console if console is not None else _get_cli_context().console
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        c.print(Text("Configuration is valid", style="theme.status.success"))
        return 0
    except Exception as e:
        logger.error("Configuration is invalid: {}", e)
        return 1


def _handle_check_mcp(check_mcp: bool, *, console: Console | None = None) -> int | None:
    """Handle --check-mcp flag; returns exit code or None to continue."""
    if not check_mcp:
        return None
    c = console if console is not None else _get_cli_context().console
    validate_custom_mcp_servers = _load_validate_custom_mcp_servers()

    try:
        workspace_scope = resolve_workspace_scope()
        rc = validate_custom_mcp_servers(workspace_scope.root)
    except Exception as e:
        logger.error("MCP validation failed: {}", e)
        return 1
    if rc == 0:
        c.print(Text("MCP servers validated successfully", style="theme.status.success"))
    else:
        c.print(Text("MCP validation failed — see logs", style="theme.status.error"))
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
    inline_prompt: str | None = None
    parallel_worker_manifest: RuntimePath | None = None


def _run_pipeline(
    config: str | None,
    opts: _RunPipelineOpts,
    *,
    display_context: DisplayContext,
) -> int:
    """Run the main pipeline."""
    c = display_context.console
    try:
        request = RunPipelineRequest(
            config_path=_config_path(config),
            cli_overrides=opts.cli_overrides,
            dry_run=opts.dry_run,
            resume=opts.resume and not opts.no_resume,
            verbosity=opts.verbosity,
            counter_overrides=opts.counter_overrides or {},
            inline_prompt=opts.inline_prompt,
            parallel_worker_manifest=opts.parallel_worker_manifest,
        )
        exit_code = run_pipeline(request, display_context=display_context)
        return exit_code
    except KeyboardInterrupt:
        c.print(Text("\nInterrupted by user", style="theme.status.warning"))
        return 130
    except Exception as e:
        logger.exception("Pipeline failed: {}")
        err_text = Text()
        err_text.append("Error:", style="theme.status.error")
        err_text.append(" ")
        err_text.append(str(e))
        c.print(err_text)
        return 1


def _configure_logging(verbosity: Verbosity) -> None:
    """Configure logging based on verbosity level."""
    # Remove default handler
    logger.remove()

    if verbosity == Verbosity.QUIET:
        logger.add(sys.stderr, level="ERROR")
    elif verbosity == Verbosity.NORMAL:
        logger.add(sys.stderr, level="INFO")
    elif verbosity == Verbosity.VERBOSE:
        logger.add(sys.stderr, level="DEBUG")
    elif verbosity == Verbosity.FULL:
        logger.add(sys.stderr, level="DEBUG", format="{time:HH:mm:ss} {level} {message}")
    else:  # DEBUG
        logger.add(
            sys.stderr,
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

    return dict(overrides)


# Public aliases — test-accessible names and monkeypatch interception points.
init_telemetry = _init_telemetry
bootstrap_global_configs = _bootstrap_global_configs
configure_logging = _configure_logging
handle_check_config = _handle_check_config
handle_check_mcp = _handle_check_mcp
handle_commit_plumbing = _handle_commit_plumbing
handle_list_agents = _handle_list_agents
handle_list_providers = _handle_list_providers
inject_quick_prompt = _inject_quick_prompt
parse_counter_overrides = _parse_counter_overrides
prepare_init_args = _prepare_init_args
build_cli_overrides = _build_cli_overrides
RunPipelineOpts = _RunPipelineOpts
invoke_pipeline = _run_pipeline
THOROUGH_DEVELOPER_ITERS = _THOROUGH_DEVELOPER_ITERS

if __name__ == "__main__":
    app()
