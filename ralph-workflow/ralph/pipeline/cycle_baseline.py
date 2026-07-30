"""Development-cycle diff baseline management.

The baseline SHA is written once when a dev cycle begins (after planning
succeeds, before any development phase invocation) and is never updated by
mid-cycle commits. It is cleared at cycle boundaries so that the next cycle
starts fresh.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from pathlib import Path

_BASELINE_FILENAME = ".agent/start_commit"


def write_cycle_baseline(
    workspace_root: Path,
    sha: str,
    *,
    force: bool = False,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Record ``sha`` as the diff baseline for the current dev cycle.

    When ``force`` is False (the default), an existing baseline is preserved
    and this call is a no-op. Callers that open a fresh cycle must pass
    ``force=True`` to overwrite any stale baseline.

    The ``backend`` seam defaults to :data:`DEFAULT_FILE_BACKEND` so an
    in-memory backend can exercise the idempotent guard without touching
    real filesystem I/O. ``force`` and ``backend`` are keyword-only so
    existing positional callers and ``runner_module`` monkeypatch spies
    are unaffected.

    A byte-identical rewrite of an existing baseline short-circuits the
    physical write so per-cycle baseline markers do not advance the
    file's mtime or generate an additional fseventsd notification. The
    post-condition "baseline file contains ``sha.strip()+'\\n'``" still
    holds because the fail-open ``write_text_if_changed`` guard falls
    through to a real write on any read uncertainty or content mismatch.
    """
    baseline_path = workspace_root / _BASELINE_FILENAME
    if not force and backend.exists(baseline_path):
        return
    backend.mkdir(baseline_path.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, baseline_path, sha.strip() + "\n")


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
