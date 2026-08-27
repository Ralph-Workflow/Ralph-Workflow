"""Abort a conflicted rebase and discard the progress it produced.

The two steps belong together and must never drift apart, which is why
they are one function rather than a convention every abort site is
trusted to remember.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import abort_rebase
from ralph.pipeline.conflict_resolution.progress import clear_progress

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["abort_rebase_discarding_progress"]


def abort_rebase_discarding_progress(root: Path) -> None:
    """``git rebase --abort`` at ``root``, then discard its progress sidecar.

    Aborting throws away every replay commit the resolution agent landed,
    so the sidecar naming them describes work that no longer exists. Its
    IDENTITY, however, survives the abort perfectly intact: abort restores
    HEAD to exactly ``orig-head``, so retrying the same rebase onto an
    unmoved target reproduces a byte-identical ``(orig-head, onto)`` pair.
    Scoping the record to a rebase therefore cannot reject it -- the
    identity is honestly equal -- and the record must simply not outlive
    the abort.

    Left behind, it does damage twice over on the retry:

    * :func:`~ralph.pipeline.conflict_resolution.rebase_loop._landed_shas_at_entry`
      reports the dead attempt's stops as already landed, so
      ``_resolve_one_stop`` SKIPS a genuinely unresolved stop and
      continues onto an unmerged index; and
    * :func:`~ralph.pipeline.auto_integrate_rebase_merge._rebase_has_landed_stops`
      again vetoes the abort, leaving the worktree mid-rebase.

    Both repeat every run until one of the two tips happens to move.

    The clear runs only after the abort returns. An abort that RAISES
    leaves the rebase in progress, and the record still describes it, so
    retaining it is correct -- callers keep their existing handling of
    that failure.
    """
    abort_rebase(repo_root=root)
    clear_progress(root)
