"""Per-worker log file metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class WorkerLog:
    """Paths and identifiers for per-worker log files."""

    log_dir: Path
    run_id: str
