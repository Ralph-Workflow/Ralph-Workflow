"""Fast-forward helpers for :mod:`ralph.pipeline.auto_integrate`.

Houses the worktree-aware / atomic-CAS fast-forward path so the
main :mod:`ralph.pipeline.auto_integrate` module stays under the
repo-structure ``_MAX_FILE_LINES`` cap. The helpers here are
a coherent unit (the AC-08 / AC-09 fast-forward branch table)
and have no callers outside :mod:`ralph.pipeline.auto_integrate`.

When the target branch is checked out somewhere, the landing order is
``git merge --ff-only`` in that worktree FIRST, then the atomic CAS as
a fallback. ``merge --ff-only`` advances the ref, the index and the
working tree together and exits non-zero WITHOUT mutating anything
when local changes would be overwritten; the CAS advances only the
shared ref, leaving that checkout's index behind so ``git status``
there describes the freshly landed work as a local reverse diff.
Trying the consistent path first is therefore strictly safer, and the
CAS still keeps the ref moving when git refuses the merge.

What neither path can do is overwrite FILE CONTENT or lose a
concurrent landing: ``merge --ff-only`` refuses rather than clobber a
working tree, and the CAS is conditioned on the exact SHA it observed,
so a sibling that landed in between wins. What the CAS fallback
deliberately DOES do is advance ``refs/heads/<target>`` while that
target's checkout is dirty -- the merge was refused precisely because
that checkout has uncommitted work -- which leaves the checkout's
index behind the ref until its owner catches up. That is intentional,
not an oversight: a fleet must not stop synchronising because one
agent left a tracked file modified. It is announced with a WARN naming
the worktree (see :func:`_fast_forward_via_target_worktree`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    WORKTREE_FOUND,
    WORKTREE_QUERY_FAILED,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    observe_branch_sha,
    worktree_lookup,
)
from ralph.git.operations import find_main_worktree_root
from ralph.git.remote_push import push_branch_to_all_remotes

_TARGET_MISSING = "target branch missing at fast-forward time"
_TARGET_QUERY_FAILED = "target branch could not be read at fast-forward time"
_TARGET_NOT_ANCESTOR = "target advanced concurrently (not an ancestor of feature)"
_TARGET_CAS_MISMATCH = "target advanced concurrently (CAS mismatch)"
_TARGET_WORKTREE_QUERY_FAILED = "target worktree lookup failed"
#: Reasons the bounded integration loop should re-attempt. Every one of
#: them says "another agent was moving this ref while I looked", never
#: "this cannot work". :data:`_TARGET_MISSING` is deliberately absent:
#: a target that genuinely does not exist will not exist on the retry
#: either, and burning the attempt budget on it hides the real cause.
_RETRYABLE_REASONS = frozenset(
    {
        _TARGET_NOT_ANCESTOR,
        _TARGET_CAS_MISMATCH,
        _TARGET_QUERY_FAILED,
        _TARGET_WORKTREE_QUERY_FAILED,
    }
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.rebase_state import RebaseState


def fast_forward_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Move the local mainline ref to ``feature_sha`` via CAS or worktree ff.

    Returns ``(fast_forwarded, skip_reason)``. ``skip_reason`` is
    empty when the fast-forward succeeded.

    AC-08 atomicity contract: the ancestry decision is BOUND to the
    same observed target SHA the CAS uses, and that pair is observed
    ONCE, as late as possible -- inside :func:`_fast_forward_via_cas`,
    immediately before the ref move. Earlier implementations checked
    ``is_ancestor(target, feature_sha)`` *before* reading the SHA,
    leaving a TOCTOU window where a concurrent landing between the
    ancestor check and the SHA read could satisfy the CAS with a SHA
    that was NOT an ancestor of ``feature_sha`` -- the bug class closed
    by this design. If the target moves between that observation and
    the CAS, the CAS fails closed (the ref no longer equals the
    observed SHA) and a concurrency skip is recorded.

    A cheap pre-flight GATE runs before any of that -- see
    :func:`_landing_gate_reason`. It is a gate and nothing more: the SHA
    it reads is deliberately not carried forward, because the worktree
    query and the ``merge --ff-only`` attempt that may follow it are
    themselves git subprocesses, and a fleet sibling can move the ref
    across them. Earlier revisions of this function called the CAS's own
    observer here and dropped its SHA on the floor with a ``_``, which
    read as an observation that bound something when it bound nothing.

    The worktree path always tries ``git merge --ff-only`` first so
    the checkout's ref, index and working tree advance together, and
    falls back to that single-observation CAS only when git refuses.
    Neither path can lose a concurrent landing or overwrite the
    worktree's uncommitted FILE content; the CAS fallback does advance
    the shared ref under a dirty target checkout, which is intentional
    and announced -- see this module's docstring.

    A FAILED worktree query is not "nobody holds the target". In this
    repository's own linked-worktree topology the mainline genuinely is
    checked out in a sibling worktree, and CAS-ing the shared ref there
    advances ``refs/heads/<target>`` while leaving that checkout's index
    and working tree behind. So a failed query fails closed with a
    RETRYABLE reason and the bounded retry loop in
    :mod:`ralph.pipeline.auto_integrate` re-attempts, rather than
    desynchronising a live checkout it could not see.

    Never force-moves the target. The skip reasons are recorded in
    the final ``RebaseState.last_reason``.
    """
    early_reason = _landing_gate_reason(repo_root, target, feature_sha)
    if early_reason:
        return False, early_reason
    primary_root = find_main_worktree_root(repo_root)
    verdict, wt = worktree_lookup(primary_root, target)
    if verdict == WORKTREE_QUERY_FAILED:
        logger.warning(
            "auto_integrate: could not determine whether '{}' is checked out; "
            "refusing to move the shared ref",
            target,
        )
        return False, _TARGET_WORKTREE_QUERY_FAILED
    if verdict == WORKTREE_FOUND and wt is not None:
        return _fast_forward_via_target_worktree(
            repo_root, wt, target, feature_sha
        )
    return _fast_forward_via_cas(repo_root, target, feature_sha)


def _landing_gate_reason(repo_root: Path, target: str, feature_sha: str) -> str:
    """Pre-flight refusal reason, or ``""`` when a landing may be attempted.

    Returns ONLY a reason. The SHA behind it is intentionally not
    surfaced: this call sits before a ``git worktree list`` query and a
    possible ``git merge --ff-only``, so any SHA read here is already
    potentially stale by the time the compare-and-swap needs one, and
    the CAS therefore takes its own observation (see
    :func:`_fast_forward_via_cas`).

    The gate earns its git subprocess twice over. It refuses a doomed
    landing before :func:`~ralph.git.operations.find_main_worktree_root`
    is called at all -- that helper RAISES on a path that is not inside
    a repository, which the fail-open integration would otherwise have
    to absorb as an opaque ``unexpected failure`` -- and it stops a
    diverged target from being handed a pointless ``merge --ff-only``
    in a live checkout.
    """
    _observed_sha, reason = _observe_landable_target(repo_root, target, feature_sha)
    return reason


def _observe_landable_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[str | None, str]:
    """Read the target SHA and check it can still be moved to ``feature_sha``.

    Returns ``(sha, skip_reason)`` with exactly one slot populated.

    AC-08 binding contract: the ancestor decision is made against the
    SHA this very call observed, never against the ref name, so the
    caller can hand that same SHA to the compare-and-swap as its
    expected-oldvalue.

    A ``rev-parse`` that FAILED is reported as the retryable
    :data:`_TARGET_QUERY_FAILED`, not as :data:`_TARGET_MISSING`. Under
    concurrency the commonest cause of that failure is a ref lock held
    by the sibling agent currently landing on the same branch, and
    calling that "the target branch does not exist" turned a situation
    a single retry resolves into a terminal skip.
    """
    observed_target_sha, query_ok = observe_branch_sha(repo_root, target)
    if not query_ok:
        return None, _TARGET_QUERY_FAILED
    if observed_target_sha is None:
        return None, _TARGET_MISSING
    if not is_ancestor(repo_root, observed_target_sha, feature_sha):
        return None, _TARGET_NOT_ANCESTOR
    return observed_target_sha, ""


def _fast_forward_via_target_worktree(
    repo_root: Path,
    worktree_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Fast-forward the target branch checked out in ``worktree_root`` (AC-09).

    ``git merge --ff-only`` is attempted first regardless of how dirty
    that checkout is: it is its own guard, refusing with a non-zero exit
    and no mutation when local changes would be overwritten or when
    ``feature_sha`` is not a descendant. Only once git has refused do we
    fall back to the CAS, which advances the shared ref alone and so
    leaves that checkout's index behind — a transient but operator-
    visible state, hence the WARN.
    """
    if fast_forward_via_worktree(worktree_root, feature_sha):
        return True, ""
    landed, reason = _fast_forward_via_cas(repo_root, target, feature_sha)
    if landed:
        logger.warning(
            "auto_integrate: advanced '{}' by ref while its worktree at {} "
            "could not fast-forward; that checkout's index is now behind",
            target,
            worktree_root,
        )
    return landed, reason


def _fast_forward_via_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Atomic CAS fast-forward of a not-checked-out target branch (AC-08).

    The target is observed HERE, as late as the design allows: in a
    fleet the target moves continuously, and the worktree query that
    precedes this call is itself a git subprocess, so a SHA read before
    it would guarantee a CAS mismatch whenever a sibling landed in the
    meantime, burning a whole retry to rediscover a value this call can
    simply read.

    Observing here does NOT weaken the atomicity contract -- the
    ancestry check and the expected-oldvalue still come from ONE
    observation, which is the property that matters -- and it lets the
    doomed case (target already past the feature tip) be reported as the
    retryable :data:`_TARGET_NOT_ANCESTOR` instead of an attempted CAS.
    """
    fresh_target_sha, reason = _observe_landable_target(
        repo_root, target, feature_sha
    )
    if fresh_target_sha is None:
        return False, reason
    if not compare_and_swap_branch(repo_root, target, fresh_target_sha, feature_sha):
        return False, _TARGET_CAS_MISMATCH
    return True, ""


def is_retryable_fast_forward_failure(reason: str) -> bool:
    """Whether a failed fast-forward reflects a transient target move."""
    return reason in _RETRYABLE_REASONS


def maybe_push_target(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    record: RebaseState,
) -> RebaseState:
    """Opt-in multi-remote push hook shared by every successful local landing.

    The auto-integrate contract is "every successful local advance of the
    shared target must reach every configured remote when push is
    enabled" -- the happy path in :func:`auto_integrate._integrate_once`
    AND the crash-recovery continuation in
    :func:`ralph.pipeline.auto_integrate_recovery._land_and_reconcile`
    both call this helper after a successful local landing.

    Never gates or alters the landing. A push failure is recorded
    on ``RebaseState.last_push`` for operator visibility; the caller's
    ``RebaseState`` is returned unchanged when push is disabled, when
    the helper raised defensively, or when the helper produced no
    summary.

    Args:
        config: Unified run configuration. ``None`` (the recovery
            preamble's pre-seam callers may not have one) skips the
            push entirely, mirroring the pre-seam byte-identical
            behaviour. Otherwise ``config.general.auto_integrate_push_enabled``
            gates the call; ``config.general.auto_integrate_push_timeout_seconds``
            sets the per-remote wall-clock budget. Both reads are
            ``getattr``-guarded so legacy configs that pre-date the
            push feature stay compatible.
        repo_root: Repository root to enumerate remotes in.
        target: Branch to push (``refs/heads/<target>:refs/heads/<target>``).
        record: The ``RebaseState`` to annotate with ``last_push``. The
            returned object is ``record`` unchanged when no push
            happened, or ``record.model_copy(update={'last_push': summary})``
            when a summary was produced.

    Returns:
        The ``RebaseState`` carrying ``last_push`` when push ran, else
        ``record`` unchanged.
    """
    if config is None:
        return record
    if not hasattr(config, "general"):
        return record
    general_obj: object = config.general
    if general_obj is None:
        return record
    push_enabled_raw: object = getattr(general_obj, "auto_integrate_push_enabled", False)
    if not (isinstance(push_enabled_raw, bool) and push_enabled_raw):
        return record
    push_timeout_raw: object = getattr(general_obj, "auto_integrate_push_timeout_seconds", 30.0)
    push_timeout_seconds: float = (
        float(push_timeout_raw)
        if isinstance(push_timeout_raw, (int, float)) and not isinstance(push_timeout_raw, bool)
        else 30.0
    )
    push_summary: str | None = None
    try:
        push_summary = push_branch_to_all_remotes(
            repo_root,
            target,
            timeout_seconds=float(push_timeout_seconds),
        )
    except Exception as push_exc:  # pragma: no cover -- defensive
        logger.warning(
            "auto_integrate: push hook raised unexpectedly: {}",
            push_exc,
        )
        push_summary = None
    if push_summary is not None:
        return record.model_copy(update={"last_push": push_summary})
    return record


__all__ = [
    "fast_forward_target",
    "is_retryable_fast_forward_failure",
    "maybe_push_target",
]


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: B11
# ladder rung: 1
# AC-14 rationale: E2
# ladder rung: 1
# AC-14 rationale: E3
# ladder rung: 3
# AC-14 rationale: E5
# ladder rung: 1
# AC-14 rationale: E6
# ladder rung: 1
# AC-14 rationale: F1
# ladder rung: 1
# AC-14 rationale: F2
# ladder rung: 3
# AC-14 rationale: F4
# ladder rung: 4
# AC-14 rationale: F5
# ladder rung: 4
# ----- end AC-14 catalog evidence -----
