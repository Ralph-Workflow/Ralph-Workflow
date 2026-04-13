"""Logging configuration for Ralph.

This module configures loguru for structured logging throughout the Ralph CLI.
Log levels map to verbosity as follows:
    0 (QUIET)  -> ERROR only
    1 (NORMAL) -> WARNING
    2 (VERBOSE) -> INFO
    3 (FULL)   -> DEBUG
    4+ (DEBUG) -> TRACE
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

# Verbosity level to loguru minimum level
_VERBOSITY_LEVELS = {
    0: "ERROR",
    1: "WARNING",
    2: "INFO",
    3: "DEBUG",
    4: "TRACE",
}


def configure_logging(verbosity: int = 1) -> None:
    """Configure loguru for Ralph CLI output.

    Removes the default handler and adds a new handler with formatting
    based on verbosity level. Higher verbosity shows more detail.

    Args:
        verbosity: Verbosity level (0=quiet/errors only, 1=normal, 2=verbose,
            3=debug, 4+=trace).
    """
    # Remove default handler
    logger.remove()

    # Determine log level from verbosity
    level = _VERBOSITY_LEVELS.get(verbosity, "TRACE")

    # Standard output format (without color for portability)
    standard_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # Add console handler
    logger.add(
        sys.stderr,
        level=level,
        format=standard_format,
        colorize=True,
        backtrace=True,
        diagnose=False,
    )

    # Log startup message
    logger.debug("Logging configured at {level} level", level=level)


def get_logger() -> Logger:
    """Get the configured ralph logger.

    Returns:
        The loguru logger instance.
    """
    return logger


class RalphLogger:
    """Structured logger for Ralph pipeline events.

    This class provides convenient methods for common logging scenarios
    in the Ralph pipeline.
    """

    def __init__(self) -> None:
        """Initialize the Ralph logger."""
        self._logger: Logger = logger

    def phase_start(self, phase: str, drain: str) -> None:
        """Log the start of a pipeline phase.

        Args:
            phase: Phase name.
            drain: Drain name.
        """
        self._logger.info("Starting phase '{phase}' on drain '{drain}'", phase=phase, drain=drain)

    def phase_complete(self, phase: str, drain: str) -> None:
        """Log the completion of a pipeline phase.

        Args:
            phase: Phase name.
            drain: Drain name.
        """
        self._logger.info("Completed phase '{phase}' on drain '{drain}'", phase=phase, drain=drain)

    def agent_invoked(self, agent_name: str, drain: str) -> None:
        """Log agent invocation.

        Args:
            agent_name: Name of the agent being invoked.
            drain: Drain name.
        """
        self._logger.debug(
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
        self._logger.debug("agent_output | drain={drain} | line={line}", drain=drain, line=line)

    def checkpoint_saved(self, path: str) -> None:
        """Log checkpoint save.

        Args:
            path: Path to checkpoint file.
        """
        self._logger.debug("Checkpoint saved to '{path}'", path=path)

    def checkpoint_loaded(self, path: str) -> None:
        """Log checkpoint load.

        Args:
            path: Path to checkpoint file.
        """
        self._logger.debug("Checkpoint loaded from '{path}'", path=path)

    def policy_loaded(self, config_dir: str) -> None:
        """Log policy load.

        Args:
            config_dir: Configuration directory path.
        """
        self._logger.info("Policy loaded from '{config_dir}'", config_dir=config_dir)

    def validation_error(self, error: str) -> None:
        """Log validation error.

        Args:
            error: Error message.
        """
        self._logger.error("Validation error: {error}", error=error)

    def pipeline_error(self, phase: str, error: str) -> None:
        """Log pipeline error.

        Args:
            phase: Current phase name.
            error: Error message.
        """
        self._logger.error(
            "Pipeline error in phase '{phase}': {error}",
            phase=phase,
            error=error,
        )
