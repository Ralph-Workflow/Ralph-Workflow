"""Resolve a conflicted rebase in place, one stopped commit at a time.

A rebase is not a single conflict. It replays N commits and can stop on
a conflict at every one of them, so resolving it means a LOOP: resolve
the stop, prove the resolution, ``git rebase --continue``, repeat. The
previous behaviour -- abort on the first stop and fall back to one
endpoint three-way merge -- meant the rebase-conflict resolution phase
was never reachable from a rebase at all, which is the defect this
module exists to close.

The resolver is injected as a plain callable rather than imported, for
the same reason
:data:`ralph.pipeline.auto_integrate_resolve.ConflictResolver` is: it
keeps this module free of the agent session, the display and the policy
bundle, so the loop can be unit-tested with fakes and driven end to end
by a deterministic stub.

Division of labour, mirroring
:func:`ralph.pipeline.auto_integrate_resolve._stage_verify_and_commit`
exactly: the resolver only EDITS FILES. Ralph stages, Ralph re-scans for
surviving conflict markers, Ralph asks git whether any unmerged path
remains, and only Ralph continues the rebase. An agent running under
Ralph's own MCP exec policy is denied every git invocation, so it could
not stage even if it tried.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)
from ralph.git.rebase.rebase import get_conflicted_files
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.conflict_resolution.graph import (
    MAX_REBASE_CONFLICT_STOPS,
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)

if TYPE_CHECKING:
    from pathlib import Path

#: Placeholder subject used when the stopped commit's subject could not
#: be read. The stop is still resolvable -- the conflicted paths are what
#: the agent actually needs -- so an unreadable subject must not abort it.
_UNKNOWN_SUBJECT = "(subject unavailable)"

__all__ = [
    "RebaseStop",
    "RebaseStopResolver",
    "resolve_rebase_in_progress",
]


@dataclass(frozen=True)
class RebaseStop:
    """One commit a rebase has paused on because replaying it conflicted.

    Carries exactly the context a resolution session is allowed to see:
    which commit is being replayed and which paths conflicted, plus the
    stop's position in the bounded loop so the operator can be told how
    far through the replay the run is.
    """

    sha: str
    subject: str
    conflicted_files: tuple[str, ...]
    stop_index: int
    stop_cap: int


#: Resolves ONE rebase stop: ``(root, target, stop) -> resolved``. The
#: implementation is expected to edit the conflicted files and nothing
#: else; staging and continuing are Ralph's job, never the resolver's.
#:
#: ``Callable`` is imported at RUNTIME, not under ``TYPE_CHECKING``: a
#: ``type`` alias body is evaluated lazily, and sphinx autodoc forces
#: that evaluation while building the API reference. The same is true of
#: :data:`ralph.pipeline.conflict_resolution.driver.ResolutionInvoker`.
type RebaseStopResolver = Callable[["Path", str, RebaseStop], bool]


def resolve_rebase_in_progress(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
) -> bool:
    """Drive an in-progress rebase to completion through ``resolver``.

    Args:
        root: Repository root holding the paused rebase.
        target: Branch being rebased onto, used to verify completion.
        resolver: Called once per stop to resolve that stop's conflicts.

    Returns:
        ``True`` only when the rebase finished AND ``HEAD`` is a
        descendant of ``target``. ``False`` for every other outcome,
        including a declining resolver, a surviving conflict marker, a
        refused continuation and an exhausted stop budget.

    Never raises. The caller treats ``False`` as "resolution declined"
    and falls through to the pre-existing abort-then-endpoint-merge
    path, so an unexpected failure here degrades to exactly the
    behaviour that shipped before this module existed.
    """
    try:
        return _resolve_stops(root, target, resolver)
    except Exception as exc:
        logger.warning(
            "conflict_resolution: rebase resolution loop failed for '{}': {}",
            target,
            exc,
        )
        return False


def _resolve_stops(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
) -> bool:
    """Body of the bounded loop; see :func:`resolve_rebase_in_progress`."""
    stops_spent = 0
    while rebase_in_progress_at(root):
        stop = _read_stop(root, stops_spent + 1)
        if stop is None:
            return False
        if not resolver(root, target, stop):
            logger.info(
                "conflict_resolution: resolver declined rebase stop {} ({})",
                stop.stop_index,
                stop.sha,
            )
            return False
        if not _stage_and_prove(root, stop):
            return False
        if not _continue_past(root, stop):
            return False

        stops_spent += 1
        route = route_after_stop(stops_spent, not rebase_in_progress_at(root))
        if route == TERMINAL_RESOLVED:
            break
        if route == TERMINAL_ABANDONED:
            logger.warning(
                "conflict_resolution: rebase still conflicting after {} stop(s); "
                "abandoning in favour of the endpoint merge",
                stops_spent,
            )
            return False

    return verify_rebase_completed_at(root, target)


def _read_stop(root: Path, stop_index: int) -> RebaseStop | None:
    """Describe the commit the rebase is currently stopped on.

    Returns ``None`` when the paused rebase reports no conflicted path.
    That combination is anomalous -- a stop this loop can act on always
    has unmerged entries in the index -- and there is nothing a resolver
    could repair, so declining hands the repository to the caller's
    abort path untouched rather than guessing.
    """
    conflicted = tuple(get_conflicted_files(repo_root=root))
    if not conflicted:
        logger.warning(
            "conflict_resolution: rebase is paused with no conflicted path; "
            "declining to resolve"
        )
        return None
    return RebaseStop(
        sha=_rev_parse_rebase_head(root),
        subject=_rebase_head_subject(root),
        conflicted_files=conflicted,
        stop_index=stop_index,
        stop_cap=MAX_REBASE_CONFLICT_STOPS,
    )


def _stage_and_prove(root: Path, stop: RebaseStop) -> bool:
    """Stage the stop's paths and prove the conflicts are really gone.

    Staging is scoped to exactly the paths that conflicted -- never
    ``git add -A`` -- so an unrelated file the agent touched is not
    swept into the replayed commit.

    The marker scan runs AFTER staging deliberately: ``git add`` clears
    a file's unmerged bit even when ``<<<<<<<`` fences survive in its
    content, so an empty :func:`unmerged_paths` result is not by itself
    proof of a resolution. Both gates are kept; git is authoritative
    about the index, the textual scan about the content.
    """
    paths = list(stop.conflicted_files)
    if not stage_paths(root, paths):
        logger.warning(
            "conflict_resolution: failed to stage resolved paths for stop {}: {}",
            stop.stop_index,
            paths,
        )
        return False
    marked = paths_with_conflict_markers(root, paths)
    if marked:
        logger.warning(
            "conflict_resolution: conflict markers survive at stop {}: {}",
            stop.stop_index,
            marked,
        )
        return False
    remaining = unmerged_paths(root)
    if remaining:
        logger.warning(
            "conflict_resolution: unmerged paths remain at stop {}: {}",
            stop.stop_index,
            remaining,
        )
        return False
    return True


def _continue_past(root: Path, stop: RebaseStop) -> bool:
    """Run ``git rebase --continue`` for a proven-resolved stop.

    Two non-obvious outcomes both count as SUCCESS for this stop:

    * :class:`NoRebaseInProgressError` -- the rebase finished between the
      proof and the continuation, which is the state the loop drives
      towards.
    * A non-zero exit that is really the NEXT commit conflicting.
      ``git rebase --continue`` commits the resolved stop and then keeps
      replaying; if the very next commit conflicts, git reports the whole
      invocation as failed even though this stop landed. Treating that as
      a failure is what made a two-conflict branch unresolvable while a
      one-conflict branch worked -- the loop would decline on stop 1 and
      fall back to the endpoint merge, so exactly the messiest rebases
      never got the multi-stop resolution this module exists to provide.
    """
    try:
        continue_rebase_at(root)
    except NoRebaseInProgressError:
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        if _advanced_to_a_new_stop(root, stop):
            logger.info(
                "conflict_resolution: stop {} landed; the rebase stopped again "
                "on the next commit",
                stop.stop_index,
            )
            return True
        logger.warning(
            "conflict_resolution: could not continue the rebase past stop {}: {}",
            stop.stop_index,
            exc,
        )
        return False
    return True


def _advanced_to_a_new_stop(root: Path, stop: RebaseStop) -> bool:
    """Whether the rebase moved on to a DIFFERENT commit than ``stop``.

    Identity, not exit code: ``REBASE_HEAD`` naming a commit other than
    the one just resolved is proof that git committed this stop and
    paused on a later one. A rebase that is no longer in progress is not
    a new stop -- that case is already handled by the caller's
    :class:`NoRebaseInProgressError` branch and by the loop's own
    completion check.
    """
    if not rebase_in_progress_at(root):
        return False
    current = _rev_parse_rebase_head(root)
    return bool(current) and current != stop.sha


def _rev_parse_rebase_head(root: Path) -> str:
    """SHA of the commit being replayed, or ``''`` when unreadable."""
    result = run_git(
        ("rev-parse", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-sha",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _rebase_head_subject(root: Path) -> str:
    """Subject line of the commit being replayed, with a safe fallback."""
    result = run_git(
        ("log", "-1", "--format=%s", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-subject",
    )
    if result.returncode != 0:
        return _UNKNOWN_SUBJECT
    return result.stdout.strip() or _UNKNOWN_SUBJECT
