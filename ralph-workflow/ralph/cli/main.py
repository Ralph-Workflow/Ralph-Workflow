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
from ralph.cli.commands._commit_plumbing_options import CommitPlumbingOptions
from ralph.cli.commands.cleanup import cleanup
from ralph.cli.commands.contribute import contribute
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
from ralph.policy.loader import load_policy_for_config_dir, load_policy_for_workspace_scope
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
    from ralph.cli._general_overrides import GeneralOverrides
    from ralph.cli.commands.run import RunPipelineRequest
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
    return cast("click.Command", command)


def _get_cli_context() -> DisplayContext:
    return _make_display_context()


_LONG_DEVELOPER_ITERS = 5
_THOROUGH_DEVELOPER_ITERS = 10


def _prepare_init_args(args: Sequence[str] | None) -> list[str] | None:
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
                if not standalone_mode:
                    raise
                exc.show()
                sys.exit(exc.exit_code)

        _set_command_main(command, patched_main)
    return command


object.__setattr__(typer.main, "get_command", _get_command_with_optional_init)
_set_typer_testing_get_command(_get_command_with_optional_init)


def version_callback(version: bool, ctx: DisplayContext | None = None) -> None:
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
    if config is None:
        return None

    return RuntimePath(config)


def resolve_effective_verbosity(
    verbosity: Verbosity,
    *,
    quiet: bool,
    debug: bool,
) -> Verbosity:
    if quiet:
        return Verbosity.QUIET
    if debug:
        return Verbosity.DEBUG
    if verbosity == Verbosity.NORMAL:
        return Verbosity.VERBOSE
    return verbosity


def _try_load_registry() -> AgentRegistry | None:
    try:
        cfg = load_config(None, {})
        registry_type = _load_agent_registry_factory()
        return registry_type.from_config(cfg)
    except Exception:
        return None


def _bootstrap_global_configs(
    *, display_context: DisplayContext, emit_welcome: bool = True
) -> None:
    agents_result = ensure_global_agents_config()
    results = [
        ensure_global_config(),
        agents_result,
        ensure_global_mcp_config(),
        *ensure_global_policy_configs(),
    ]
    if emit_welcome and not agents_result.path.exists():
        display = resolve_active_display(None, display_context)
        display.emit_warning(
            f"Agents configuration is missing: {agents_result.path}. "
            "Run `ralph --init` to create it."
        )
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
    if not init_requested:
        _bootstrap_global_configs_or_exit(
            display_context,
            emit_welcome=not regenerate_config,
        )


def _handle_init(
    *, template: str | None, config: str | None, display_context: DisplayContext
) -> None:
    try:
        from ralph.cli.commands.init import init_command

        init_command(template, _config_path(config), display_context=display_context)
    except ConfigTomlError as exc:
        logger.error(str(exc))
        raise typer.Exit(code=1) from None


def diagnose_command(
    config_path: RuntimePath | None = None,
    cli_overrides: dict[str, object] | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    from ralph.cli.commands.diagnose import diagnose_command as _diagnose_command

    return _diagnose_command(config_path, cli_overrides, display_context=display_context)


def _handle_diagnose(
    *,
    config: str | None,
    cli_overrides: dict[str, object],
    display_context: DisplayContext,
) -> None:
    exit_code = diagnose_command(
        _config_path(config), cli_overrides, display_context=display_context
    )
    raise typer.Exit(code=exit_code)


def _handle_regenerate_config(*, display_context: DisplayContext) -> None:
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
    if version:
        version_callback(version)
    if explain_policy:
        from ralph.cli.commands.explain import explain_command

        policy_dir = RuntimePath(explain_policy_dir) if explain_policy_dir else None
        raise typer.Exit(code=explain_command(policy_dir))
    if check_policy:
        from ralph.cli.commands.check_policy import check_policy_command

        policy_dir = RuntimePath(explain_policy_dir) if explain_policy_dir else None
        raise typer.Exit(code=check_policy_command(policy_dir, counter_overrides=counter_overrides))


def _handle_force_init_skills(*, workspace_root: RuntimePath) -> None:
    from ralph.skills.manager import SkillManager

    display_context = _get_cli_context()
    manager = SkillManager()
    cap_state, failures = manager.reinstall_baseline_skills(workspace_root=workspace_root)
    print_capability_summary(display_context.console, cap_state, workspace_root=workspace_root)
    if failures:
        resolve_active_display(None, display_context).emit_skill_failure_warning(failures)


def _handle_retry_integration(
    config: RuntimePath | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    from ralph.pipeline.auto_integrate import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    scope = WorkspaceScope(root=RuntimePath.cwd())
    cfg = None
    try:
        from ralph.config.loader import load_config

        cfg = load_config(config_path=config, workspace_scope=scope)
    except Exception as exc:
        logger.debug("Config load during explicit retry: {}", exc)

    from ralph.pipeline.auto_integrate_recovery import build_recovery_context

    cr, rs, ctrl = build_recovery_context(scope, cfg, display_context=display_context)

    outcome = recover_incomplete_integration(
        scope,
        config=cfg,
        conflict_resolver=cr,
        rebase_stop_resolver=rs,
        recovery_controller=ctrl,
        force_retry=True,
    )
    if outcome is not None:
        logger.bind(recovery=True).info(
            "Explicit integration retry: outcome={}", outcome.last_outcome
        )
        return 0 if (outcome.fast_forwarded or outcome.last_outcome == "recovered") else 1
    return 0


def _dispatch_maintenance_flags(
    *,
    regenerate_config: bool,
    force_init_skills: bool,
    retry_integration: bool,
    config: str | None,
    display_context: DisplayContext,
) -> None:
    if regenerate_config:
        _handle_regenerate_config(display_context=display_context)
        raise typer.Exit()

    if force_init_skills:
        _handle_force_init_skills(workspace_root=RuntimePath.cwd())
        raise typer.Exit()

    if retry_integration:
        exit_code = _handle_retry_integration(
            config=_config_path(config), display_context=display_context
        )
        raise typer.Exit(code=exit_code)


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
    if not _CLI_RESTORE_STATE.registered:
        snapshot_terminal_modes()
        reg = register_fn if register_fn is not None else atexit.register
        reg(restore_terminal)
        install_sanitizing_excepthook()
        _CLI_RESTORE_STATE.registered = True
    if (
        _CLI_RESTORE_STATE.signals_registered
        or threading.current_thread() is not threading.main_thread()
    ):
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

        def _restore_then_delegate(
            received: int, frame: object, *, previous: object = previous
        ) -> None:
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
    retry_integration: Annotated[
        bool,
        typer.Option(
            "--retry",
            "--retry-integration",
            help="Force retry of retained integration conflict resolution, bypassing the continuation gate",
        ),
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
            help="Internal worker bootstrap manifest path (Deprecated; see docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers').",
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
    if removed_malloc_debug_vars := sanitize_process_environment():
        logger.debug(
            "Stripped inherited malloc-debug environment variables: {}",
            ", ".join(removed_malloc_debug_vars),
        )
    ensure_cli_terminal_restore()

    if ctx is not None and ctx.invoked_subcommand:
        _record_cli_command(ctx)
        return

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

    configure_logging(verbosity, console_sink=make_sanitizing_log_sink(_cli_ctx))
    _bootstrap_for_command(
        init_requested=init is not None or "--init" in sys.argv[1:],
        regenerate_config=regenerate_config,
        display_context=_cli_ctx,
    )
    _init_telemetry()
    _record_cli_command(ctx)

    effective_developer_iters = _resolve_effective_developer_iters(
        quick=quick,
        long_run=long_run,
        thorough=thorough,
        developer_iters=developer_iters,
    )

    counter_overrides = _counter_overrides_with_developer_iters(
        counter_overrides, effective_developer_iters
    )

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
        _handle_diagnose(config=config, cli_overrides=cli_overrides, display_context=_cli_ctx)

    if init is not None:
        _handle_init(template=init, config=config, display_context=_cli_ctx)
        raise typer.Exit()

    _dispatch_maintenance_flags(
        regenerate_config=regenerate_config,
        force_init_skills=force_init_skills,
        retry_integration=retry_integration,
        config=config,
        display_context=_cli_ctx,
    )

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

    from ralph.update_check import maybe_render_update_nag

    maybe_render_update_nag(_cli_ctx)

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
    display = resolve_active_display(None, _get_cli_context())
    result = run_on_demand_judgement(target, intent)
    if result.blocker is not None:
        display.emit_warning(f"visual judgement blocked: {result.blocker}")
        raise typer.Exit(code=1)
    display.emit_status(f"submitted on-demand design verdict {result.verdict_id} ({result.status})")
    display.emit_status("on-demand visual judgement is not a blocking verification gate")


app.command(name="visual-judgement")(visual_judgement)


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
    from ralph.cli.commands.smoke import smoke_interactive_claude_command

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
    from ralph.cli.commands.smoke import smoke_headless_claude_command

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
            "AGY model alias to smoke (e.g. agy/gemini-3.8-flash-low). "
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
    from ralph.cli.commands.smoke import smoke_interactive_agy_command

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
    from ralph.cli.commands.smoke import smoke_interactive_nanocoder_command

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
    from ralph.cli.commands.smoke import smoke_interactive_cursor_command

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
    from ralph.cli.commands.smoke import smoke_interactive_kimi_command

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
    if (provider is None) != (model is None):
        raise click.UsageError("--provider and --model must be given together")
    agent_name = f"opencode/{provider}/{model}" if provider is not None else agent
    from ralph.cli.commands.smoke import smoke_interactive_opencode_command

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
    from ralph.cli.commands.smoke import smoke_interactive_codex_command

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
    from ralph.cli.commands.smoke import smoke_interactive_pi_command

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
    agent: str | None = typer.Option(
        None,
        help="CCS alias to smoke; defaults to the effective policy development chain.",
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
    from ralph.cli.commands.smoke import smoke_interactive_ccs_command

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


def _handle_check_mcp(check_mcp: bool, *, display_context: DisplayContext) -> int | None:
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
    if not (options.generate_commit_msg or options.generate_commit or options.show_commit_msg):
        return None

    from ralph.cli.commands.commit import commit_plumbing

    return commit_plumbing(options=options, display_context=display_context)


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
        from ralph.cli.commands.run import RunPipelineRequest

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
    logger.remove()

    sink = console_sink if console_sink is not None else make_stderr_log_sink()

    if verbosity == Verbosity.QUIET:
        logger.add(sink, level="ERROR")
    elif verbosity in {Verbosity.NORMAL, Verbosity.VERBOSE}:
        logger.add(sink, level="INFO")
    elif verbosity == Verbosity.FULL:
        logger.add(sink, level="DEBUG", format="{time:HH:mm:ss} {level} {message}")
    else:
        logger.add(
            sink,
            level="TRACE",
            format="{time:HH:mm:ss} {level} {name}:{function}:{line} {message}",
        )


def _parse_counter_overrides(raw_entries: list[str]) -> dict[str, int]:
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
    general: GeneralOverrides = {"execution": {}, "workflow": {}}
    overrides: dict[str, object] = {"general": general}

    if input.developer_agent is not None:
        overrides["developer_agent"] = input.developer_agent

    if input.developer_model is not None:
        overrides["developer_model"] = input.developer_model

    if input.git_user_name is not None:
        general["git_user_name"] = input.git_user_name

    if input.git_user_email is not None:
        general["git_user_email"] = input.git_user_email

    if input.developer_iters is not None:
        general["developer_iters"] = input.developer_iters

    if input.unsafe_mode is not None:
        general["workflow"] = {"unsafe_mode": input.unsafe_mode}

    return overrides


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


def __getattr__(name: str) -> object:
    if name == "RunPipelineRequest":
        from ralph.cli.commands.run import RunPipelineRequest

        return RunPipelineRequest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run_pipeline(request: RunPipelineRequest, *, display_context: DisplayContext) -> int:
    from ralph.cli.commands.run import run_pipeline as _impl

    return _impl(request, display_context=display_context)


def _resolve_display_from_legacy(
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> ParallelDisplay:
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
    if not check_config:
        return None
    display = _resolve_display_from_legacy(console=console, display_context=display_context)
    config_path = _config_path(config)
    from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

    workspace_scope: WorkspaceScope | None = None
    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
    except Exception:
        workspace_scope = None

    from ralph.config.bootstrap import collect_unreadable_config_files

    unreadable_files = collect_unreadable_config_files(
        config_path=config_path,
        workspace_scope=workspace_scope,
        display=display,
    )

    validation_errors: list[str] = []
    try:
        config_value = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = _load_agent_registry_factory().from_config(config_value)
        if config_path is not None:
            bundle = load_policy_for_config_dir(config_path.parent, config=config_value)
        else:
            if workspace_scope is None:
                raise RuntimeError("workspace scope is required for the active configuration")
            bundle = load_policy_for_workspace_scope(workspace_scope, config=config_value)
        validate_agent_chains_satisfiable(bundle, registry)
        validate_chain_agents_on_path(bundle.agents)
    except Exception as e:
        validation_errors.append(str(e))

    try:
        from ralph.config.mcp_loader import load_mcp_config

        load_mcp_config(
            workspace_scope=workspace_scope,
            config_path=config_path,
            strict_optional_files=True,
        )
    except (Exception, SystemExit) as e:
        validation_errors.append(str(e))

    if unreadable_files or validation_errors:
        messages: list[str] = [f"{u.path}: {u.error}" for u in unreadable_files]
        for v in validation_errors:
            if not any(v in m for m in messages):
                messages.append(v)
        err_msg = "Configuration is invalid:\n" + "\n".join(f"  - {m}" for m in messages)
        logger.error("{}", err_msg)
        display.emit_warning(err_msg)
        return 1

    display.emit_status("Configuration is valid")
    return 0


if __name__ == "__main__":
    app()
