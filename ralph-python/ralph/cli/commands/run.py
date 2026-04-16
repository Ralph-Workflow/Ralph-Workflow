"""Run pipeline command for Ralph CLI.

This module implements the main pipeline execution command.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger
from rich.text import Text

from ralph.config.loader import load_config
from ralph.pipeline import checkpoint as ckpt
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.state import PipelineState


class _RunnerFunc(Protocol):
    def __call__(self, config: UnifiedConfig, initial_state: PipelineState | None) -> int: ...


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


def run_pipeline(
    config_path: Path | None = None,
    cli_overrides: ConfigOverrides | None = None,
    dry_run: bool = False,
    resume: bool = False,
) -> int:
    """Run the Ralph pipeline.

    Args:
        config_path: Path to configuration file.
        cli_overrides: CLI flag overrides for config.
        dry_run: If True, run without invoking agents.
        resume: If True, resume from checkpoint.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    # Load configuration
    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
    except Exception as e:
        logger.error("Failed to load configuration: {}", e)
        return 1

    # Check for checkpoint if resume is requested
    initial_state: PipelineState | None = None
    if resume:
        initial_state = ckpt.load()
        if initial_state is None:
            console.print("[yellow]No checkpoint found to resume from[/yellow]")
            resume = False

    # In dry-run mode, just initialize and exit
    if dry_run:
        console.print("[cyan]Dry run mode[/cyan]")
        console.print(_detail_text("Phase", initial_state.phase if initial_state else "planning"))
        console.print(_detail_text("Iterations", str(config.general.developer_iters)))
        console.print(_detail_text("Review passes", str(config.general.reviewer_reviews)))
        return 0

    # Run the actual pipeline
    if _run_func is None:
        logger.error("Pipeline runner is unavailable")
        console.print("[red]Pipeline runner is unavailable[/red]")
        return 1

    try:
        exit_code = _run_func(config, initial_state)
        return exit_code
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        # Save checkpoint on interrupt
        if initial_state is not None:
            update_data: ConfigOverrides = {"interrupted_by_user": True}
            interrupted_state = initial_state.model_copy(update=update_data)
            ckpt.save(interrupted_state)
        return 130
    except Exception as e:
        logger.exception("Pipeline execution failed: {}")
        console.print(_status_text("Pipeline failed", str(e), "red"))
        return 1


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
