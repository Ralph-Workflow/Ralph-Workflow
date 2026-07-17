"""Idempotent write primitive for byte-identical filesystem mutations.

Exposes two behavior-preserving helpers:

* :func:`write_text_if_changed` — direct write with read-compare skip.
* :func:`atomic_write_text_if_changed` — atomic temp + replace with
  read-compare skip on the destination.

Both helpers skip a byte-identical rewrite of an existing file,
keeping the filesystem mutation rate down (notably macOS fseventsd
load) without altering any observable behavior: the file still
contains ``content`` after the call.

The helpers are intentionally fail-open: any read uncertainty
(``OSError`` on ``read_text``, a missing path, or a content
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

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend


def write_text_if_changed(
    backend: FileBackend,
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> bool:
    """Write ``content`` to ``path`` only when it differs from the existing bytes.

    Returns:
        ``True`` when a physical write was performed, ``False`` when the
        existing file already contained byte-identical content and the
        write was skipped.

    Behavior:
        * If ``backend.exists(path)`` is ``False``: writes and returns ``True``.
        * If ``backend.read_text(path)`` raises ``OSError``: writes and
          returns ``True`` (fail-open — partial/corrupt prior files
          self-heal on the next call).
        * If the read-back content equals ``content``: returns ``False``
          without calling ``backend.write_text`` (the skip).
        * Otherwise: writes and returns ``True``.

    The helper never creates parent directories. ``mkdir`` is the
    caller's responsibility so directory-creation semantics at every
    converted call site are unchanged.
    """
    if backend.exists(path):
        try:
            existing = backend.read_text(path, encoding=encoding)
        except OSError:
            existing = None
        if existing is not None and existing == content:
            return False
    backend.write_text(path, content, encoding=encoding)
    return True


def atomic_write_text_if_changed(
    backend: FileBackend,
    destination: Path,
    content: str,
    *,
    tmp_path: Path,
    encoding: str = "utf-8",
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
        * If ``backend.exists(destination)`` is ``False``: writes
          ``tmp_path``, replaces onto ``destination``, returns ``True``.
        * If ``backend.read_text(destination)`` raises ``OSError``:
          writes ``tmp_path``, replaces onto ``destination``,
          returns ``True`` (fail-open — partial/corrupt prior files
          self-heal on the next call).
        * If the read-back content equals ``content``: returns
          ``False`` without calling ``backend.write_text`` or
          ``backend.replace`` (the skip).
        * Otherwise: writes ``tmp_path``, replaces onto
          ``destination``, returns ``True``.

    The helper never creates parent directories. ``mkdir`` is the
    caller's responsibility so directory-creation semantics at every
    converted call site are unchanged.
    """
    if backend.exists(destination):
        try:
            existing = backend.read_text(destination, encoding=encoding)
        except OSError:
            existing = None
        if existing is not None and existing == content:
            return False
    backend.write_text(tmp_path, content, encoding=encoding)
    backend.replace(tmp_path, destination)
    return True


__all__ = [
    "atomic_write_text_if_changed",
    "write_text_if_changed",
]
