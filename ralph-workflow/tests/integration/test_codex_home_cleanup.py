from __future__ import annotations

import collections
from typing import TYPE_CHECKING

from ralph.mcp.transport import codex as codex_module
from ralph.mcp.transport.codex import (
    _all_allocated_codex_homes,
    _allocate_codex_home_dir,
    _allocated_codex_homes,
    cleanup_codex_homes,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_home_tempdirs_cleaned_up(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    d1 = _allocate_codex_home_dir(workspace_path=tmp_path)
    d2 = _allocate_codex_home_dir(workspace_path=tmp_path)

    assert d1.exists() and d2.exists()
    assert str(d1) in _allocated_codex_homes, "allocation 1 should be registered"
    assert str(d2) in _allocated_codex_homes, "allocation 2 should be registered"
    assert str(d1) in _all_allocated_codex_homes, "allocation 1 should be in lifetime set"
    assert str(d2) in _all_allocated_codex_homes, "allocation 2 should be in lifetime set"

    cleanup_codex_homes()

    assert not d1.exists(), f"tempdir {d1} should have been removed"
    assert not d2.exists(), f"tempdir {d2} should have been removed"
    assert list(_allocated_codex_homes) == [], "registration list should be empty after cleanup"
    assert _all_allocated_codex_homes == set(), (
        "lifetime tracking set should be empty after cleanup"
    )


def test_cleanup_codex_homes_is_idempotent(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    assert d.exists()

    cleanup_codex_homes()
    cleanup_codex_homes()

    assert not d.exists()
    assert list(_allocated_codex_homes) == []
    assert _all_allocated_codex_homes == set()


def test_cleanup_codex_homes_robust_to_missing_dirs(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    d.rmdir()
    assert not d.exists()
    assert str(d) in _allocated_codex_homes
    assert str(d) in _all_allocated_codex_homes

    cleanup_codex_homes()

    assert list(_allocated_codex_homes) == []
    assert _all_allocated_codex_homes == set()


def test_cleanup_codex_homes_keeps_lifetime_cap_evictions_on_disk(tmp_path: Path) -> None:
    small_cap = 2
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    original_cap = codex_module._ALL_CODEX_HOMES_CAP
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    codex_module._ALL_CODEX_HOMES_CAP = small_cap
    try:
        dirs = [_allocate_codex_home_dir(workspace_path=tmp_path) for _ in range(4)]

        assert len(codex_module._allocated_codex_homes) == small_cap
        assert len(codex_module._all_allocated_codex_homes) == small_cap
        assert all(directory.exists() for directory in dirs)

        cleanup_codex_homes()

        assert sum(directory.exists() for directory in dirs) == small_cap
        assert list(codex_module._allocated_codex_homes) == []
        assert codex_module._all_allocated_codex_homes == set()
    finally:
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set
        codex_module._ALL_CODEX_HOMES_CAP = original_cap


def test_cleanup_codex_homes_reaps_fifo_evicted_orphans(tmp_path: Path) -> None:
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=2)
    codex_module._all_allocated_codex_homes = set()
    try:
        dirs = [_allocate_codex_home_dir(workspace_path=tmp_path) for _ in range(4)]

        assert len(codex_module._allocated_codex_homes) == 2
        assert len(codex_module._all_allocated_codex_homes) == 4

        cleanup_codex_homes()

        assert all(not directory.exists() for directory in dirs)
        assert list(codex_module._allocated_codex_homes) == []
        assert codex_module._all_allocated_codex_homes == set()
    finally:
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set
