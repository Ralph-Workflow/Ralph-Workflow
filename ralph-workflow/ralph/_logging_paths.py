"""LoggingPaths — resolved file paths for a configured logging session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class LoggingPaths:
    """Resolved file paths for a configured logging session."""

    run_directory: Path | None
    text_log_path: Path | None
    structured_log_path: Path | None


__all__ = ["LoggingPaths"]
