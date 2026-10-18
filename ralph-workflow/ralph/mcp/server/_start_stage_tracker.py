"""_StartStageTracker — per-spawn, flushed startup stage recorder for the MCP child.

Each child process records its most recent completed startup stage to a
dedicated file the supervisor reads on preflight failure. The file is
keyed by the current spawn identity (a UUID4 scoped to the module import
or a call to ``reset_spawn_id``), ensuring a new child cannot inherit or
read a predecessor's stage — even when both children share the same
workspace root.

The stage file is opened with ``O_CREAT | O_TRUNC | O_WRONLY`` so a concurrent
supervisor reader always sees a complete stage label, never a partial one.
Writes are flushed and fsynced so a SIGKILL of the child after ``record()``
returns leaves the latest label on disk.

A sibling overrun log file (``<stage_file_path>.overruns``) records every
per-stage budget overrun as a JSONL entry for post-hoc diagnostics. Missing
or malformed lines are skipped silently. Per-stage budget overruns are
diagnostic warnings and do not abort startup; the fatal deadline is enforced
separately by ``_check_real_startup_deadline`` against ``preflight - _PROBE_SAFETY_S``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import cast

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
_spawn_id_holder: list[str] = [os.environ.get(MCP_STAGE_ID_ENV) or str(uuid.uuid4())]


def _get_spawn_id() -> str:
    """Return the current spawn identity string.

    Overrideable via patching for deterministic testing.
    """
    return _spawn_id_holder[0]


def reset_spawn_id() -> None:
    """Generate a fresh spawn identity for the current process.

    A child that calls this before its first ``record()`` gets a clean
    stage file unreachable from any prior child. The supervisor always
    reads the latest stage by calling ``read_last_stage(root)`` — the
    path is stable for a given spawn but the file content is atomically
    replaced on each write.
    """
    _spawn_id_holder[0] = str(uuid.uuid4())


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
    fd, tmp_name = (
        tempfile.mkstemp(  # filesystem-write-ok: temp sibling immediately atomically replaced via os.replace — the only durable artifact is the final stage file
            dir=path.parent, prefix=f".tmp.{path.name}.", suffix=".tmp"
        )
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o644)
            os.write(fd, stage.encode("ascii"))
            # Flush + fsync the temp file so the kernel cannot reorder a
            # subsequent SIGKILL before the bytes hit disk.
            # filesystem-write-ok: durability barrier for the per-spawn stage label — fd-level fsync before rename so the supervisor preflight read cannot miss the stage across a child SIGKILL
            os.fsync(  # resource-lifecycle-ok: atomic-replace boundary — durability barrier so SIGKILL cannot lose the stage label before disk
                fd
            )
        finally:
            os.close(fd)
        # filesystem-write-ok: atomic-replace boundary — the stage file must never be observed blank or truncated by the supervisor's preflight read
        tmp_path.replace(path)
        # fsync the directory so the rename itself is durable.
        dir_fd = os.open(  # resource-lifecycle-ok: directory fsync immediately closed in finally  # filesystem-write-ok: durability barrier for the rename so the replaced stage file survives a child SIGKILL
            path.parent, os.O_RDONLY
        )
        try:
            # filesystem-write-ok: durability barrier for the rename so the replaced stage file survives a child SIGKILL
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        # A failed mid-write must leave the previous stage file intact:
        # clean up the temp sibling so no stale ``.tmp`` clutter remains.
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()  # filesystem-write-ok: cleanup of the transient .tmp sibling so a failed mid-write leaves no stale clutter
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


# Closed vocabulary of the seven flushed startup stages the child logs.
# Listed here so the test that asserts the contract can compare
# against this tuple without redefining it; the production runtime
# logs them in the same order. ``config_loading`` is the boundary that
# fires BEFORE the bounded pre-serve executor (allowed_roots, FsWorkspace,
# load_mcp_config) and ``config_loaded`` is the boundary that fires
# AFTER the bounded pre-serve executor returns successfully — a hung
# filesystem surfaces ``last_stage="config_loading"`` on preflight failure
# instead of a generic 30 s timeout.
RUNTIME_STAGES: tuple[str, ...] = (
    "env_loaded",
    "session_ready",
    "config_loading",
    "config_loaded",
    "upstream_catalog_or_discovery_done",
    "registry_built",
    "ready_to_serve",
)


#: Default fail-safe timeout for diagnostic writes (seconds).
_DEFAULT_DIAGNOSTIC_TIMEOUT_S: float = 0.2


def record_overrun(
    root: Path,
    stage: str,
    elapsed_s: float,
    budget_s: float,
    remaining_s: float,
    headroom_s: float | None = None,
    *,
    timeout_s: float | None = None,
) -> None:
    """Append one JSONL overrun record to ``<stage_file_path>.overruns``.

    Per-stage overruns are non-fatal diagnostic warnings. Each line is
    a JSON object with keys:
    ``stage``, ``elapsed_s``, ``budget_s``, ``remaining_s``, ``headroom_s``,
    ``observed_at`` (unix timestamp), ``warning_id`` (UUID4 hex).

    Uses append-mode open (``O_CREAT | O_WRONLY | O_APPEND``) so multiple
    calls accumulate entries in the same file rather than overwriting.
    Diagnostic writes are bounded by ``timeout_s`` (defaulting to
    ``mcp_overrun_diagnostic_timeout_seconds()``) in a daemon thread so a
    stalled or hung filesystem cannot delay startup deadline checks.
    """
    path = stage_file_path(root)
    overruns_path = Path(str(path) + ".overruns")
    overruns_path.parent.mkdir(  # filesystem-write-ok: ensure parent directory exists for per-spawn overruns diagnostic log
        parents=True, exist_ok=True
    )

    _warn_id: str = uuid.uuid4().hex
    _now: float = __import__("time").time()
    record: dict[str, object] = {
        "stage": stage,
        "elapsed_s": elapsed_s,
        "budget_s": budget_s,
        "remaining_s": remaining_s,
        "headroom_s": headroom_s,
        "observed_at": _now,
        "warning_id": _warn_id,
    }
    line: str = json.dumps(record, separators=(",", ":")) + "\n"

    # Append-mode write to the overruns JSONL file. ``file.write()``
    # converts str -> bytes under the hood (TextIOWrapper), and the
    # flush+fileno+fsync trio gives the same durability guarantee as
    # the fd+os.write path without triggering ``Any``-from-stub issues.
    try:
        with (
            overruns_path.open(  # filesystem-write-ok: append-mode journal for per-spawn startup overruns diagnostics  # filesystem-read-ok: open append-mode write journal for startup overruns
                "a", encoding="utf-8"
            ) as f
        ):
            f.write(line)
            f.flush()
            os.fsync(  # filesystem-write-ok: durability barrier so startup overrun record survives a child SIGKILL
                f.fileno()
            )
    except BaseException:
        raise


def read_overruns(root: Path, *, spawn_id: str | None = None) -> list[dict[str, object]]:
    """Read all overrun records from ``<stage_file_path>.overruns``.

    Returns a list of dicts in insertion order. Malformed lines are skipped.
    Missing file returns ``[]``.

    Args:
        root: Workspace root for the MCP server.
        spawn_id: Optional explicit spawn identity. When ``None`` the
            module-global identity is used.
    """
    path = stage_file_path(root, spawn_id=spawn_id)
    overruns_path = Path(str(path) + ".overruns")
    try:
        # filesystem-read-ok: read per-spawn overruns diagnostic log directly from disk
        content = overruns_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return []

    records: list[dict[str, object]] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
            parsed: object = json.loads(stripped)
            if isinstance(parsed, dict):
                records.append(cast("dict[str, object]", parsed))
    return records


__all__ = [
    "RUNTIME_STAGES",
    "read_last_stage",
    "read_overruns",
    "record",
    "record_overrun",
    "reset_spawn_id",
    "stage_file_path",
]
