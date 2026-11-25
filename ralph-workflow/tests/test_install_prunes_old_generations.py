from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ralph.install_transaction import prune_generations

if TYPE_CHECKING:
    from pathlib import Path


def _generation(generations: Path, name: str, *, mtime: float) -> Path:
    path = generations / name
    (path / "ralph").mkdir(parents=True)
    os.utime(path, (mtime, mtime))
    return path


def test_old_generations_are_removed_keeping_the_new_and_previous_one(tmp_path: Path) -> None:
    oldest = _generation(tmp_path, "generation-a", mtime=1000.0)
    older = _generation(tmp_path, "generation-b", mtime=2000.0)
    previous = _generation(tmp_path, "generation-c", mtime=3000.0)
    newest = _generation(tmp_path, "generation-d", mtime=4000.0)

    removed = prune_generations(tmp_path, keep=newest, in_use=frozenset)

    assert sorted(path.name for path in removed) == ["generation-a", "generation-b"]
    assert not oldest.exists()
    assert not older.exists()
    assert previous.exists()
    assert newest.exists()


def test_a_generation_a_running_ralph_still_uses_is_never_removed(tmp_path: Path) -> None:
    running = _generation(tmp_path, "generation-running", mtime=1000.0)
    _generation(tmp_path, "generation-idle", mtime=2000.0)
    _generation(tmp_path, "generation-previous", mtime=3000.0)
    newest = _generation(tmp_path, "generation-new", mtime=4000.0)

    prune_generations(tmp_path, keep=newest, in_use=lambda: frozenset({"generation-running"}))

    assert running.exists()
    assert not (tmp_path / "generation-idle").exists()


def test_entries_that_are_not_generations_are_left_alone(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    newest = _generation(tmp_path, "generation-new", mtime=4000.0)

    assert prune_generations(tmp_path, keep=newest, in_use=frozenset) == ()
    assert (tmp_path / "notes").exists()
