"""Black-box tests for the FIFO-bounded upstream tool catalog cache.

wt-024 Step 4 (AC-02): ``_CACHE`` in
``ralph/mcp/upstream/tool_catalog_cache.py`` is now an
``OrderedDict`` with a ``_MAX_CACHE_ENTRIES = 32`` cap. Inserting
more than the cap evicts the oldest entry (FIFO), so a session
that touches many distinct workspace roots cannot grow the cache
unboundedly. ``get_tool_catalog`` and ``clear_tool_catalog`` still
behave identically from the public contract; the change is purely
a memory-growth fix.

Each test creates a fresh ``tmp_path`` and seeds a deterministic
``UpstreamTool`` per workspace so the assertions don't depend on
real upstream servers. No real subprocess, no time.sleep.

The cache is a module-level singleton; each test clears it via a
``_clear_cache`` fixture so the assertions can count entries
exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.upstream import tool_catalog_cache
from ralph.mcp.upstream.upstream_tool import UpstreamTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    tool_catalog_cache._CACHE.clear()


def _seed_workspace(path: Path, tool_name: str) -> None:
    """Cache a single, deterministic tool under the given workspace root."""
    tool_catalog_cache.cache_tool_catalog(
        path,
        {tool_name: [UpstreamTool(name=tool_name, description=f"tool for {path.name}")]},
    )


def test_cache_tool_catalog_evicts_oldest_workspace_when_cap_exceeded(
    tmp_path: Path,
) -> None:
    """FIFO eviction: oldest workspace is dropped when the cap is exceeded."""
    cap = tool_catalog_cache._MAX_CACHE_ENTRIES

    # Insert cap+5 distinct workspaces, each with a unique tool.
    oldest_paths = [tmp_path / f"old-{i}" for i in range(5)]
    newest_paths = [tmp_path / f"new-{i}" for i in range(cap)]

    for index, path in enumerate(oldest_paths):
        _seed_workspace(path, f"old-{index}")
    for index, path in enumerate(newest_paths):
        _seed_workspace(path, f"new-{index}")

    # The 5 oldest workspaces MUST have been evicted.
    for index, path in enumerate(oldest_paths):
        assert tool_catalog_cache.get_tool_catalog(path) == {}, (
            f"oldest workspace old-{index} must be evicted when cap exceeded"
        )

    # The most recent cap workspaces MUST still be cached.
    for index, path in enumerate(newest_paths):
        catalog = tool_catalog_cache.get_tool_catalog(path)
        assert f"new-{index}" in catalog, (
            f"newest workspace new-{index} must still be cached"
        )

    # Total entries in the cache MUST be exactly the cap.
    assert len(tool_catalog_cache._CACHE) == cap, (
        f"cache must hold exactly {cap} entries, got {len(tool_catalog_cache._CACHE)}"
    )


def test_cache_tool_catalog_does_not_evict_when_under_cap(tmp_path: Path) -> None:
    """Inserts under the cap never evict anything."""
    cap = tool_catalog_cache._MAX_CACHE_ENTRIES
    paths = [tmp_path / f"ws-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"ws-{index}")
    for index, path in enumerate(paths):
        assert f"ws-{index}" in tool_catalog_cache.get_tool_catalog(path)
    assert len(tool_catalog_cache._CACHE) == cap


def test_cache_tool_catalog_refreshes_existing_key_position(tmp_path: Path) -> None:
    """Re-inserting an existing key moves it to the back (FIFO refresh)."""
    cap = tool_catalog_cache._MAX_CACHE_ENTRIES
    # Fill to the cap; the LAST key is at the back.
    paths = [tmp_path / f"keep-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"keep-{index}")
    # Re-insert the FIRST key. It should move to the back and the
    # previously-second key becomes the oldest.
    _seed_workspace(paths[0], "keep-0-refreshed")
    # Insert one MORE key to trigger eviction of the current oldest.
    extra = tmp_path / "extra"
    _seed_workspace(extra, "extra")

    # The first key (refreshed) must still be cached.
    assert "keep-0-refreshed" in tool_catalog_cache.get_tool_catalog(paths[0])
    # The previously-second key (which is now the oldest) MUST be evicted.
    assert tool_catalog_cache.get_tool_catalog(paths[1]) == {}
    # The newest key must be present.
    assert "extra" in tool_catalog_cache.get_tool_catalog(extra)


def test_clear_tool_catalog_removes_one_entry_without_disturbing_others(
    tmp_path: Path,
) -> None:
    """clear_tool_catalog pops exactly one entry; the cap is preserved."""
    cap = tool_catalog_cache._MAX_CACHE_ENTRIES
    paths = [tmp_path / f"keep-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"keep-{index}")

    # Clear the middle key.
    target = paths[cap // 2]
    tool_catalog_cache.clear_tool_catalog(target)

    assert tool_catalog_cache.get_tool_catalog(target) == {}
    for index, path in enumerate(paths):
        if path == target:
            continue
        assert f"keep-{index}" in tool_catalog_cache.get_tool_catalog(path)
    assert len(tool_catalog_cache._CACHE) == cap - 1


def test_cache_key_collision_does_not_grow_cache(tmp_path: Path) -> None:
    """Re-inserting the SAME workspace overwrites in place; the cap is unaffected."""
    path = tmp_path / "single"
    for index in range(5):
        _seed_workspace(path, f"v-{index}")
    assert len(tool_catalog_cache._CACHE) == 1
    # The most recent value wins.
    assert "v-4" in tool_catalog_cache.get_tool_catalog(path)
