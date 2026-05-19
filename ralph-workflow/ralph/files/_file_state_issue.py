"""FileStateIssue — a mismatch between captured and current file state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.files._file_state_kind import FileStateKind


@dataclass(frozen=True)
class FileStateIssue:
    """A mismatch between captured and current file state."""

    kind: FileStateKind
    path: Path


__all__ = ["FileStateIssue"]
