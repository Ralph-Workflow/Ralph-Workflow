"""Atomic and safe writer for the user-global ``ralph-workflow-agents.toml``.

:func:`write_agents_toml` is the single choke point for every write to the
user-global agents configuration (``~/.config/ralph-workflow-agents.toml``).
It validates the TOML payload, compares it with the on-disk file **as
bytes**, replaces the target atomically through a temporary file in the
same directory, and reports the write on an operator-visible sink.

Behavioural contract
---------------------

**Bytes, not decoded text.** The current file is read with
``Path.read_bytes`` and compared with the UTF-8 encoding of the new
payload. A file whose bytes are not valid UTF-8 is therefore *different*
(it can never equal the new payload) instead of raising
``UnicodeDecodeError``; its raw bytes land in the ``.bak`` so
``ralph --regenerate-config`` can always recover an operator's file.

**Symlinked targets.** When ``path`` is a symlink the write goes *through*
the link: the resolved real file is replaced, the symlink itself is never
replaced, and it stays a symlink pointing at the updated file.

**Backup location.** The backup always lives at ``<path>.bak`` next to the
*requested* path -- the path the operator configured -- even when that
path is a symlink. It holds the previous bytes of the file that was
actually replaced (the resolved real file for a symlinked target). The
location is deterministic and matches the ``BootstrapResult.backup`` path
that :mod:`ralph.config.bootstrap` derives from the same target.

**Modes.** An existing file keeps its mode (``stat.S_IMODE`` of the
replaced file, symlinks resolved). A newly created file gets mode
``0644``; ``tempfile.mkstemp`` would otherwise leave it at ``0600``.

**Failure cleanup.** The backup is published *before* the replace, so an
operator's content is never overwritten without a recoverable copy on
disk. A failed write removes the temporary file under
``contextlib.suppress(OSError)`` so the original exception is never
masked and no stray temporary file survives, and it removes (or restores)
the backup it created, so a failed write leaves the original file
byte-identical and no stray backup behind.

**Concurrent writers.** The backup is staged under a writer-unique name
and published with an atomic replace, so a reader never observes a
half-written backup and two writers never write the same backup file.
Each publication gets its own file identity, which the writer records as
an ownership token: a failed write only removes (or restores) the backup
while the canonical ``.bak`` still carries the identity this writer
published. A concurrent writer that published a newer recovery copy
therefore keeps its backup, and the failing writer never destroys
another writer's only recoverable copy of the operator's bytes.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
import tempfile
import tomllib
from pathlib import Path

from loguru import logger

_std_logger = logging.getLogger(__name__)

#: Mode applied to a newly created agents configuration file.
_NEW_FILE_MODE = 0o644


def _read_existing_bytes(target: Path) -> bytes | None:
    """Return the current bytes of *target*, or ``None`` when it does not exist."""
    try:
        # filesystem-read-ok: current bytes for the idempotent comparison and the .bak backup
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _resolve_mode(target: Path) -> int:
    """Return the permission bits a replacement of *target* must carry."""
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return _NEW_FILE_MODE


def _file_identity(path: Path) -> tuple[int, int] | None:
    """Return an ownership token for *path*, or ``None`` when it has none.

    The token is the device and inode of the published file. A writer
    records the token of the backup it publishes and may only remove or
    restore that backup while the canonical path still carries the same
    token, which proves no other writer republished it meanwhile.
    """
    try:
        status = path.stat()
    except OSError:
        return None
    if status.st_ino == 0:
        return None
    return (status.st_dev, status.st_ino)


def _publish_backup(
    bak_path: Path, previous_bytes: bytes, directory: Path
) -> tuple[int, int] | None:
    """Publish *previous_bytes* as the canonical backup and return its ownership token.

    The bytes are staged under a writer-unique name in *directory* and
    then published with an atomic replace, so the canonical backup is
    never observed half-written and two concurrent writers never write
    the same file.
    """
    staging = directory / f".{bak_path.name}.stage.{os.urandom(8).hex()}"
    descriptor = os.open(  # resource-lifecycle-ok: the staged backup descriptor is owned by the file object below and closed by it, and closed explicitly when the file object cannot be built; filesystem-write-ok: transient backup staging file published atomically before the target replace
        staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    try:
        staged = os.fdopen(  # filesystem-write-ok: transient scratch file holding the previous agents config bytes
            descriptor, "wb"
        )
    except BaseException:
        os.close(descriptor)
        raise
    try:
        with staged:
            staged.write(previous_bytes)
            staged.flush()
        identity = _file_identity(staging)
        staging.replace(bak_path)
    except BaseException:
        with contextlib.suppress(OSError):
            staging.unlink(  # filesystem-write-ok: removing the unpublished backup staging file
                missing_ok=True
            )
        raise
    return identity


def _release_backup(
    bak_path: Path,
    previous_backup: bytes | None,
    published_identity: tuple[int, int] | None,
) -> None:
    """Undo the backup publication of a failed write, but only when it is still ours.

    A concurrent writer that republished the canonical backup owns it now;
    removing it would destroy the only recoverable copy of the operator's
    bytes, so the cleanup leaves it untouched.
    """
    if published_identity is None:
        return
    if _file_identity(bak_path) != published_identity:
        return
    with contextlib.suppress(OSError):
        if previous_backup is None:
            bak_path.unlink(  # filesystem-write-ok: removing the backup created by the failed write
                missing_ok=True
            )
        else:
            bak_path.write_bytes(  # filesystem-write-ok: restoring the pre-existing backup after a failed write
                previous_backup
            )


def write_agents_toml(path: Path, text: str, *, trigger: str) -> bool:
    """Validate TOML and atomically write text to path.

    Parameters
    ----------
    path:
        Target file path for the agents configuration. A symlink is
        written through: the resolved real file is replaced and the
        symlink stays a symlink.
    text:
        TOML formatted string content to write.
    trigger:
        Description or identifier of why the write was initiated.

    Returns
    -------
    bool:
        True if the file was written, False if existing content was identical.

    Raises
    ------
    ValueError:
        If text is not valid TOML.
    """
    path = Path(path)

    try:
        tomllib.loads(text)
    except ValueError as err:
        raise ValueError(f"Invalid TOML content for {path}: {err}") from err

    new_bytes = text.encode("utf-8")
    write_target = path.resolve() if path.is_symlink() else path
    bak_path = path.with_name(f"{path.name}.bak")

    existing_bytes = _read_existing_bytes(write_target)
    if existing_bytes == new_bytes:
        return False

    file_mode = _resolve_mode(write_target)

    write_target.parent.mkdir(  # filesystem-write-ok: ensuring target directory exists before writing agents config
        parents=True, exist_ok=True
    )
    previous_backup: bytes | None = None
    published_backup: tuple[int, int] | None = None
    tmp_path: Path | None = None
    try:
        fd, tmp_path_str = tempfile.mkstemp(
            dir=write_target.parent,
            prefix=f".{write_target.name}.tmp.",
        )
        tmp_path = Path(tmp_path_str)
        with os.fdopen(  # filesystem-write-ok: transient scratch file written before atomic replace
            fd, "wb"
        ) as handle:
            handle.write(new_bytes)
            handle.flush()
        tmp_path.chmod(  # filesystem-write-ok: the replacement keeps the previous file mode (0644 for a new file)
            file_mode
        )
        if existing_bytes is not None:
            previous_backup = bak_path.read_bytes() if bak_path.is_file() else None
            # The backup lands BEFORE the replace: an overwrite never happens
            # without a recoverable copy of the previous bytes on disk.
            published_backup = _publish_backup(bak_path, existing_bytes, write_target.parent)
        # filesystem-write-ok: atomic-replace boundary for agents config file
        tmp_path.replace(write_target)
    except BaseException:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(  # filesystem-write-ok: removing transient scratch file on failure
                    missing_ok=True
                )
        _release_backup(bak_path, previous_backup, published_backup)
        raise

    logger.info("Wrote {} (trigger: {}, backup: {})", path, trigger, bak_path)
    _std_logger.info("Wrote %s (trigger: %s, backup: %s)", path, trigger, bak_path)
    return True
