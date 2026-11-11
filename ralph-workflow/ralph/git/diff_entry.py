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
        base_tree_paths: The tracked paths of the tree the diff was computed
            against, or None when the entry does not carry base-tree
            membership. A recursive diff reports BLOB entries only, so the
            removal of a whole directory arrives as one deleted blob path per
            tracked file and never as a directory entry: the base-tree
            membership is what tells "the directory is gone in its entirety"
            apart from "some files under it are gone".
    """

    path: str
    status: str
    old_mode: str
    new_mode: str
    size: int
    is_binary: bool
    content: str | None = None
    base_tree_paths: frozenset[str] | None = None
