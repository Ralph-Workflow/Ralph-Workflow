"""Ralph CLI entry point - typer application with rich-click help styling.

This module provides the main CLI application for Ralph, using typer
for argument parsing and rich-click for enhanced help output.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path as RuntimePath
from typing import TYPE_CHECKING, Annotated, TypedDict

import rich_click as click
import typer
from loguru import logger
from rich.console import Console
from rich.text import Text

# Late imports to avoid circular dependencies
from ralph import __version__
from ralph.api.opencode import list_providers as fetch_providers
from ralph.cli.commands.cleanup import cleanup
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.diagnose import diagnose_command
from ralph.cli.commands.init import init_command
from ralph.cli.commands.run import run_pipeline
from ralph.cli.options import (
    display_agents_table,
    display_providers_table,
)
from ralph.config.enums import ReviewDepth, Verbosity
from ralph.config.loader import load_config
from ralph.pipeline import checkpoint as ckpt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.models import AgentConfig


@dataclass(frozen=True)
class CLIOverrideInput:
    """Input for building CLI overrides."""

    developer_iters: int | None = None
    reviewer_reviews: int | None = None
    developer_agent: str | None = None
    reviewer_agent: str | None = None
    developer_model: str | None = None
    reviewer_model: str | None = None
    review_depth: ReviewDepth | None = None
    git_user_name: str | None = None
    git_user_email: str | None = None
    isolation_mode: bool | None = None


class GeneralOverrides(TypedDict):
    """General configuration overrides."""

    developer_iters: int | None
    reviewer_reviews: int | None
    review_depth: str | None
    git_user_name: str | None
    git_user_email: str | None
    execution: dict[str, bool]


class CLIOverrides(TypedDict):
    """CLI configuration overrides."""

    general: GeneralOverrides
    developer_agent: str | None
    reviewer_agent: str | None
    developer_model: str | None
    reviewer_model: str | None


click.rich_click.USE_RICH_MARKUP = True
click.rich_click.USE_MARKDOWN = True

app = typer.Typer(
    name="ralph",
    help="[bold]Ralph[/bold] - Multi-agent AI orchestration pipeline.\n\n"
    "Ralph orchestrates AI coding agents to implement changes based on PROMPT.md.\n"
    "It runs a developer agent for code implementation, then a reviewer agent for\n"
    "review and fixes, automatically staging and committing the final result.",
    add_completion=True,
    rich_markup_mode="rich",
    suggest_commands=True,
)
console = Console()


def version_callback(version: bool) -> None:
    """Print version information."""
    if version:
        version_text = Text()
        version_text.append("Ralph", style="cyan")
        version_text.append(" version ")
        version_text.append(__version__, style="green")
        console.print(version_text)
        raise typer.Exit()


def _config_path(config: str | None) -> RuntimePath | None:
    """Convert CLI config string into a Path when provided."""
    if config is None:
        return None

    return RuntimePath(config)


def _resolve_effective_verbosity(
    verbosity: Verbosity,
    *,
    quiet: bool,
    debug: bool,
) -> Verbosity:
    """Compute the verbosity to use for the run.

    ``--quiet`` and ``--debug`` take precedence. Absent those, the default
    is ``Verbosity.VERBOSE`` so Ralph is visibly active by default. The
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


def main(  # noqa: PLR0913 - Typer CLI callbacks require many options and branches
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
        int,
        typer.Option(
            "--developer-iters",
            "-D",
            min=1,
            help="Number of developer agent iterations",
        ),
    ] = 5,
    reviewer_reviews: Annotated[
        int,
        typer.Option(
            "--reviewer-reviews",
            "-R",
            min=0,
            help="Number of review-fix cycles (0=skip review)",
        ),
    ] = 2,
    developer_agent: Annotated[
        str | None,
        typer.Option(
            "--developer-agent",
            "-a",
            help="Developer agent name",
        ),
    ] = None,
    reviewer_agent: Annotated[
        str | None,
        typer.Option(
            "--reviewer-agent",
            "-r",
            help="Reviewer agent name",
        ),
    ] = None,
    developer_model: Annotated[
        str | None,
        typer.Option(
            "--developer-model",
            help="Model flag for developer agent",
        ),
    ] = None,
    reviewer_model: Annotated[
        str | None,
        typer.Option(
            "--reviewer-model",
            help="Model flag for reviewer agent",
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
    no_isolation: Annotated[
        bool,
        typer.Option("--no-isolation", help="Disable isolation mode"),
    ] = False,
    review_depth: Annotated[
        ReviewDepth,
        typer.Option(
            "--review-depth",
            help="Review depth: standard, comprehensive, security, incremental",
        ),
    ] = ReviewDepth.STANDARD,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Resume from checkpoint"),
    ] = False,
    no_resume: Annotated[
        bool,
        typer.Option("--no-resume", help="Ignore existing checkpoint"),
    ] = False,
    inspect_checkpoint: Annotated[
        bool,
        typer.Option("--inspect-checkpoint", help="Show checkpoint contents"),
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
        typer.Option("--check-config", help="Validate configuration"),
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
        typer.Option("--init", help="Initialize Ralph with a template (e.g. starter-template)"),
    ] = None,
    rebase_only: Annotated[
        bool,
        typer.Option("--rebase-only", help="Only rebase, don't run pipeline"),
    ] = False,
    generate_commit_msg: Annotated[
        bool,
        typer.Option("--generate-commit-msg", help="Generate commit message"),
    ] = False,
    apply_commit: Annotated[
        bool,
        typer.Option("--apply-commit", help="Apply generated commit"),
    ] = False,
    generate_commit: Annotated[
        bool,
        typer.Option("--generate-commit", help="Generate and apply commit"),
    ] = False,
    show_commit_msg: Annotated[
        bool,
        typer.Option("--show-commit-msg", help="Show commit message"),
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
) -> None:
    """Run the Ralph multi-agent pipeline or execute a sub-operation."""
    # Handle version flag
    if version:
        version_callback(version)

    verbosity = _resolve_effective_verbosity(verbosity, quiet=quiet, debug=debug)

    # Set up logging based on verbosity
    _configure_logging(verbosity)

    # Load configuration
    cli_overrides = _build_cli_overrides(
        CLIOverrideInput(
            developer_iters=developer_iters,
            reviewer_reviews=reviewer_reviews,
            developer_agent=developer_agent,
            reviewer_agent=reviewer_agent,
            developer_model=developer_model,
            reviewer_model=reviewer_model,
            review_depth=review_depth,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            isolation_mode=not no_isolation,
        ),
    )

    # Check for early exit commands
    exit_code = _handle_list_agents(config, cli_overrides, list_agents)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = _handle_list_providers(list_providers)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = _handle_check_config(config, cli_overrides, check_config)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    exit_code = _handle_check_mcp(check_mcp)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    if diagnose:
        diagnose_command(_config_path(config), cli_overrides)
        raise typer.Exit()

    if init is not None:
        init_command(init, _config_path(config))
        raise typer.Exit()

    if inspect_checkpoint:
        summary = ckpt.inspect()
        console.print(summary)
        raise typer.Exit()

    exit_code = _handle_commit_plumbing(
        CommitPlumbingOptions(
            generate_commit_msg=generate_commit_msg,
            apply_commit=apply_commit,
            generate_commit=generate_commit,
            show_commit_msg=show_commit_msg,
            config_path=_config_path(config),
            cli_overrides=cli_overrides,
        ),
    )
    if exit_code is not None:
        raise typer.Exit(code=exit_code)

    if rebase_only:
        console.print("[yellow]Rebase-only mode not yet implemented[/yellow]")
        raise typer.Exit(1)

    # If a subcommand was invoked, we're done
    if ctx.invoked_subcommand:
        return

    # Run the main pipeline
    exit_code = _run_pipeline(config, cli_overrides, dry_run, resume, no_resume, verbosity)
    raise typer.Exit(code=exit_code)


app.callback(invoke_without_command=True)(main)
app.command()(cleanup)


def _handle_list_agents(
    config: str | None,
    cli_overrides: dict[str, object],
    list_agents: bool,
) -> int | None:
    """Handle --list-agents flag.

    Args:
        config: Path to config file.
        cli_overrides: CLI overrides dict.
        list_agents: Whether flag was set.

    Returns:
        Exit code or None to continue.
    """
    if not list_agents:
        return None
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        cfg = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        agents: Mapping[str, AgentConfig] = cfg.agents
        display_agents_table(agents)
        return 0
    except Exception as e:
        logger.error("Failed to list agents: {}", e)
        return 1


def _handle_list_providers(list_providers: bool) -> int | None:
    """Handle --list-providers flag.

    Args:
        list_providers: Whether flag was set.

    Returns:
        Exit code or None to continue.
    """
    if not list_providers:
        return None
    try:
        providers = fetch_providers()
        display_providers_table(providers)
        return 0
    except Exception as e:
        logger.error("Failed to list providers: {}", e)
        return 1


def _handle_check_config(
    config: str | None,
    cli_overrides: dict[str, object],
    check_config: bool,
) -> int | None:
    """Handle --check-config flag.

    Args:
        config: Path to config file.
        cli_overrides: CLI overrides dict.
        check_config: Whether flag was set.

    Returns:
        Exit code or None to continue.
    """
    if not check_config:
        return None
    try:
        config_path = _config_path(config)
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        console.print("[green]Configuration is valid[/green]")
        return 0
    except Exception as e:
        logger.error("Configuration is invalid: {}", e)
        return 1


def _handle_check_mcp(check_mcp: bool) -> int | None:
    """Handle --check-mcp flag.

    Runs the same startup validation the pipeline runs, without starting
    the pipeline itself. Returns 0 when every custom MCP server + agent
    transport probe succeeds, 1 otherwise. When no ``mcp.toml`` is
    configured, validation is a no-op and returns 0.
    """
    if not check_mcp:
        return None
    from ralph.pipeline import runner as runner_module  # noqa: PLC0415

    try:
        workspace_scope = resolve_workspace_scope()
        rc = runner_module._validate_custom_mcp_servers(workspace_scope.root)
    except Exception as e:
        logger.error("MCP validation failed: {}", e)
        return 1
    if rc == 0:
        console.print("[green]MCP servers validated successfully[/green]")
    else:
        console.print("[red]MCP validation failed — see logs[/red]")
    return rc


def _handle_commit_plumbing(
    options: CommitPlumbingOptions,
) -> int | None:
    """Handle commit plumbing commands.

    Args:
        options: Commit plumbing options.

    Returns:
        Exit code or None to continue.
    """
    if not (
        options.generate_commit_msg
        or options.apply_commit
        or options.generate_commit
        or options.show_commit_msg
    ):
        return None

    commit_plumbing(options=options)
    return 0


def _run_pipeline(  # noqa: PLR0913
    config: str | None,
    cli_overrides: dict[str, object],
    dry_run: bool,
    resume: bool,
    no_resume: bool,
    verbosity: Verbosity = Verbosity.VERBOSE,
) -> int:
    """Run the main pipeline.

    Args:
        config: Path to config file.
        cli_overrides: CLI overrides dict.
        dry_run: Whether to do dry run.
        resume: Whether to resume.
        no_resume: Whether to ignore checkpoint.

    Returns:
        Exit code.
    """
    try:
        exit_code = run_pipeline(
            config_path=_config_path(config),
            cli_overrides=cli_overrides,
            dry_run=dry_run,
            resume=resume and not no_resume,
            verbosity=verbosity,
        )
        return exit_code
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        return 130
    except Exception as e:
        logger.exception("Pipeline failed: {}")
        console.print(_status_text("Error", str(e), "red"))
        return 1


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _configure_logging(verbosity: Verbosity) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbosity: Verbosity level.
    """
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


def _build_cli_overrides(
    input: CLIOverrideInput,
) -> dict[str, object]:
    """Build CLI overrides dictionary.

    Args:
        input: CLI override input data.

    Returns:
        Dictionary of CLI overrides for config merging.
    """
    overrides: CLIOverrides = {
        "general": {
            "developer_iters": None,
            "reviewer_reviews": None,
            "review_depth": None,
            "git_user_name": None,
            "git_user_email": None,
            "execution": {},
        },
        "developer_agent": None,
        "reviewer_agent": None,
        "developer_model": None,
        "reviewer_model": None,
    }

    if input.developer_iters is not None:
        overrides["general"]["developer_iters"] = input.developer_iters

    if input.reviewer_reviews is not None:
        overrides["general"]["reviewer_reviews"] = input.reviewer_reviews

    if input.developer_agent is not None:
        overrides["developer_agent"] = input.developer_agent

    if input.reviewer_agent is not None:
        overrides["reviewer_agent"] = input.reviewer_agent

    if input.developer_model is not None:
        overrides["developer_model"] = input.developer_model

    if input.reviewer_model is not None:
        overrides["reviewer_model"] = input.reviewer_model

    if input.review_depth is not None:
        overrides["general"]["review_depth"] = input.review_depth.value

    if input.git_user_name is not None:
        overrides["general"]["git_user_name"] = input.git_user_name

    if input.git_user_email is not None:
        overrides["general"]["git_user_email"] = input.git_user_email

    if input.isolation_mode is not None:
        overrides["general"]["execution"]["isolation_mode"] = input.isolation_mode

    return dict(overrides)


if __name__ == "__main__":
    app()
