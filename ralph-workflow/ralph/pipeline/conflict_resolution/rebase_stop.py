"""The one commit a paused rebase stopped on, as the loop passes it around.

Split out of :mod:`ralph.pipeline.conflict_resolution.rebase_loop` so the
stop record has no dependency on the loop that produces it. Everything
that merely READS a stop -- the deterministic resolver, the driver, the
status bar, the injected resolver callable -- can import it without
importing the loop, which is what keeps those modules free of the
loop's own git and progress-sidecar imports.

:class:`RebaseStop` is re-exported from ``rebase_loop`` (and from the
package root), so every pre-existing import path still resolves.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RebaseStop"]


@dataclass(frozen=True)
class RebaseStop:
    """One commit a rebase has paused on because replaying it conflicted.

    Carries exactly the context a resolution session is allowed to see:
    which commit is being replayed and which paths conflicted, plus two
    INDEPENDENT counters that are easy to confuse.

    ``stop_index``/``stop_cap`` are the bounded loop's safety counters:
    how many stops this loop has spent out of the fixed
    :data:`~ralph.pipeline.conflict_resolution.graph.MAX_REBASE_CONFLICT_STOPS`
    it is allowed, which is what terminates the loop. They say nothing
    about how long the rebase is.

    ``replay_index``/``replay_total`` are the operator-facing replay
    position: which of the rebase's own commits is being replayed, read
    from git's rebase state by :func:`~ralph.pipeline.conflict_resolution.rebase_loop._read_replay_progress`. They are
    display-only, both ``None`` when that state is unreadable, and must
    never influence loop termination.
    """

    sha: str
    subject: str
    conflicted_files: tuple[str, ...]
    stop_index: int
    stop_cap: int
    replay_index: int | None = None
    replay_total: int | None = None
