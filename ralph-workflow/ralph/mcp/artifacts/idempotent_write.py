"""Idempotent write primitive for byte-identical filesystem mutations.

Exposes two behavior-preserving helpers:

* :func:`write_text_if_changed` — direct text write with read-compare skip.
* :func:`atomic_write_text_if_changed` — atomic text temp + replace with
  read-compare skip on the destination.
* :func:`write_bytes_if_changed` — direct byte write with byte-compare skip.
* :func:`atomic_write_bytes_if_changed` — atomic byte temp + replace with
  byte-compare skip on the destination.

Both helpers skip a byte-identical rewrite of an existing file,
keeping the filesystem mutation rate down (notably macOS fseventsd
load) without altering any observable behavior: the file still
contains ``content`` after the call.

The helpers are intentionally fail-open: any read uncertainty
(``OSError`` or ``UnicodeDecodeError`` on ``read_text``, a missing path, or a content
mismatch) falls through to a real write so the post-condition
"file contains ``content``" always holds and a partial or corrupt
prior file self-heals on the next write.

The helpers do NOT create parent directories — ``mkdir`` stays
the caller's responsibility so existing directory-creation
semantics are unchanged at every converted call site.

The atomic helper writes ``content`` to a caller-supplied temporary
path and then delegates to ``backend.replace`` to move it on top of
``destination``. A byte-identical ``destination`` short-circuits
both the temp write and the replace so no filesystem mutation
occurs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend


def write_text_if_changed(
    backend: FileBackend,
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    """Write ``content`` to ``path`` only when it differs from the existing bytes.

    Returns:
        ``True`` when a physical write was performed, ``False`` when the
        existing file already contained byte-identical content and the
        write was skipped.

    Behavior:
        * Reads once to determine whether the destination is absent, unreadable,
          or already contains the requested bytes; it does not probe existence
          separately.
        * If ``backend.read_text(path)`` raises ``OSError`` or
          ``UnicodeDecodeError``: writes and returns ``True`` (fail-open —
          missing, partial, corrupt, or undecodable prior files self-heal on the
          next call).
        * If the read-back content equals ``content``: returns ``False``
          without calling ``backend.write_text`` (the skip).
        * Otherwise: calls ``prepare_write`` when supplied, writes, and returns
          ``True``. The callback runs only for a physical write, so callers can
          defer parent-directory creation until changed content requires it.

    Args:
        prepare_write: Optional pre-write action such as creating the destination
            parent directory. It is never called for a skipped identical write.

    The helper never creates parent directories itself. ``mkdir`` stays in the
    caller's responsibility so directory-creation semantics at every
    converted call site are unchanged.
    """
    try:
        existing = backend.read_text(path, encoding=encoding)
    except (KeyError, OSError, UnicodeDecodeError):
        # In-memory FileBackend implementations represent a missing path as
        # KeyError; OS-backed implementations raise FileNotFoundError/OSError.
        # Undecodable existing bytes are also read uncertainty: publish the
        # requested text so a corrupt prior file self-heals.
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    backend.write_text(path, content, encoding=encoding)
    return True


def write_bytes_if_changed(
    backend: FileBackend,
    path: Path,
    content: bytes,
    *,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    """Write exact ``content`` bytes only when they differ at ``path``.

    A missing or unreadable destination fails open to preserve the post-condition
    that the requested bytes are published. An identical destination skips both
    ``prepare_write`` and the physical write.
    """
    try:
        existing = backend.read_bytes(path)
    except (KeyError, OSError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    backend.write_bytes(path, content)
    return True


def _unique_staging_path(tmp_path: Path) -> Path:
    """Return a same-directory staging path that cannot collide with another writer.

    ``tmp_path`` supplies the caller's visible stem and destination filesystem;
    a random suffix scopes the actual staging file to one publication. This
    avoids shared caller-derived staging names, so concurrent atomic updates do
    not overwrite or remove each other's staging payload before ``replace``
    publishes it.
    """
    return tmp_path.with_name(f"{tmp_path.name}.{uuid4().hex}")


def atomic_write_text_if_changed(
    backend: FileBackend,
    destination: Path,
    content: str,
    *,
    tmp_path: Path,
    encoding: str = "utf-8",
    sync_directory: bool = False,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    """Atomic temp+replace write of ``content`` to ``destination``, skipping on identity.

    Writes ``content`` to ``tmp_path`` then delegates to
    ``backend.replace(tmp_path, destination)``. Mirrors
    :func:`write_text_if_changed` on the destination so a
    byte-identical existing destination short-circuits both the
    temp write and the replace.

    Returns:
        ``True`` when a physical write (and replace) was performed,
        ``False`` when ``destination`` already contained
        byte-identical content and the helper skipped.

    Behavior:
        * Reads once to determine whether the destination is absent, unreadable,
          or already contains the requested bytes; it does not probe existence
          separately.
        * If ``backend.read_text(destination)`` raises ``OSError`` or
          ``UnicodeDecodeError``: writes ``tmp_path``, replaces onto
          ``destination``, and returns ``True`` (fail-open — missing, partial,
          corrupt, or undecodable prior files self-heal on the next call).
        * If the read-back content equals ``content``: returns
          ``False`` without calling ``backend.write_text`` or
          ``backend.replace`` (the skip).
        * Otherwise: calls ``prepare_write`` when supplied, writes ``tmp_path``,
          replaces onto ``destination``, and returns ``True``.

    Args:
        prepare_write: Optional pre-write action such as creating the destination
            parent directory. It is never called for a skipped identical write.

    The helper never creates parent directories itself. ``mkdir`` stays in the
    caller's responsibility so directory-creation semantics at every converted
    call site are unchanged.
    """
    try:
        existing = backend.read_text(destination, encoding=encoding)
    except (KeyError, OSError, UnicodeDecodeError):
        # In-memory FileBackend implementations represent a missing path as
        # KeyError; OS-backed implementations raise FileNotFoundError/OSError.
        # Undecodable existing bytes are also read uncertainty: atomically
        # publish the requested text so a corrupt prior file self-heals.
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    staging_path = _unique_staging_path(tmp_path)
    try:
        backend.write_text(staging_path, content, encoding=encoding)
        backend.replace(staging_path, destination)
    except BaseException:
        backend.unlink(staging_path, missing_ok=True)
        raise
    if sync_directory:
        backend.sync_directory(destination.parent)
    return True


def atomic_write_bytes_if_changed(
    backend: FileBackend,
    destination: Path,
    content: bytes,
    *,
    tmp_path: Path,
    sync_directory: bool = False,
    prepare_write: Callable[[], None] | None = None,
) -> bool:
    """Atomically publish exact ``content`` bytes unless ``destination`` already matches.

    A changed or unreadable destination is staged at ``tmp_path`` and atomically
    replaced. An identical destination skips staging, replacement, preparation,
    and the optional directory durability barrier.
    """
    try:
        existing = backend.read_bytes(destination)
    except (KeyError, OSError):
        existing = None
    if existing is not None and existing == content:
        return False
    if prepare_write is not None:
        prepare_write()
    staging_path = _unique_staging_path(tmp_path)
    try:
        backend.write_bytes(staging_path, content)
        backend.replace(staging_path, destination)
    except BaseException:
        backend.unlink(staging_path, missing_ok=True)
        raise
    if sync_directory:
        backend.sync_directory(destination.parent)
    return True


__all__ = [
    "atomic_write_bytes_if_changed",
    "atomic_write_text_if_changed",
    "write_bytes_if_changed",
    "write_text_if_changed",
]
