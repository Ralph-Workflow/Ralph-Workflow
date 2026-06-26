"""Black-box tests for Codex home tempdir cleanup.

wt-024 memory-perf GAP-RES-01: ``_allocate_codex_home_dir`` creates a
fresh ``tempfile.mkdtemp`` on every Codex invocation that is never
removed. The fix tracks allocated dirs in ``_allocated_codex_homes``
and removes them via the standalone ``cleanup_codex_homes()`` (registered
with ``atexit``).

wt-024 memory-perf round 3: ``cleanup_codex_homes`` must reap every
home this process ever allocated, including FIFO-evicted homes whose
bookkeeping entry was dropped from the bounded deque. The fix tracks
every allocation in ``_all_allocated_codex_homes`` (a separate set
that outlives the bounded deque) so the atexit net can find
FIFO-evicted orphans.

These tests exercise ``_allocate_codex_home_dir`` + ``cleanup_codex_homes``
DIRECTLY (NOT ``prepare_codex_home_with_upstreams``, which reads and
mirrors ``~/.codex`` and would create an environment-dependent test).
The ``tmp_path`` fixture scopes the tempdirs to the test.
"""

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
    """cleanup_codex_homes must rmtree all directories this process allocated."""
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
    assert (
        _all_allocated_codex_homes == set()
    ), "lifetime tracking set should be empty after cleanup"


def test_cleanup_codex_homes_is_idempotent(tmp_path: Path) -> None:
    """Calling cleanup twice must not raise (ignore_errors + empty-after-clear)."""
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    assert d.exists()

    cleanup_codex_homes()
    # Second call: list is already empty, no-op
    cleanup_codex_homes()

    assert not d.exists()
    assert list(_allocated_codex_homes) == []
    assert _all_allocated_codex_homes == set()


def test_cleanup_codex_homes_robust_to_missing_dirs(tmp_path: Path) -> None:
    """If a registered dir was already removed externally, cleanup must not raise."""
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    # Remove it out-of-band to simulate a partial-state shutdown
    d.rmdir()
    # Sanity: dir is gone but still registered
    assert not d.exists()
    assert str(d) in _allocated_codex_homes
    assert str(d) in _all_allocated_codex_homes

    # Should NOT raise (ignore_errors=True)
    cleanup_codex_homes()

    assert list(_allocated_codex_homes) == []
    assert _all_allocated_codex_homes == set()


def test_cleanup_codex_homes_reaps_fifo_evicted_orphans(tmp_path: Path) -> None:
    """Regression for analysis-feedback wt-024 round 3:

    After ``_allocate_codex_home_dir`` evicts the oldest entry from the
    bounded deque (FIFO overflow), ``cleanup_codex_homes`` MUST still
    rmtree the evicted on-disk directory. The eviction only drops the
    bookkeeping entry from ``_allocated_codex_homes``; the home remains
    in ``_all_allocated_codex_homes`` so the atexit net can find it.

    Scenario: shrink the deque cap to 2 via module-swap, allocate 4
    homes (forcing 2 evictions), then call ``cleanup_codex_homes``
    directly. EVERY allocated path (including the 2 evicted ones)
    must be rmtree'd. Without the fix, the 2 evicted homes survive
    because ``cleanup_codex_homes`` only iterates the bounded deque.

    Proof: after the fix the test fails if ``_all_allocated_codex_homes``
    is not used (the 2 evicted homes would still be on disk).
    """
    small_cap = 2
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    try:
        # Allocate cap + 2 entries (two oldest will be evicted from
        # the bookkeeping deque).
        dirs: list = []
        for _ in range(small_cap + 2):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)

        # Sanity: the bounded deque holds only the latest entries.
        assert len(codex_module._allocated_codex_homes) == small_cap
        # Sanity: the lifetime set holds ALL allocations.
        assert len(codex_module._all_allocated_codex_homes) == small_cap + 2

        # The two oldest paths were FIFO-evicted from the deque but
        # must STILL be tracked in the lifetime set so cleanup_codex_homes
        # can find them.
        assert str(dirs[0]) not in codex_module._allocated_codex_homes
        assert str(dirs[1]) not in codex_module._allocated_codex_homes
        assert str(dirs[0]) in codex_module._all_allocated_codex_homes
        assert str(dirs[1]) in codex_module._all_allocated_codex_homes

        # Cleanup must rmtree every allocated path (registry + set).
        cleanup_codex_homes()

        for d in dirs:
            assert not d.exists(), (
                f"cleanup_codex_homes must rmtree FIFO-evicted home {d} "
                f"(round 3 regression)"
            )
        assert list(codex_module._allocated_codex_homes) == []
        assert codex_module._all_allocated_codex_homes == set()
    finally:
        # Restore the production deque + set
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set
