"""Worktree-state probes extracted from :mod:`ralph.pipeline.auto_integrate`.

The auto-integration orchestrator asks one narrow question about the
local repository on every boundary seam: ``is the worktree free of
uncommitted TRACKED changes that would corrupt a rebase/merge in
flight?`` :func:`_worktree_is_clean` answers it with a single
``git status --porcelain --untracked-files=no`` probe.

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

if TYPE_CHECKING:
    from pathlib import Path


def _worktree_is_clean(root: Path) -> bool:
    """True when no uncommitted TRACKED modification is present.

    Uses the SAME definition of "clean" as
    :func:`ralph.git.rebase.rebase_preconditions._ensure_clean_worktree`
    (``git status --porcelain --untracked-files=no``), and for the same
    reason its docstring records: blocking on untracked files "turned a
    per-file, git-detectable hazard into a run-wide outage: one scratch
    file left by a phase disabled integration for every later commit
    seam".

    This guard used to be the one asymmetric holdout, and it sits on the
    only seam that carries ANOTHER agent's landing to an agent that is
    not committing right now. So the asymmetry re-created exactly that
    outage on the seam where it hurts most: a single stray scratch file
    silently disabled cross-agent synchronisation for the rest of the
    run.

    Untracked work in flight is still safe: ``git rebase`` and
    ``git merge`` refuse non-destructively and per-file for any
    untracked path they would overwrite, and that refusal already routes
    into the endpoint-merge fallback via
    :func:`ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`.
    Uncommitted TRACKED modifications still defer the boundary.

    Fails closed (False) on any git failure so the phase-transition
    hook never integrates on top of a worktree it cannot prove clean.
    """
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
        cwd=root,
        label="git-transition-status",
    )
    if result.returncode != 0:
        return False
    return not result.stdout.strip()

