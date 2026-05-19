"""ProcessRunOptions — execution options for run_process and run_process_async."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass(frozen=True)
class ProcessRunOptions:
    """Execution options for run_process and run_process_async."""

    cwd: str | Path | None = None
    env: Mapping[str, str] | None = None
    timeout: float | None = None
    capture_output: bool = True


__all__ = ["ProcessRunOptions"]
