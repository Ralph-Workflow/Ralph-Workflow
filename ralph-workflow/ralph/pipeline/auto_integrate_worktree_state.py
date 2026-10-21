"""Legacy Boolean cleanliness shims over the ONE worktree classifier.

The auto-integration orchestrator asks one narrow question about the
local repository on every boundary seam: ``is the worktree free of
uncommitted TRACKED changes that would corrupt a rebase/merge in
flight?`` :func:`_worktree_is_clean` answers it.

The DECISION now has exactly one owner:
:func:`ralph.pipeline.integration_observation.classify_worktree`. This
module keeps only the two things it must still own itself:

1. the git invocation, which stays here because
   ``tests/test_auto_integrate_boundary_cleanliness.py`` pins its exact
   argv and its ``run_git`` module symbol as the monkeypatch seam; and
2. the projection of a :class:`~ralph.pipeline.integration_observation.WorktreeVerdict`
   back onto the bare ``bool`` this legacy signature promises.

Everything that decides *what a status blob means* — which codes are
unmerged, whether untracked paths block, and above all that an
unreadable probe is ``UNKNOWN`` and not a fact — lives in
:mod:`ralph.pipeline.integration_observation`.

The helper lives in a dedicated module so
:mod:`ralph.pipeline.auto_integrate` stays under the
``_MAX_FILE_LINES`` cap while the boundary-deferral path keeps
reading ``_worktree_is_clean`` from the auto-integrate namespace via
``from ralph.pipeline.auto_integrate_worktree_state import
_worktree_is_clean``. That re-export preserves the existing
``monkeypatch.setattr(ai, "_worktree_is_clean", ...)`` seams used by
``tests/test_auto_integrate_boundary_cleanliness.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.integration_observation import (
    Reading,
    WorktreeState,
    WorktreeVerdict,
    classify_worktree,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["_worktree_is_clean", "worktree_is_clean"]


def _verdict(root: Path) -> WorktreeVerdict:
    """Classify ``root`` through the shared classifier.

    The probe keeps this module's own ``--untracked-files=no`` argv: the
    boundary contract is defined on TRACKED changes only, so an untracked
    scratch file can never disable integration for every later seam (see
    ``ralph.git.rebase.rebase_preconditions._ensure_clean_worktree`` for
    the same definition at the commit seam). The classifier still sees
    the blob, so any untracked entry that ever does appear is classified
    as :attr:`WorktreeState.UNTRACKED` — clean for dispatch — instead of
    being read as blocking.

    A raised or non-zero git call yields
    :attr:`~ralph.pipeline.integration_observation.WorktreeState.UNKNOWN`,
    which the classifier reports as ``dispatch=UNKNOWN``: never clean and
    never blocked.
    """
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
            cwd=root,
            label="git-transition-status",
        )
    except Exception:
        return classify_worktree(WorktreeState.UNKNOWN)
    if result.returncode != 0:
        return classify_worktree(WorktreeState.UNKNOWN)
    return classify_worktree(Reading.known(result.stdout))


def _worktree_is_clean(root: Path) -> bool:
    """True when no uncommitted TRACKED modification is present.

    A thin projection of :func:`_verdict` onto the bare ``bool`` this
    legacy signature promises. Untracked work in flight is still safe:
    ``git rebase`` and ``git merge`` refuse non-destructively and
    per-file for any untracked path they would overwrite, and that
    refusal already routes into the endpoint-merge fallback via
    :func:`ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`.

    Known limitation, pinned by
    ``tests/test_integration_gate_parity.py``: this signature cannot
    express the classifier's three-valued verdict, so an unreadable
    probe still collapses to ``False`` here. ``False`` means "not clean"
    to a caller, i.e. the boundary defers — which is the fail-closed
    behaviour ``tests/test_auto_integrate_boundary_cleanliness.py``
    pins. Every gate that can back off must ask
    :func:`ralph.pipeline.integration_observation.classify_worktree`
    instead of this shim.
    """
    return _verdict(root).dispatch_allowed


worktree_is_clean = _worktree_is_clean
