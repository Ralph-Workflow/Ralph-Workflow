"""LoggingConfig — logging configuration for a Ralph Workflow run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class LoggingConfig:
    """Logging configuration used to create handlers and run directories."""

    verbosity: int = 1
    log_directory: Path | None = None
    run_id: str | None = None
    structured: bool = False
    rotation: str | int | None = "10 MB"


__all__ = ["LoggingConfig"]
