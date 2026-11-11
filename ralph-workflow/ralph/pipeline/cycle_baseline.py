
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
    baseline_path = workspace_root / _BASELINE_FILENAME
    if not force and backend.exists(baseline_path):
        return
    write_text_if_changed(
        backend,
        baseline_path,
        sha.strip() + "\n",
        prepare_write=lambda: backend.mkdir(baseline_path.parent, parents=True, exist_ok=True),
    )


def read_cycle_baseline(workspace_root: Path) -> str | None:
    baseline_path = workspace_root / _BASELINE_FILENAME
    try:
        if not baseline_path.exists():
            return None
        sha = baseline_path.read_text(encoding="utf-8").strip()
        return sha if sha else None
    except OSError:
        return None


def clear_cycle_baseline(workspace_root: Path) -> None:
    baseline_path = workspace_root / _BASELINE_FILENAME
    with suppress(OSError):
        baseline_path.unlink(missing_ok=True)
