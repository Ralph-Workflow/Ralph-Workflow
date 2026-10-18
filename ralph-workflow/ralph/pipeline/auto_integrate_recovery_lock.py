"""Lock-liveness probe extracted from :mod:`ralph.pipeline.auto_integrate_recovery`.

When the crash-recovery path finds a stale ``index.lock`` (or
``HEAD.lock`` / ``refs/.../lock``) left behind by a previous run, it
must decide between two outcomes:

* **Live holder**: this is *contention*, not *staleness* -- the lock
  MUST be left in place and the bounded retry loop backs off.
* **Dead holder** (no live process holds or uses the lock): the lock
  is safe to clear.

The helper is a thin wrapper around
``ralph.git.operations._lock_has_no_live_holder`` that applies the
age threshold first and returns ``True`` only when the probe confirms
``"dead"``. It is re-exported by
``ralph.pipeline._auto_integrate_recovery_git_state`` so callers in
``commit_executor`` and ``auto_integrate_recovery_legacy`` continue to
use ``_lock_holder_is_dead`` without import changes.
"""

from __future__ import annotations

import time
from pathlib import Path

from ralph.git._lock_recovery import (
    _STALE_GIT_LOCK_AGE_SECONDS as _STALE_AGE,
)
from ralph.git._lock_recovery import (
    _lock_has_no_live_holder as _liveness_probe,
)


def _lock_holder_is_dead(
    lock_path: Path | str,
    gitdir: Path | str | None = None,
    gitdir_common: Path | str | None = None,
) -> bool:
    """True when the lock has no live holder and is old enough to be stale.

    A lock is removed only when:
      (a) it is older than ``_STALE_GIT_LOCK_AGE_SECONDS`` (10 s), AND
      (b) no live process holds it open and no live ``git`` process
          is running against the worktree-private or common gitdir.

    Returns ``True`` only on the combined ``"dead"`` result from the
    liveness probe. On ``"live"`` or ``"unknown"`` the lock is left
    in place (fail-closed).
    """
    path = Path(lock_path)
    # filesystem-read-ok: probe git lock existence outside workspace
    if not path.exists():
        return True  # already gone
    try:
        # filesystem-read-ok: git lock age determination via mtime stat probe
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age < _STALE_AGE:
        return False  # too young to be proven stale
    resolved_gitdir = path.parent if gitdir is None else Path(gitdir)
    common_gitdir = Path(gitdir_common) if gitdir_common is not None else None
    result = _liveness_probe(path, resolved_gitdir, gitdir_common=common_gitdir)
    return result == "dead"
