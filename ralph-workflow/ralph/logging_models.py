"""Data models for Ralph Workflow logging configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from loguru import Logger

    from .logging import RalphLogger


@dataclass(frozen=True)
class LoggingSession:
    """Configured logger bundle for a single Ralph Workflow run."""

    @dataclass(frozen=True)
    class LoggingPaths:
        """Resolved file paths for a configured logging session."""

        run_directory: Path | None
        text_log_path: Path | None
        structured_log_path: Path | None

    @dataclass(frozen=True)
    class LoggingConfig:
        """Logging configuration used to create handlers and run directories."""

        verbosity: int = 1
        log_directory: Path | None = None
        run_id: str | None = None
        structured: bool = False
        rotation: str | int | None = "10 MB"


    config: LoggingConfig
    paths: LoggingPaths
    logger: Logger
    ralph: RalphLogger


LoggingPaths = LoggingSession.LoggingPaths
LoggingConfig = LoggingSession.LoggingConfig


__all__ = ["LoggingConfig", "LoggingPaths", "LoggingSession"]
