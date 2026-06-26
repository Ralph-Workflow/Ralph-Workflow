"""wt-024 memory-perf GAP-RES-01 follow-up: test the NORMAL production release path.

The companion ``test_codex_home_cleanup.py`` covers the atexit-only cleanup path
(``cleanup_codex_homes()`` at interpreter shutdown). The analysis feedback
identified that the original registry marker was misleading: ``atexit``-only
cleanup means the in-memory ``_allocated_codex_homes`` registry grew
monotonically across the entire interpreter lifetime and the on-disk dirs
persisted until shutdown.

The fix in this PR:
  1. Convert the registry to ``collections.deque(maxlen=_DEFAULT_CODEX_HOME_CAP)``
     so even an unbounded call pattern is bounded in memory.
  2. ``_allocate_codex_home_dir`` rmtree's the FIFO-evicted oldest entry on
     every append past the cap so the on-disk footprint stays bounded too.
  3. Add a ``release_codex_home(home)`` function for callers (e.g. the
     MCP probe) that allocate a codex home, use it for a bounded operation,
     and want to release it BEFORE interpreter shutdown.
  4. ``_probe_codex`` in agent_probe.py uses try/finally to release the
     codex home it allocated -- the "normal production release path".

These tests verify:
  - ``release_codex_home`` removes the home from the registry AND rmtree's
    the on-disk directory.
  - ``release_codex_home`` returns ``False`` for unregistered paths (no-op).
  - Repeated allocation past the FIFO cap evicts the oldest entry (registry
    bounded) AND the evicted on-disk dir is rmtree'd.
  - ``_probe_codex`` releases the codex home it allocated during normal
    production flow (not just at interpreter exit).
"""

from __future__ import annotations

import collections
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.startup import PreflightError
from ralph.mcp.transport import codex as codex_module
from ralph.mcp.transport.codex import (
    _allocate_codex_home_dir,
    _allocated_codex_homes,
    cleanup_codex_homes,
    release_codex_home,
)
from ralph.mcp.upstream.agent_probe import _probe_codex
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from pathlib import Path


def test_release_codex_home_removes_from_registry_and_disk(tmp_path: Path) -> None:
    """``release_codex_home(home)`` rmtree's the dir and removes from registry."""
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    assert d.exists()
    assert str(d) in _allocated_codex_homes

    released = release_codex_home(str(d))

    assert released is True, "release_codex_home must return True for registered homes"
    assert not d.exists(), "on-disk dir MUST be rmtree'd"
    assert str(d) not in _allocated_codex_homes, "registry MUST be drained"


def test_release_codex_home_returns_false_for_unknown_path(tmp_path: Path) -> None:
    """``release_codex_home(unknown)`` returns False and is a no-op."""
    _allocated_codex_homes.clear()

    fake = str(tmp_path / "never-allocated-home")
    # Sanity: not in registry, doesn't exist on disk
    assert fake not in _allocated_codex_homes
    assert not (tmp_path / "never-allocated-home").exists()

    released = release_codex_home(fake)

    assert released is False, "release_codex_home(unknown) MUST return False"
    assert list(_allocated_codex_homes) == [], "registry MUST remain empty"


def test_release_codex_home_is_idempotent_per_home(tmp_path: Path) -> None:
    """Calling ``release_codex_home(home)`` twice: second call returns False."""
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)

    assert release_codex_home(str(d)) is True
    # Second call: home was already removed, returns False
    assert release_codex_home(str(d)) is False


def test_allocate_past_cap_evicts_oldest_with_disk_cleanup(tmp_path: Path) -> None:
    """When the FIFO deque is at cap, the next allocation evicts the oldest
    entry AND rmtree's its on-disk directory. This is the in-memory +
    on-disk bound that makes ``_allocated_codex_homes`` a true bounded
    accumulator (not just the previous atexit-only marker).
    """
    # Shrink the cap for a fast deterministic test by swapping the
    # deque with a smaller-maxlen one. We restore the original in finally.
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    try:
        # Allocate cap + 1 entries
        dirs: list = []
        for _ in range(small_cap + 1):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)

        # Registry must be bounded at the cap
        assert len(codex_module._allocated_codex_homes) == small_cap, (
            f"registry must be capped at {small_cap}; "
            f"got {len(codex_module._allocated_codex_homes)}"
        )

        # The OLDEST entry must be evicted (and its on-disk dir rmtree'd)
        assert not dirs[0].exists(), (
            f"oldest allocation {dirs[0]} must have been rmtree'd on FIFO eviction"
        )
        # The newest entries must still exist
        for d in dirs[1:]:
            assert d.exists(), f"recent allocation {d} must still exist on disk"
    finally:
        # Clean up any remaining on-disk dirs before restoring
        cleanup_codex_homes()
        # Restore the production deque (with original cap)
        codex_module._allocated_codex_homes = original_deque


def test_probe_codex_releases_home_in_normal_flow(tmp_path: Path) -> None:
    """``_probe_codex`` must release the codex home it allocated (the
    "normal production release path") -- not rely on atexit.

    Regression for the analysis-feedback finding that ``atexit``-only
    cleanup leaves every allocated home on disk for the entire
    interpreter lifetime and grows the registry unboundedly.
    """
    _allocated_codex_homes.clear()

    # Pass a fake server; the probe will raise PreflightError (via
    # server_handshake -> httpx connection refused). We catch the
    # exception -- what matters is the finally block releases the home.
    server = UpstreamMcpServer(
        name="test-release-path",
        transport="http",
        url="http://127.0.0.1:1",  # unreachable; handshake will fail
        command=None,
        args=(),
    )

    with pytest.raises(PreflightError):
        _probe_codex(server, workspace_path=tmp_path)

    # The finally block MUST have released the home, even though the
    # probe raised mid-flow. Registry is empty, no stray dirs on disk.
    assert list(_allocated_codex_homes) == [], (
        f"registry must be empty after probe raises; got {_allocated_codex_homes}"
    )
    # Sanity: any dirs that were created under tmp_path must be rmtree'd.
    remaining = [p for p in tmp_path.iterdir() if p.name.startswith("codex-home-")]
    assert not remaining, f"stray codex home dirs on disk: {remaining}"


def test_probe_codex_registry_does_not_grow_across_repeated_calls(
    tmp_path: Path,
) -> None:
    """Multiple _probe_codex calls must NOT accumulate entries in the
    registry. The registry length must remain bounded across N probes.
    Regression for the analysis feedback ("monotonic growth of the
    registry between process start and exit").
    """
    _allocated_codex_homes.clear()

    server = UpstreamMcpServer(
        name="test-no-growth",
        transport="http",
        url="http://127.0.0.1:1",
        command=None,
        args=(),
    )

    n_iterations = 10
    for _ in range(n_iterations):
        with pytest.raises(PreflightError):
            _probe_codex(server, workspace_path=tmp_path)
        # After EACH iteration, registry must be empty
        assert list(_allocated_codex_homes) == [], "registry MUST be drained after each probe call"

    # Final invariant: after N iterations, registry is empty
    assert len(_allocated_codex_homes) == 0
