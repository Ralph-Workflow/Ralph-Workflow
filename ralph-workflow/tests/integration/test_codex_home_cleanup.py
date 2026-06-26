"""Black-box tests for Codex home tempdir cleanup.

wt-024 memory-perf GAP-RES-01: ``_allocate_codex_home_dir`` creates a
fresh ``tempfile.mkdtemp`` on every Codex invocation that is never
removed. The fix tracks allocated dirs in ``_allocated_codex_homes``
and removes them via the standalone ``cleanup_codex_homes()`` (registered
with ``atexit``).

These tests exercise ``_allocate_codex_home_dir`` + ``cleanup_codex_homes``
DIRECTLY (NOT ``prepare_codex_home_with_upstreams``, which reads and
mirrors ``~/.codex`` and would create an environment-dependent test).
The ``tmp_path`` fixture scopes the tempdirs to the test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.transport.codex import (
    _allocate_codex_home_dir,
    _allocated_codex_homes,
    cleanup_codex_homes,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_home_tempdirs_cleaned_up(tmp_path: Path) -> None:
    """cleanup_codex_homes must rmtree all directories this process allocated."""
    _allocated_codex_homes.clear()

    d1 = _allocate_codex_home_dir(workspace_path=tmp_path)
    d2 = _allocate_codex_home_dir(workspace_path=tmp_path)

    assert d1.exists() and d2.exists()
    assert str(d1) in _allocated_codex_homes, "allocation 1 should be registered"
    assert str(d2) in _allocated_codex_homes, "allocation 2 should be registered"

    cleanup_codex_homes()

    assert not d1.exists(), f"tempdir {d1} should have been removed"
    assert not d2.exists(), f"tempdir {d2} should have been removed"
    assert list(_allocated_codex_homes) == [], "registration list should be empty after cleanup"


def test_cleanup_codex_homes_is_idempotent(tmp_path: Path) -> None:
    """Calling cleanup twice must not raise (ignore_errors + empty-after-clear)."""
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    assert d.exists()

    cleanup_codex_homes()
    # Second call: list is already empty, no-op
    cleanup_codex_homes()

    assert not d.exists()
    assert list(_allocated_codex_homes) == []


def test_cleanup_codex_homes_robust_to_missing_dirs(tmp_path: Path) -> None:
    """If a registered dir was already removed externally, cleanup must not raise."""
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    # Remove it out-of-band to simulate a partial-state shutdown
    d.rmdir()
    # Sanity: dir is gone but still registered
    assert not d.exists()
    assert str(d) in _allocated_codex_homes

    # Should NOT raise (ignore_errors=True)
    cleanup_codex_homes()

    assert list(_allocated_codex_homes) == []
