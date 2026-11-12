from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import abort_rebase
from ralph.pipeline.conflict_resolution.progress import clear_progress

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["abort_rebase_discarding_progress"]


def abort_rebase_discarding_progress(root: Path) -> None:
    abort_rebase(repo_root=root)
    clear_progress(root)
