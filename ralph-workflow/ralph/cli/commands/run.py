"""Run pipeline command for Ralph CLI.

This module implements the main pipeline execution command.
"""

from __future__ import annotations

import importlib
from inspect import signature
from typing import TYPE_CHECKING, NamedTuple, Protocol, cast

from loguru import logger
from rich.text import Text

from ralph.config.loader import load_config
from ralph.pipeline import checkpoint as ckpt
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState


class _RunnerFunc(Protocol):
    def __call__(
        self,
        config: UnifiedConfig,
        initial_state: PipelineState | None,
        **kwargs: object,
    ) -> int: ...


# Late import to avoid circular dependency
try:
    from ralph.pipeline.runner import run as _imported_run_func
except ImportError:
    _run_func: _RunnerFunc | None = None
else:
    _run_func = cast("_RunnerFunc", _imported_run_func)


class _FallbackConsole:
    def print(self, *args: object, **kwargs: object) -> None:
        return None


class _ConsoleLike(Protocol):
    def print(self, *args: object, **kwargs: object) -> None: ...


ConfigOverrides = dict[str, object]


def _create_console() -> _ConsoleLike:
    try:
        console_module = importlib.import_module("rich.console")
    except ModuleNotFoundError:
        return _FallbackConsole()

    return cast("_ConsoleLike", console_module.Console())


console: _ConsoleLike = _create_console()


# Exit codes
_EXIT_SUCCESS = 0
_EXIT_CONFIG_ERROR = 1
_EXIT_INTERRUPT = 130


class _LoadResult(NamedTuple):
    config: UnifiedConfig
    workspace_scope: WorkspaceScope | None
    initial_state: PipelineState | None


def run_pipeline(
    config_path: Path | None = None,
    cli_overrides: ConfigOverrides | None = None,
    dry_run: bool = False,
    resume: bool = False,
    verbosity: Verbosity | None = None,
) -> int:
    """Run the Ralph pipeline.

    Args:
        config_path: Path to configuration file.
        cli_overrides: CLI flag overrides for config.
        dry_run: If True, run without invoking agents.
        resume: If True, resume from checkpoint.
        verbosity: Optional explicit verbosity passed through to the runner.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    # Phase 1: Load configuration
    load_result = _load_configuration(config_path, cli_overrides, resume)
    if isinstance(load_result, int):
        return load_result

    # Phase 2: Handle dry-run
    if dry_run:
        _print_dry_run(load_result.initial_state, load_result.config)
        return _EXIT_SUCCESS

    # Phase 3: Execute pipeline
    return _execute_pipeline(load_result.config, load_result.initial_state, verbosity)


def _load_configuration(
    config_path: Path | None,
    cli_overrides: ConfigOverrides | None,
    resume: bool,
) -> _LoadResult | int:
    """Load configuration and resolve workspace scope.

    Returns:
        _LoadResult on success, or int error code on failure.
    """
    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        logger.error("Failed to load configuration: {}", e)
        return _EXIT_CONFIG_ERROR

    initial_state: PipelineState | None = None
    if resume:
        initial_state = ckpt.load()
        if initial_state is None:
            console.print("[yellow]No checkpoint found to resume from[/yellow]")

    return _LoadResult(config=config, workspace_scope=workspace_scope, initial_state=initial_state)


def _print_dry_run(initial_state: PipelineState | None, config: UnifiedConfig) -> None:
    """Print dry-run information."""
    console.print("[cyan]Dry run mode[/cyan]")
    console.print(_detail_text("Phase", initial_state.phase if initial_state else "planning"))
    console.print(_detail_text("Iterations", str(config.general.developer_iters)))
    console.print(_detail_text("Review passes", str(config.general.reviewer_reviews)))


def _execute_pipeline(
    config: UnifiedConfig,
    initial_state: PipelineState | None,
    verbosity: Verbosity | None,
) -> int:
    """Execute the pipeline.

    Returns:
        Exit code from pipeline runner.
    """
    if _run_func is None:
        logger.error("Pipeline runner is unavailable")
        console.print("[red]Pipeline runner is unavailable[/red]")
        return _EXIT_CONFIG_ERROR

    try:
        kwargs: dict[str, object] = {}
        if verbosity is not None and "verbosity" in signature(_run_func).parameters:
            kwargs["verbosity"] = verbosity
        return _run_func(config, initial_state, **kwargs)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        if initial_state is not None:
            _save_interrupt_checkpoint(initial_state)
        return _EXIT_INTERRUPT
    except Exception as e:
        logger.exception("Pipeline execution failed: {}")
        console.print(_status_text("Pipeline failed", str(e), "red"))
        return _EXIT_CONFIG_ERROR


def _save_interrupt_checkpoint(initial_state: PipelineState) -> None:
    """Save checkpoint on interrupt."""
    try:
        update_data: ConfigOverrides = {"interrupted_by_user": True}
        interrupted_state = initial_state.model_copy(update=update_data)
        ckpt.save(interrupted_state)
    except Exception:
        logger.warning("Checkpoint save failed during interrupt", exc_info=True)


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def _detail_text(label: str, detail: str) -> Text:
    text = Text()
    text.append(f"  {label}: ")
    text.append(detail)
    return text
