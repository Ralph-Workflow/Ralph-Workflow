"""_StartStageTracker — visible, flushed startup stage recorder for the MCP child.

The child writes the most recent completed startup stage to a
dedicated file the supervisor reads on preflight failure. The file
is keyed by FD and uses an atomic ``O_CREAT | O_TRUNC | O_WRONLY``
write so a concurrent reader always sees a complete stage label, not
a partial one. The tracker is intentionally minimal: one constant
filename, one ``record(stage)`` function, and a ``read_last_stage``
helper for the supervisor. No mutable in-memory state survives the
import — the file is the source of truth.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: Default stage-tracker filename (resolved against the workspace root).
_STAGE_FILE_NAME: str = ".agent/.ralph_mcp_start_stage"


def stage_file_path(root: Path) -> Path:
    """Return the absolute path to the stage file under ``root``.

    The file lives inside the workspace because it is per-bridge and
    must be removed when the bridge is rebuilt. The path is
    deliberately below the agent dir so an operator can inspect it
    on a wedged run without strace.
    """
    return root / _STAGE_FILE_NAME


def record(root: Path, stage: str) -> None:
    """Atomically write ``stage`` to the per-bridge stage file.

    The file is opened with ``O_CREAT | O_TRUNC | O_WRONLY`` and
    flushed before close so a SIGKILL of the child after this point
    leaves the latest label on disk for the supervisor. The label is
    plain ASCII — the bound stage vocabulary is closed and
    introspectable; the validator in
    :mod:`ralph.testing.audit_resource_lifecycle` pins the contract.
    """
    path = stage_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``O_TRUNC`` is required so a stale label from a previous
    # child on the same port does not survive a fast respawn.
    fd = os.open(  # filesystem-write-ok: small per-bridge startup-stage file, lifecycle-bounded
        path,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o644,
    )
    try:
        os.write(fd, stage.encode("ascii"))
        # Flush + fsync so the kernel cannot reorder a subsequent
        # SIGKILL before the bytes hit disk.
        os.fsync(fd)
    finally:
        os.close(fd)


def read_last_stage(root: Path) -> str | None:
    """Return the last stage label written by the child, or ``None``.

    ``None`` is returned when the file is absent (the child was
    killed before the first stage flush, or the runtime does not
    implement the stage-tracker contract). The supervisor falls back
    to ``"last_stage_unknown"`` on ``None`` so the operator always
    sees a stable token in the surfaced error.
    """
    path = stage_file_path(root)
    try:
        return path.read_text(encoding="ascii").strip() or None
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return None


# Closed vocabulary of the six flushed startup stages the child logs.
# Listed here so the test that asserts the contract can compare
# against this tuple without redefining it; the production runtime
# logs them in the same order.
RUNTIME_STAGES: tuple[str, ...] = (
    "env_loaded",
    "session_ready",
    "config_loaded",
    "upstream_catalog_or_discovery_done",
    "registry_built",
    "ready_to_serve",
)


__all__ = [
    "RUNTIME_STAGES",
    "read_last_stage",
    "record",
    "stage_file_path",
]
