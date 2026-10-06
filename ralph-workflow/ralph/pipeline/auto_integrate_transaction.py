"""Exclusive worktree ownership across integration and crash recovery."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_catchup_coordination import _try_lock, _unlock

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def integration_transaction(root: Path) -> Iterator[bool]:
    """Nonblocking lease; a contended worktree remains in retry without mutation."""
    if not (root / ".git").exists():
        yield True
        return
    observed = run_git(("rev-parse", "--absolute-git-dir"), cwd=root, label="integration:lock-dir")
    if observed.returncode != 0 or not observed.stdout.strip():
        yield False
        return
    path = Path(observed.stdout.strip()) / "ralph-auto-integrate.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")  # filesystem-write-ok: persistent integration advisory-lock inode
    except OSError:
        yield False
        return
    with handle:
        acquired = _try_lock(handle)
        try:
            yield acquired
        finally:
            if acquired:
                _unlock(handle)
