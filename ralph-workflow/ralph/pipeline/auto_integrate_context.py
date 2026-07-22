"""Pre-flight context helpers for auto-integration's early skip table.

Separated from :mod:`ralph.pipeline.auto_integrate` so that module
stays under the repo-structure ``_MAX_FILE_LINES`` cap. Everything
here belongs to the phase BEFORE any git mutation: reading the branch
the integration would run on, and deciding what the origin refresh
performed in that same phase means for the record the seam returns.

The refresh helpers exist because the early skip paths ("nothing to
integrate", "already at the target") are decided FROM the target
pointer the refresh was supposed to freshen. A refresh that could not
reach origin therefore makes those verdicts rest on a pointer of
unknown age, and the fail-open contract means nothing else fails. The
outcome has to travel with the record instead of being discarded, or
an unreachable mainline is completely silent on exactly the paths that
run most often.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
    REFRESH_DISABLED,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_ORIGIN,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_REFRESHED,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

#: Refresh outcomes that leave the target pointer trustworthy: either
#: it was just read from origin, or origin has no say over it (fetching
#: is off, there is no origin, or origin does not carry the branch), or
#: it was just re-read from the shared ref store a fleet of sibling
#: worktrees advances directly. Every OTHER outcome -- unreachable,
#: diverged, a lost refresh race, a missing local branch -- means the
#: pointer the skip decision used could not be confirmed current, so the
#: skip must say so.
_HEALTHY_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {
        REFRESH_ALREADY_CURRENT,
        REFRESH_DISABLED,
        REFRESH_LOCAL_FLEET,
        REFRESH_NO_ORIGIN,
        REFRESH_NO_REMOTE_BRANCH,
        REFRESH_REFRESHED,
    }
)

__all__ = [
    "current_branch_or_detached_marker",
    "record_refresh",
    "record_when_stale",
    "refresh_outcome_is_healthy",
]


def refresh_outcome_is_healthy(refresh: str | None) -> bool:
    """Whether ``refresh`` leaves the target pointer trustworthy.

    ``None`` means no refresh was attempted on this path at all, which
    is healthy in the same sense as :data:`REFRESH_DISABLED`: nothing
    claimed freshness and nothing failed to establish it.
    """
    return refresh is None or refresh in _HEALTHY_REFRESH_OUTCOMES


def record_refresh(record: RebaseState, refresh: str | None) -> RebaseState:
    """Stamp the refresh outcome onto a record built before it was known.

    A no-op when ``refresh`` is ``None`` (no refresh ran) so the
    disabled-feature and no-target paths stay byte-identical to what
    they recorded before the refresh became observable.
    """
    if refresh is None:
        return record
    return record.model_copy(update={"last_refresh": refresh})


def record_when_stale(
    record: RebaseState, refresh: str | None
) -> RebaseState | None:
    """Return ``record`` stamped with ``refresh``, or ``None`` if healthy.

    The phase-boundary hook is deliberately quiet: it records nothing
    when there is nothing to integrate. That silence is only defensible
    while the target pointer the verdict was read through is
    trustworthy, so this is the one place that decides between staying
    quiet and speaking up. The caller builds ``record`` eagerly because
    constructing one frozen model is cheaper than threading a factory.
    """
    if refresh_outcome_is_healthy(refresh):
        return None
    return record_refresh(record, refresh)


def current_branch_or_detached_marker(root: Path) -> str | None:
    """Return the current branch name or ``None`` if HEAD is detached.

    Detaches the typed-exception guard from :func:`get_current_branch`'s
    broad fallback so the auto-integrate skip table can record a
    DETACHED-HEAD outcome (AC-02/AC-13 skip condition: "no branch to
    integrate"). GitPython raises ``TypeError`` from
    ``repo.active_branch.name`` when HEAD points at a detached SHA.
    Any other exception -- not a git repo, transport error, etc. --
    propagates so the caller can surface the actual failure.

    Returns:
        The current branch name, or ``None`` when HEAD is detached.
    """
    from git import Repo
    from git.exc import GitCommandError

    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except (TypeError, ValueError, GitCommandError, AttributeError):
        # TypeError is GitPython's "DetachedHead has no .name"
        # AttributeError is the same in some GitPython versions.
        # ValueError/GitCommandError cover "no HEAD" / "ambiguous HEAD"
        # edge cases that are also "not on a branch".
        return None
    finally:
        if repo is not None:
            close_method: object = getattr(repo, "close", None)
            if callable(close_method):
                close_method()
