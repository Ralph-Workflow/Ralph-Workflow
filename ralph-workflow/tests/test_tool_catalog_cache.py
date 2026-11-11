from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.upstream import tool_catalog_cache
from ralph.mcp.upstream.upstream_tool import UpstreamTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    tool_catalog_cache.reset_tool_catalog_cache()


def _seed_workspace(path: Path, tool_name: str) -> None:
    tool_catalog_cache.cache_tool_catalog(
        path,
        {tool_name: [UpstreamTool(name=tool_name, description=f"tool for {path.name}")]},
    )


def test_cache_tool_catalog_evicts_oldest_workspace_when_cap_exceeded(
    tmp_path: Path,
) -> None:
    cap = tool_catalog_cache.cache_capacity()

    oldest_paths = [tmp_path / f"old-{i}" for i in range(5)]
    newest_paths = [tmp_path / f"new-{i}" for i in range(cap)]

    for index, path in enumerate(oldest_paths):
        _seed_workspace(path, f"old-{index}")
    for index, path in enumerate(newest_paths):
        _seed_workspace(path, f"new-{index}")

    cached_keys = set(tool_catalog_cache.cached_workspace_keys())
    evicted_keys = {tool_catalog_cache.cache_key(path) for path in oldest_paths}
    assert not (evicted_keys & cached_keys), (
        "oldest workspaces must be evicted from the cache when cap exceeded"
    )

    for index, path in enumerate(newest_paths):
        catalog = tool_catalog_cache.get_tool_catalog(path)
        assert f"new-{index}" in catalog, f"newest workspace new-{index} must still be cached"

    assert tool_catalog_cache.cached_entry_count() == cap, (
        f"cache must hold exactly {cap} entries, got {tool_catalog_cache.cached_entry_count()}"
    )


def test_cache_tool_catalog_does_not_evict_when_under_cap(tmp_path: Path) -> None:
    cap = tool_catalog_cache.cache_capacity()
    paths = [tmp_path / f"ws-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"ws-{index}")
    for index, path in enumerate(paths):
        assert f"ws-{index}" in tool_catalog_cache.get_tool_catalog(path)
    assert tool_catalog_cache.cached_entry_count() == cap


def test_cache_tool_catalog_refreshes_existing_key_position(tmp_path: Path) -> None:
    cap = tool_catalog_cache.cache_capacity()
    paths = [tmp_path / f"keep-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"keep-{index}")
    _seed_workspace(paths[0], "keep-0-refreshed")
    extra = tmp_path / "extra"
    _seed_workspace(extra, "extra")

    assert "keep-0-refreshed" in tool_catalog_cache.get_tool_catalog(paths[0])
    assert tool_catalog_cache.cache_key(paths[1]) not in tool_catalog_cache.cached_workspace_keys()
    assert "extra" in tool_catalog_cache.get_tool_catalog(extra)


def test_clear_tool_catalog_removes_one_entry_without_disturbing_others(
    tmp_path: Path,
) -> None:
    cap = tool_catalog_cache.cache_capacity()
    paths = [tmp_path / f"keep-{i}" for i in range(cap)]
    for index, path in enumerate(paths):
        _seed_workspace(path, f"keep-{index}")

    target = paths[cap // 2]
    tool_catalog_cache.clear_tool_catalog(target)

    assert tool_catalog_cache.get_tool_catalog(target) == {}
    for index, path in enumerate(paths):
        if path == target:
            continue
        assert f"keep-{index}" in tool_catalog_cache.get_tool_catalog(path)
    assert tool_catalog_cache.cached_entry_count() == cap - 1


def test_cache_key_collision_does_not_grow_cache(tmp_path: Path) -> None:
    path = tmp_path / "single"
    for index in range(5):
        _seed_workspace(path, f"v-{index}")
    assert tool_catalog_cache.cached_entry_count() == 1
    assert "v-4" in tool_catalog_cache.get_tool_catalog(path)
