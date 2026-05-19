"""Data models for Ralph Workflow logging configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph._logging_config import LoggingConfig
from ralph._logging_paths import LoggingPaths

if TYPE_CHECKING:
    from loguru import Logger

    from .logging import RalphLogger


@dataclass(frozen=True)
class LoggingSession:
    """Configured logger bundle for a single Ralph Workflow run."""

    config: LoggingConfig
    paths: LoggingPaths
    logger: Logger
    ralph: RalphLogger


__all__ = ["LoggingConfig", "LoggingPaths", "LoggingSession"]
