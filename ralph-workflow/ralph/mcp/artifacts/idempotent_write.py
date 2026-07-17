"""Idempotent write primitive for byte-identical filesystem mutations.

Exposes a single behavior-preserving helper,
:func:`write_text_if_changed`, that performs a write only when the
on-disk bytes differ from the requested content. Skipping a
byte-identical write keeps the filesystem mutation rate down
(notably macOS fseventsd load) without altering any observable
behavior: the file still contains ``content`` after the call.

The helper is intentionally fail-open: any read uncertainty
(``OSError`` on ``read_text``, a missing path, or a content
mismatch) falls through to a real write so the post-condition
"file contains ``content``" always holds and a partial or corrupt
prior file self-heals on the next write.

The helper does NOT create parent directories — ``mkdir`` stays
the caller's responsibility so existing directory-creation
semantics are unchanged at every converted call site.
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


__all__ = ["write_text_if_changed"]
