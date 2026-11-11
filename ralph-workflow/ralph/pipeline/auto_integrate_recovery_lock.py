
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
    path = Path(lock_path)
    if not path.exists():
        return True
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    if age < _STALE_AGE:
        return False
    resolved_gitdir = path.parent if gitdir is None else Path(gitdir)
    common_gitdir = Path(gitdir_common) if gitdir_common is not None else None
    result = _liveness_probe(path, resolved_gitdir, gitdir_common=common_gitdir)
    return result == "dead"
