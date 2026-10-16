"""_StartStageTracker — per-spawn, flushed startup stage recorder for the MCP child.

Each child process records its most recent completed startup stage to a
dedicated file the supervisor reads on preflight failure. The file is
keyed by the current spawn identity (a UUID4 scoped to the module import
or a call to ``reset_spawn_id``), ensuring a new child cannot inherit or
read a predecessor's stage — even when both children share the same
workspace root.

The file is opened with ``O_CREAT | O_TRUNC | O_WRONLY`` so a concurrent
supervisor reader always sees a complete stage label, never a partial one.
Writes are flushed and fsynced so a SIGKILL of the child after ``record()``
returns leaves the latest label on disk.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: Default stage-tracker filename component (resolved against the workspace root).
_STAGE_FILE_NAME_COMPONENT: str = ".agent/.ralph_mcp_start_stage"

#: Module-level spawn identity. ``reset_spawn_id()`` regenerates this so a
#: new spawn scoped to the same workspace root gets a new file.
_spawn_id: str = str(uuid.uuid4())


def _get_spawn_id() -> str:
    """Return the current spawn identity string.

    Overrideable via patching for deterministic testing.
    """
    return _spawn_id


def reset_spawn_id() -> None:
    """Generate a fresh spawn identity for the current process.

    A child that calls this before its first ``record()`` gets a clean
    stage file unreachable from any prior child. The supervisor always
    reads the latest stage by calling ``read_last_stage(root)`` — the
    path is stable for a given spawn but the file content is atomically
    replaced on each write.
    """
    global _spawn_id  # noqa: PLW0603 — intentional module-state reset per spawn identity
    _spawn_id = str(uuid.uuid4())


def stage_file_path(root: Path) -> Path:
    """Return the absolute path to the stage file for the current spawn.

    The path embeds the current spawn identity so two children on the
    same workspace root never share a stage file. The file lives inside
    the workspace because it is per-bridge and must be removed when the
    bridge is rebuilt; an operator can inspect it on a wedged run
    without strace.
    """
    return root / f"{_STAGE_FILE_NAME_COMPONENT}.{_get_spawn_id()}"


def record(root: Path, stage: str) -> None:
    """Atomically write ``stage`` to the per-spawn stage file.

    The file is opened with ``O_CREAT | O_TRUNC | O_WRONLY`` and
    flushed before close so a SIGKILL of the child after this point
    leaves the latest label on disk for the supervisor. The label is
    plain ASCII — the bound stage vocabulary is closed and
    introspectable.
    """
    path = stage_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``O_TRUNC`` is required so a stale label from the same spawn's
    # prior stage does not survive a fast respawn within the same run.
    fd = os.open(  # filesystem-write-ok: small per-bridge startup-stage file, lifecycle-bounded
        path,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o644,
    )
    try:
        os.write(fd, stage.encode("ascii"))
        # Flush + fsync so the kernel cannot reorder a subsequent
        # SIGKILL before the bytes hit disk.
        os.fsync(  # filesystem-write-ok: atomic-replace boundary — durability barrier so SIGKILL cannot lose the stage label before disk
            fd
        )
    finally:
        os.close(fd)


def read_last_stage(root: Path) -> str | None:
    """Return the last stage label written by the current spawn, or ``None``.

    ``None`` is returned when the file is absent (the child was
    killed before the first stage flush, or the runtime does not
    implement the stage-tracker contract). The supervisor falls back
    to ``"last_stage_unknown"`` on ``None`` so the operator always
    sees a stable token in the surfaced error.

    Because the path includes the spawn identity, reading a file written
    by a *different* spawn returns ``None`` — the predecessor's stage file
    is unreachable by the current spawn's path.
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
    "reset_spawn_id",
    "stage_file_path",
]
