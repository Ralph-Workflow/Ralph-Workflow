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
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import MCP_STAGE_ID_ENV

#: Default stage-tracker filename component (resolved against the workspace root).
_STAGE_FILE_NAME_COMPONENT: str = ".agent/.ralph_mcp_start_stage"

#: Module-level spawn identity. ``reset_spawn_id()`` regenerates this so a
#: new spawn scoped to the same workspace root gets a new file.
#: If the parent set ``RALPH_MCP_STAGE_ID`` before the child imports this
#: module, the child's spawn identity is taken from that env var (and the
#: module-level UUID fallback is replaced) so the parent and the child
#: address the SAME per-spawn stage file. Without this, the child records
#: under its own import-time UUID and the parent's ``read_last_stage``
#: always returns ``None`` after a preflight failure.
_spawn_id: str = os.environ.get(MCP_STAGE_ID_ENV) or str(uuid.uuid4())


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


def stage_file_path(root: Path, *, spawn_id: str | None = None) -> Path:
    """Return the absolute path to the stage file for the current spawn.

    The path embeds the current spawn identity so two children on the
    same workspace root never share a stage file. The file lives inside
    the workspace because it is per-bridge and must be removed when the
    bridge is rebuilt; an operator can inspect it on a wedged run
    without strace.

    Args:
        root: Workspace root for the MCP server.
        spawn_id: Optional explicit spawn identity. When ``None`` the
            module-global identity is used (correct for the child itself).
            Callers that know the parent's assigned ID can pass it
            directly to attribute a stage file to a specific child.
    """
    sid = spawn_id if spawn_id is not None else _get_spawn_id()
    return root / f"{_STAGE_FILE_NAME_COMPONENT}.{sid}"


def record(root: Path, stage: str) -> None:
    """Atomically write ``stage`` to the per-spawn stage file.

    The label is written to a temporary sibling file and then moved into
    place with ``os.replace``, so a concurrent supervisor reader sees
    either the complete previous label or the complete new label —
    never a blank or truncated file (``os.replace`` is atomic on POSIX
    and Windows). The temp file is fsynced before the rename and the
    directory is fsynced after it, so a SIGKILL of the child after
    ``record()`` returns leaves the latest label on disk.
    """
    path = stage_file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # tempfile.mkstemp already sets O_EXCL; sibling of the final path so
    # os.replace stays on one filesystem. Mode 0o644 for operator reads.
    fd, tmp_name = tempfile.mkstemp(  # filesystem-write-ok: temp sibling immediately atomically replaced via os.replace — the only durable artifact is the final stage file
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, stage.encode("ascii"))
            # Flush + fsync the temp file so the kernel cannot reorder a
            # subsequent SIGKILL before the bytes hit disk.
            os.fsync(  # resource-lifecycle-ok: atomic-replace boundary — durability barrier so SIGKILL cannot lose the stage label before disk  # filesystem-write-ok: durability barrier for the per-spawn stage label — fd-level fsync before rename so the supervisor preflight read cannot miss the stage across a child SIGKILL
                fd
            )
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
        # fsync the directory so the rename itself is durable.
        dir_fd = os.open(  # resource-lifecycle-ok: directory fsync immediately closed in finally  # filesystem-write-ok: durability barrier for the rename so the replaced stage file survives a child SIGKILL
            path.parent, os.O_RDONLY
        )
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        # A failed mid-write must leave the previous stage file intact:
        # clean up the temp sibling so no stale ``.tmp`` clutter remains.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def read_last_stage(root: Path, *, spawn_id: str | None = None) -> str | None:
    """Return the last stage label written by the current spawn, or ``None``.

    ``None`` is returned when the file is absent (the child was
    killed before the first stage flush, or the runtime does not
    implement the stage-tracker contract). The supervisor falls back
    to ``"last_stage_unknown"`` on ``None`` so the operator always
    sees a stable token in the surfaced error.

    Because the path includes the spawn identity, reading a file written
    by a *different* spawn returns ``None`` — the predecessor's stage file
    is unreachable by the current spawn's path.

    Args:
        root: Workspace root for the MCP server.
        spawn_id: Optional explicit spawn identity. When ``None`` the
            module-global identity is used. Pass the parent's assigned
            ID to read a specific child's stage file from the parent
            process.
    """
    path = stage_file_path(root, spawn_id=spawn_id)
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
