"""Diff entry representation for git comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiffEntry:
    """A single file change entry between two trees or references.

    Attributes:
        path: Path relative to repository root.
        status: Git diff status code (e.g. 'M', 'A', 'D', 'R', 'T').
        old_mode: Octal file mode before the change (or '000000').
        new_mode: Octal file mode after the change (or '000000').
        size: Size in bytes of the new file/blob (0 if deleted).
        is_binary: True if the file contains NUL bytes or is binary.
        content: Text content of the new blob, or None when it was not read.
            Only bounded, non-binary blobs are read, so a staged credential
            can be detected by CONTENT and not only by file name.
    """

    path: str
    status: str
    old_mode: str
    new_mode: str
    size: int
    is_binary: bool
    content: str | None = None
