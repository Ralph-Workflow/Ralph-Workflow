"""Development-cycle diff baseline management.

The baseline SHA is written once when a dev cycle begins (after planning
succeeds, before any development phase invocation) and is never updated by
mid-cycle commits. It is cleared at cycle boundaries so that the next cycle
starts fresh.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_BASELINE_FILENAME = ".agent/start_commit"


def write_cycle_baseline(workspace_root: Path, sha: str) -> None:
    """Record ``sha`` as the diff baseline for the current dev cycle."""
    baseline_path = workspace_root / _BASELINE_FILENAME
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(sha.strip() + "\n", encoding="utf-8")


def read_cycle_baseline(workspace_root: Path) -> str | None:
    """Return the recorded baseline SHA, or None if no baseline is set."""
    baseline_path = workspace_root / _BASELINE_FILENAME
    try:
        if not baseline_path.exists():
            return None
        sha = baseline_path.read_text(encoding="utf-8").strip()
        return sha if sha else None
    except OSError:
        return None


def clear_cycle_baseline(workspace_root: Path) -> None:
    """Remove the baseline file so the next cycle starts fresh."""
    baseline_path = workspace_root / _BASELINE_FILENAME
    with suppress(OSError):
        baseline_path.unlink(missing_ok=True)
