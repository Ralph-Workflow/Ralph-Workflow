"""Logging configuration for Ralph Workflow.

This module configures loguru for structured logging throughout the Ralph Workflow CLI.
Log levels map to verbosity as follows::

    0 (QUIET)  -> ERROR only
    1 (NORMAL) -> WARNING
    2 (VERBOSE) -> INFO
    3 (FULL)   -> DEBUG
    4+ (DEBUG) -> TRACE

Custom levels registered on first configure_logging() call::

    SUCCESS (25): between INFO (20) and WARNING (30)
    MILESTONE (35): between WARNING (30) and ERROR (40)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

from .logging_models import LoggingConfig, LoggingPaths, LoggingSession
from .logging_worker_sink import WorkerSinkHandle, bind_worker_sink, remove_worker_sink

# Verbosity level to loguru minimum level
_VERBOSITY_LEVELS = {
    0: "ERROR",
    1: "WARNING",
    2: "INFO",
    3: "DEBUG",
    4: "TRACE",
}

_CUSTOM_LEVELS: tuple[tuple[str, int, str], ...] = (
    ("SUCCESS", 25, "<green>"),
    ("MILESTONE", 35, "<cyan>"),
)


def _ensure_custom_levels() -> None:
    for name, no, color in _CUSTOM_LEVELS:
        try:
            logger.level(name)
        except ValueError:
            logger.level(name, no=no, color=color)


def configure_logging(
    verbosity: int = 1,
    *,
    log_directory: str | Path | None = None,
    run_id: str | None = None,
    structured: bool = False,
    rotation: str | int | None = "10 MB",
) -> LoggingSession:
    """Configure loguru for Ralph Workflow CLI output.

    Removes the default handler and adds a new handler with formatting
    based on verbosity level. Higher verbosity shows more detail.

    Args:
        verbosity: Verbosity level (0=quiet/errors only, 1=normal, 2=verbose,
            3=debug, 4+=trace).
        log_directory: Optional base directory for file logging.
        run_id: Optional run identifier for per-run log directories.
        structured: Whether to emit JSON structured logs.
        rotation: Optional loguru rotation policy for file handlers.

    Returns:
        Logging session with resolved paths and bound logger helpers.
    """
    config = LoggingConfig(
        verbosity=verbosity,
        log_directory=Path(log_directory) if log_directory is not None else None,
        run_id=run_id,
        structured=structured,
        rotation=rotation,
    )

    logger.remove()
    _ensure_custom_levels()

    level = _VERBOSITY_LEVELS.get(verbosity, "TRACE")
    bound_logger = logger.bind(**_build_base_extra(run_id))

    standard_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stderr,
        level=level,
        format=standard_format,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    paths = _configure_file_handlers(config, level)
    session = LoggingSession(
        config=config,
        paths=paths,
        logger=bound_logger,
        ralph=RalphLogger(bound_logger),
    )

    session.logger.debug("Logging configured at {level} level", level=level)
    return session


def _build_base_extra(run_id: str | None) -> dict[str, str]:
    if run_id is None:
        return {}
    return {"run_id": run_id}


def _configure_file_handlers(config: LoggingConfig, level: str) -> LoggingPaths:
    log_directory = config.log_directory
    if log_directory is None:
        return LoggingPaths(
            run_directory=None,
            text_log_path=None,
            structured_log_path=None,
        )

    run_directory = log_directory / config.run_id if config.run_id else log_directory
    run_directory.mkdir(parents=True, exist_ok=True)

    text_log_path = run_directory / "ralph.log"
    logger.add(
        text_log_path,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        colorize=False,
        backtrace=True,
        diagnose=False,
        rotation=config.rotation,
    )

    structured_log_path: Path | None = None
    if config.structured:
        structured_log_path = run_directory / "ralph.jsonl"
        assert structured_log_path is not None
        logger.add(
            structured_log_path,
            level=level,
            serialize=True,
            backtrace=True,
            diagnose=False,
            rotation=config.rotation,
            buffering=8192,
        )

    return LoggingPaths(
        run_directory=run_directory,
        text_log_path=text_log_path,
        structured_log_path=structured_log_path,
    )


def get_logger() -> Logger:
    """Get the configured ralph logger.

    Returns:
        The loguru logger instance.
    """
    return logger


class RalphLogger:
    """Structured logger for Ralph Workflow pipeline events.

    This class provides convenient methods for common logging scenarios
    in the Ralph Workflow pipeline.
    """

    def __init__(self, base_logger: Logger | None = None) -> None:
        """Initialize the Ralph Workflow logger."""
        self._logger: Logger = base_logger if base_logger is not None else logger

    def phase_start(self, phase: str, drain: str) -> None:
        """Log the start of a pipeline phase.

        Args:
            phase: Phase name.
            drain: Drain name.
        """
        self._logger.bind(event="phase_start", phase=phase, drain=drain).info(
            "Starting phase '{phase}' on drain '{drain}'",
            phase=phase,
            drain=drain,
        )

    def phase_complete(self, phase: str, drain: str) -> None:
        """Log the completion of a pipeline phase.

        Args:
            phase: Phase name.
            drain: Drain name.
        """
        self._logger.bind(event="phase_complete", phase=phase, drain=drain).info(
            "Completed phase '{phase}' on drain '{drain}'",
            phase=phase,
            drain=drain,
        )

    def agent_invoked(self, agent_name: str, drain: str) -> None:
        """Log agent invocation.

        Args:
            agent_name: Name of the agent being invoked.
            drain: Drain name.
        """
        self._logger.bind(event="agent_invoked", agent=agent_name, drain=drain).debug(
            "Invoking agent '{agent}' for drain '{drain}'",
            agent=agent_name,
            drain=drain,
        )

    def agent_output(self, drain: str, line: str) -> None:
        """Log agent output line.

        Args:
            drain: Drain name.
            line: Output line from agent.
        """
        self._logger.bind(event="agent_output", drain=drain).debug(
            "agent_output | drain={drain} | line={line}",
            drain=drain,
            line=line,
        )

    def checkpoint_saved(self, path: str) -> None:
        """Log checkpoint save.

        Args:
            path: Path to checkpoint file.
        """
        self._logger.bind(event="checkpoint_saved", path=path).debug(
            "Checkpoint saved to '{path}'",
            path=path,
        )

    def checkpoint_loaded(self, path: str) -> None:
        """Log checkpoint load.

        Args:
            path: Path to checkpoint file.
        """
        self._logger.bind(event="checkpoint_loaded", path=path).debug(
            "Checkpoint loaded from '{path}'",
            path=path,
        )

    def policy_loaded(self, config_dir: str) -> None:
        """Log policy load.

        Args:
            config_dir: Configuration directory path.
        """
        self._logger.bind(event="policy_loaded", config_dir=config_dir).info(
            "Policy loaded from '{config_dir}'",
            config_dir=config_dir,
        )

    def validation_error(self, error: str) -> None:
        """Log validation error.

        Args:
            error: Error message.
        """
        self._logger.bind(event="validation_error").error("Validation error: {error}", error=error)

    def pipeline_error(self, phase: str, error: str) -> None:
        """Log pipeline error.

        Args:
            phase: Current phase name.
            error: Error message.
        """
        self._logger.bind(event="pipeline_error", phase=phase).error(
            "Pipeline error in phase '{phase}': {error}",
            phase=phase,
            error=error,
        )

    def success(self, message: str, **extra: object) -> None:
        self._logger.bind(**extra).log("SUCCESS", message)

    def milestone(self, message: str, **extra: object) -> None:
        self._logger.bind(**extra).log("MILESTONE", message)


__all__ = [
    "RalphLogger",
    "WorkerSinkHandle",
    "bind_worker_sink",
    "configure_logging",
    "get_logger",
    "remove_worker_sink",
]
