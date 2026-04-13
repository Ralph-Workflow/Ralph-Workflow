"""Run pipeline command for Ralph CLI.

This module implements the main pipeline execution command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from rich.console import Console

from ralph.config.loader import load_config
from ralph.pipeline import checkpoint as ckpt

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState

# Late import to avoid circular dependency
try:
    from ralph.pipeline.runner import run as _run_func
except ImportError:
    _run_func = None  # type: ignore[assignment]

console = Console()


def run_pipeline(
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
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
        config = load_config(config_path, cli_overrides)
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
        console.print(f"  Phase: {initial_state.phase if initial_state else 'planning'}")
        console.print(f"  Iterations: {config.general.developer_iters}")
        console.print(f"  Review passes: {config.general.reviewer_reviews}")
        return 0

    # Run the actual pipeline
    if _run_func is None:
        console.print("[red]Pipeline runner not available[/red]")
        return 1
    try:
        exit_code = _run_func(config, initial_state)
        return exit_code
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        # Save checkpoint on interrupt
        if initial_state is not None:
            ckpt.save(initial_state)
        return 130
    except Exception as e:
        logger.exception("Pipeline execution failed: {}")
        console.print(f"[red]Pipeline failed:[/red] {e}")
        return 1
