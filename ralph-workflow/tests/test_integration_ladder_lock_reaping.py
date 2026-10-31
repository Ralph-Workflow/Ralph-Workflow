"""Rung 6's stale-lock removal is gated and scoped to this worktree.

The last-resort rung removes git locks left behind by a crashed writer.
A lock is only ever removed through the shared stale-lock reaper (age,
liveness and unchanged identity), and only from this worktree's private
git dir plus the common dir's shared top-level and ref locks -- never a
sibling linked worktree's private git dir. The git dirs are laid out on
``tmp_path`` by hand, so no git process is spawned.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ralph.pipeline.integration_ladder import reap_worktree_stale_locks

pytestmark = pytest.mark.timeout_seconds(5)

_AN_HOUR_AGO_S = 3600.0


def _git_dir(root: Path) -> Path:
    git_dir = root / ".git"
    for sub in ("objects", "refs/heads", "logs"):
        (git_dir / sub).mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return git_dir


def _linked_private_dir(common: Path, name: str) -> Path:
    private = common / "worktrees" / name
    (private / "refs").mkdir(parents=True)
    (private / "HEAD").write_text("ref: refs/heads/other\n", encoding="utf-8")
    (private / "commondir").write_text("../..\n", encoding="utf-8")
    return private


def _aged(lock: Path) -> Path:
    lock.write_text("", encoding="utf-8")
    stamp = lock.stat().st_mtime - _AN_HOUR_AGO_S
    os.utime(lock, (stamp, stamp))
    return lock


def test_a_sibling_worktrees_locks_are_never_touched(tmp_path: Path) -> None:
    """An orphaned-looking lock in a sibling's private git dir is not ours to reap."""
    common = _git_dir(tmp_path / "main")
    sibling = _linked_private_dir(common, "sibling")
    sibling_index = _aged(sibling / "index.lock")
    sibling_ref = _aged(sibling / "refs" / "bisect.lock")

    removed = reap_worktree_stale_locks(common, common)

    assert removed == ()
    assert sibling_index.exists()
    assert sibling_ref.exists()


def test_a_fresh_lock_in_this_worktree_is_never_removed(tmp_path: Path) -> None:
    """A brand-new lock may belong to a live writer: the age gate refuses it."""
    git_dir = _git_dir(tmp_path / "main")
    index_lock = git_dir / "index.lock"
    index_lock.write_text("", encoding="utf-8")
    ref_lock = git_dir / "refs" / "heads" / "feature.lock"
    ref_lock.write_text("", encoding="utf-8")

    removed = reap_worktree_stale_locks(git_dir, git_dir)

    assert removed == ()
    assert index_lock.exists()
    assert ref_lock.exists()


def test_a_linked_worktree_never_reaps_the_main_worktrees_private_locks(
    tmp_path: Path,
) -> None:
    """From a linked worktree, the common dir's ``index.lock`` is the MAIN checkout's."""
    common = _git_dir(tmp_path / "main")
    private = _linked_private_dir(common, "mine")
    main_index = _aged(common / "index.lock")
    main_head = _aged(common / "HEAD.lock")

    removed = reap_worktree_stale_locks(private, common)

    assert removed == ()
    assert main_index.exists()
    assert main_head.exists()
