"""Crash-durable overlay protocol for operator-owned MCP config files.

Three agent CLIs (Cursor, Kimi Code, AGY) offer no way to point a single
run at a private MCP config file: their user-global config path is
compiled in (``~/.cursor/mcp.json``, ``$KIMI_CODE_HOME/mcp.json``,
``~/.gemini/**/mcp_config.json``) with no flag and no environment
override that relocates it alone.  Ralph therefore has to OVERLAY a file
it does not own: publish the run-scoped ``ralph`` entry before the agent
starts, and put the operator's own bytes back when the run ends.

A plain ``try/finally`` restore is not enough.  ``SIGKILL``, an OOM kill,
a hard reboot and a power cut all skip ``finally``, and what survives is
the operator's user-global MCP config replaced by a single Ralph entry
pointing at a localhost port that no longer listens.  Their own IDE and
CLI sessions stay broken until they notice and rebuild the file by hand.

This module makes that state RECOVERABLE.  Two collaborating pieces:

**Durable backup record.**  Before the overlay is published,
:func:`stage_config_overlay` writes a sidecar next to the config
(``<name>.ralph-backup``) holding the operator's exact prior bytes (or
the fact that no file existed) plus the SHA-256 of the overlay Ralph is
about to publish.  The record is fsynced and atomically renamed into
place BEFORE the config is touched, so a crash at any instant leaves
either "no record and no overlay" or "record and overlay".

**Reclaim on the next run.**  :func:`reclaim_config_overlay` runs at the
START of the next overlay, inside the cross-process lock, before the new
snapshot is taken.  The staleness rule it applies -- the one decision
this module exists to get right -- is:

    RESTORE ONLY IF THE FILE ON DISK IS STILL BYTE-IDENTICAL TO THE
    OVERLAY THE RECORD SAYS RALPH PUBLISHED.

Rationale, case by case:

* disk == overlay: nothing has legitimately written the file since Ralph
  clobbered it, so the file IS Ralph's abandoned overlay.  Put the
  operator's bytes back (or delete the file when Ralph created it) and
  drop the record.
* disk != overlay (including "file is now absent"): somebody wrote that
  file after Ralph did -- the operator repairing the damage by hand,
  their IDE rewriting it, another tool, or a later Ralph run that
  restored correctly.  Their content is newer and authoritative, so the
  record is SUPERSEDED: drop it and touch nothing.  Age is deliberately
  not used as the signal; a timestamp cannot distinguish an operator's
  edit from a clock skew, while content can.

Because the reclaim runs before the snapshot, it also closes the second-
order failure: without it, the run after a killed one would snapshot the
Ralph-only corpse as "the operator's original" and faithfully restore
THAT forever, cementing the corruption.

**Cross-process serialization.**  :func:`mcp_config_overlay_lock` is the
bounded advisory ``fcntl.flock`` / ``msvcrt.locking`` sidecar that AGY
introduced and that ``tests/test_agy_config_overlay_cross_process_e2e.py``
proves cross-process.  Every transport that overlays a user-global config
takes it, so two independent Ralph processes cannot interleave their
snapshot/write/restore transactions -- the failure mode where whichever
process restored LAST wrote back the wrong content.  The lock is what
separates "abandoned overlay" from "another run's live overlay": a live
holder still owns the file, and a killed holder's ``flock`` is released
by the kernel, which is exactly when the reclaim is allowed to act.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import errno
import hashlib
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO, cast

from loguru import logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

#: Suffix of the advisory lock sidecar placed next to a config path. Its
#: CONTENT is never read; the file exists only so independent processes
#: can serialize their overlay transactions with an advisory file lock
#: (both flavours auto-release on process death, so a crashed holder
#: cannot strand the lock).
_LOCK_SUFFIX = ".ralph.lock"

#: Suffix of the durable backup sidecar written before an overlay.
_BACKUP_SUFFIX = ".ralph-backup"

#: Suffix used for the same-directory staging file of an atomic publish.
_STAGING_SUFFIX = ".ralph-staging"

#: Wire identifier of the backup record. Bump it only for an
#: incompatible field change; an unrecognised schema is treated as an
#: unusable record (dropped, never applied).
_BACKUP_SCHEMA = "ralph-mcp-config-backup/1"

#: Bounded acquisition budget for the cross-process advisory lock. An
#: overlay transaction's write/restore steps are milliseconds long, so
#: 10 s is far beyond legitimate contention while still failing closed
#: fast enough that a hung holder surfaces as an actionable launch error
#: rather than an unbounded stall.
_DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0

#: Retry cadence for the non-blocking lock acquisition loop.
_LOCK_POLL_SECONDS = 0.05


class McpConfigOverlayLockTimeoutError(RuntimeError):
    """An MCP config overlay lock could not be acquired within its budget.

    Raised fail-closed by :func:`mcp_config_overlay_lock` when another
    process holds the lock past the caller's timeout. The run surfaces
    the timeout as a launch failure instead of racing the holder's
    write/restore steps and corrupting the shared config.
    """


@dataclass(frozen=True)
class _BackupRecord:
    """Decoded backup sidecar: what to put back, and when that is allowed."""

    original_bytes: bytes | None
    overlay_digest: str


def mcp_config_lock_path(config_path: Path) -> Path:
    """Return the advisory-lock sidecar path guarding ``config_path``."""
    return config_path.with_name(config_path.name + _LOCK_SUFFIX)


def mcp_config_backup_path(config_path: Path) -> Path:
    """Return the durable backup sidecar path for ``config_path``."""
    return config_path.with_name(config_path.name + _BACKUP_SUFFIX)


@contextmanager
def mcp_config_overlay_lock(
    lock_path: Path,
    *,
    timeout_seconds: float | None = None,
    error_type: type[McpConfigOverlayLockTimeoutError] = McpConfigOverlayLockTimeoutError,
) -> Iterator[None]:
    """Hold a bounded cross-process advisory lock on ``lock_path``.

    Uses the repository's established ``fcntl.flock`` / ``msvcrt.locking``
    pattern (same shape as :mod:`ralph.workspace.agent_dir_retention` and
    :mod:`ralph.mcp.server._wire_ledger`) with a bounded
    retry-with-deadline loop, so a contending caller fails closed instead
    of blocking the MCP launch path forever. The lock file is created on
    first use and never removed -- its contents are never read, only the
    lock state matters.

    Args:
        lock_path: Sidecar path to lock (see :func:`mcp_config_lock_path`).
        timeout_seconds: Acquisition budget; ``None`` uses the module
            default. Callers pass their own module-level constant so a
            test can shrink the budget by assigning that attribute.
        error_type: Exception class raised on timeout. Transports with a
            public, agent-specific timeout error pass it here so their
            documented exception keeps propagating.

    Raises:
        McpConfigOverlayLockTimeoutError: (or ``error_type``) when the
            lock is still held at the deadline.
    """
    budget = _DEFAULT_LOCK_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    DEFAULT_FILE_BACKEND.mkdir(lock_path.parent, parents=True, exist_ok=True)
    # filesystem-write-ok: persistent advisory-lock sidecar serializes cross-process MCP config overlay writes; contents never read back.
    handle: TextIO = lock_path.open("a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + budget
    try:
        while True:
            if _try_acquire(handle):
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise error_type(
                    f"MCP config lock {lock_path} held by another process for more "
                    f"than {budget:.1f}s; refusing to race the concurrent overlay "
                    "write/restore."
                )
            time.sleep(_LOCK_POLL_SECONDS)  # filesystem-poll-ok: cross-process file-lock retry; no event source for another process's flock release
        yield
    finally:
        if acquired:
            _release(handle)
        with contextlib.suppress(OSError):
            handle.close()


def stage_config_overlay(config_path: Path, payload: bytes) -> bytes | None:
    """Publish ``payload`` to ``config_path`` behind a durable backup record.

    The backup sidecar (operator bytes plus the digest of ``payload``) is
    fsynced and renamed into place BEFORE the config is overwritten, so an
    abnormal exit anywhere after this call is recoverable by
    :func:`reclaim_config_overlay` on the next run.

    Returns:
        The bytes ``config_path`` held before the overlay, or ``None``
        when the file did not exist. Hand the value back to
        :func:`restore_config_overlay` on the normal exit path.
    """
    original_bytes = _read_bytes_or_none(config_path)
    if original_bytes != payload:
        # A byte-identical destination is not an overlay: there is nothing
        # to put back and nothing to recover, so no record is written.
        _publish(mcp_config_backup_path(config_path), _encode_backup_record(original_bytes, payload))
    atomic_write_bytes_if_changed(
        DEFAULT_FILE_BACKEND,
        config_path,
        payload,
        tmp_path=config_path.with_name(config_path.name + _STAGING_SUFFIX),
        prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(
            config_path.parent, parents=True, exist_ok=True
        ),
    )
    return original_bytes


def restore_config_overlay(config_path: Path, original_bytes: bytes | None) -> None:
    """Put ``original_bytes`` back at ``config_path`` and retire the backup record.

    ``original_bytes=None`` means the file did not exist before the
    overlay, so the file Ralph created is removed. The backup record is
    dropped only after the restore has been published, so a crash between
    the two leaves a record whose staleness rule already resolves to
    "superseded" -- never to a second, wrong restore.
    """
    if original_bytes is None:
        DEFAULT_FILE_BACKEND.unlink(config_path, missing_ok=True)
    else:
        atomic_write_bytes_if_changed(
            DEFAULT_FILE_BACKEND,
            config_path,
            original_bytes,
            tmp_path=config_path.with_name(config_path.name + _STAGING_SUFFIX),
            prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(
                config_path.parent, parents=True, exist_ok=True
            ),
        )
    DEFAULT_FILE_BACKEND.unlink(mcp_config_backup_path(config_path), missing_ok=True)


def reclaim_config_overlay(config_path: Path) -> bool:
    """Undo an overlay a previous run was killed before it could restore.

    Call this at the START of an overlay transaction, inside the
    cross-process lock and before snapshotting the current bytes.

    The record is applied ONLY while ``config_path`` still holds the exact
    overlay it names (module docstring: "restore only if the file on disk
    is still byte-identical to the overlay"). Any other content -- an
    operator's hand repair, an IDE rewrite, an absent file -- is newer and
    authoritative, so the record is dropped without touching the file.

    Returns:
        ``True`` when the operator's config was restored, ``False`` when
        there was nothing to reclaim or the record was superseded.
    """
    backup_path = mcp_config_backup_path(config_path)
    raw_record = _read_bytes_or_none(backup_path)
    if raw_record is None:
        return False
    record = _decode_backup_record(raw_record)
    if record is None:
        logger.warning(
            "Discarding unreadable Ralph MCP config backup {}; leaving {} untouched.",
            backup_path,
            config_path,
        )
        DEFAULT_FILE_BACKEND.unlink(backup_path, missing_ok=True)
        return False
    current_bytes = _read_bytes_or_none(config_path)
    if current_bytes is None or _digest(current_bytes) != record.overlay_digest:
        logger.debug(
            "Ralph MCP config backup {} is superseded by newer content at {}; dropping it.",
            backup_path,
            config_path,
        )
        DEFAULT_FILE_BACKEND.unlink(backup_path, missing_ok=True)
        return False
    logger.warning(
        "Restoring {} from a previous Ralph run that exited before it could put the "
        "file back (its MCP overlay was still in place).",
        config_path,
    )
    restore_config_overlay(config_path, record.original_bytes)
    return True


def _try_acquire(handle: TextIO) -> bool:
    """Attempt one non-blocking advisory lock acquisition on ``handle``."""
    try:
        if sys.platform == "win32":
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise
        return False
    return True


def _release(handle: TextIO) -> None:
    """Release the advisory lock held on ``handle``, tolerating a closed file."""
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_bytes_or_none(path: Path) -> bytes | None:
    """Return the bytes at ``path``, or ``None`` when it is absent or unreadable."""
    try:
        return DEFAULT_FILE_BACKEND.read_bytes(path)
    except OSError:
        return None


def _digest(data: bytes) -> str:
    """Return the hex SHA-256 of ``data`` (the overlay-identity fingerprint)."""
    return hashlib.sha256(data).hexdigest()


def _publish(path: Path, data: bytes) -> None:
    """Durably publish ``data`` at ``path`` (fsynced write, atomic rename, synced dir)."""
    atomic_write_bytes_if_changed(
        DEFAULT_FILE_BACKEND,
        path,
        data,
        tmp_path=path.with_name(path.name + _STAGING_SUFFIX),
        prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(path.parent, parents=True, exist_ok=True),
        sync_directory=True,
    )


def _encode_backup_record(original_bytes: bytes | None, payload: bytes) -> bytes:
    """Serialize the recovery record for one overlay of one config path."""
    record: dict[str, object] = {
        "schema": _BACKUP_SCHEMA,
        "original_present": original_bytes is not None,
        "original_base64": (
            base64.b64encode(original_bytes).decode("ascii") if original_bytes is not None else ""
        ),
        "overlay_sha256": _digest(payload),
    }
    return json.dumps(record, indent=2).encode("utf-8")


def _parse_backup_fields(raw_record: bytes) -> dict[str, object] | None:
    """Return the record's JSON object, or ``None`` when it is unusable.

    Corruption is never applied: a truncated, non-JSON, non-object or
    wrong-schema sidecar yields ``None`` so the caller drops it and leaves
    the operator's file exactly as it found it.
    """
    try:
        parsed: object = json.loads(raw_record.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    fields = cast(
        "dict[str, object]", parsed
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if fields.get("schema") != _BACKUP_SCHEMA:
        return None
    return fields


def _decode_original_bytes(encoded_original: object) -> bytes | None:
    """Decode the base64 operator bytes, or ``None`` when the field is unusable."""
    if not isinstance(encoded_original, str):
        return None
    try:
        return base64.b64decode(encoded_original.encode("ascii"), validate=True)
    except (binascii.Error, ValueError):
        return None


def _decode_backup_record(raw_record: bytes) -> _BackupRecord | None:
    """Parse a backup sidecar into the bytes to restore and when that is allowed."""
    fields = _parse_backup_fields(raw_record)
    if fields is None:
        return None
    overlay_digest = fields.get("overlay_sha256")
    present = fields.get("original_present")
    if not isinstance(overlay_digest, str) or not isinstance(present, bool):
        return None
    if not present:
        return _BackupRecord(original_bytes=None, overlay_digest=overlay_digest)
    original_bytes = _decode_original_bytes(fields.get("original_base64"))
    if original_bytes is None:
        return None
    return _BackupRecord(original_bytes=original_bytes, overlay_digest=overlay_digest)


__all__ = [
    "McpConfigOverlayLockTimeoutError",
    "mcp_config_backup_path",
    "mcp_config_lock_path",
    "mcp_config_overlay_lock",
    "reclaim_config_overlay",
    "restore_config_overlay",
    "stage_config_overlay",
]
