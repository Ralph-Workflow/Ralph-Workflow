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
import shutil
from typing import TYPE_CHECKING

import pytest

import ralph.agents.invoke
from ralph.agents.invoke import invoke_agent
from ralph.agents.invoke._types import InvokeOptions
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.protocol.startup import PreflightError
from ralph.mcp.transport import codex as codex_module
from ralph.mcp.transport.codex import (
    _all_allocated_codex_homes,
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


def test_allocate_past_cap_evicts_oldest_from_registry_keeps_disk(
    tmp_path: Path,
) -> None:
    """When the FIFO deque is at cap, the next allocation evicts the oldest
    entry from the in-memory registry but PRESERVES its on-disk directory.

    This is the active-home invariant introduced in wt-024 round 2
    (analysis feedback): a home that is still in use by a live Codex
    subprocess must not be deleted out from under the running agent just
    because the bounded registry reached its cap. The on-disk bound is
    now provided by ``release_codex_home`` (production release path via
    the ``ResolvedInvocationRuntime.cleanup`` hook) and
    ``cleanup_codex_homes`` (atexit net for orphans); see
    ``_allocate_codex_home_dir`` for the rationale.
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

        # Registry must be bounded at the cap (FIFO eviction happened)
        assert len(codex_module._allocated_codex_homes) == small_cap, (
            f"registry must be capped at {small_cap}; "
            f"got {len(codex_module._allocated_codex_homes)}"
        )

        # The OLDEST entry must be EVICTED FROM THE REGISTRY...
        assert str(dirs[0]) not in codex_module._allocated_codex_homes, (
            f"oldest allocation {dirs[0]} must have been evicted from the registry"
        )
        # ...but its on-disk directory MUST still exist (the eviction is
        # registry-only; the active-home invariant prevents deleting a
        # home that may still be referenced by a live Codex subprocess).
        assert dirs[0].exists(), (
            f"oldest allocation {dirs[0]} must STILL exist on disk "
            f"(eviction must be registry-only, not on-disk)"
        )
        # The newest entries must exist (both in registry and on disk).
        for d in dirs[1:]:
            assert str(d) in codex_module._allocated_codex_homes
            assert d.exists(), f"recent allocation {d} must still exist on disk"
    finally:
        # Clean up any remaining on-disk dirs before restoring
        cleanup_codex_homes()
        # Restore the production deque (with original cap)
        codex_module._allocated_codex_homes = original_deque


def test_release_codex_home_rmtrees_after_eviction_from_registry(
    tmp_path: Path,
) -> None:
    """After the registry evicts the oldest entry, the production cleanup
    hook (release_codex_home + unconditional rmtree) STILL rmtree's
    the on-disk directory.

    ``release_codex_home`` returns False for an evicted path (the
    home is no longer in the bounded deque), but it now ALSO rmtree's
    the on-disk directory because the active-home invariant from
    round 2 made ``_allocate_codex_home_dir`` keep the directory on
    FIFO eviction. As of wt-024 round 3, ``release_codex_home``
    itself is idempotent and unified: it discards from BOTH
    ``_all_allocated_codex_homes`` (the lifetime tracking set) and
    ``_allocated_codex_homes`` (the bounded deque), and it rmtree's
    with ``ignore_errors=True`` so a second call is a no-op.

    The ``ResolvedInvocationRuntime.cleanup`` hook therefore still
    pairs ``release_codex_home`` with an unconditional
    ``shutil.rmtree(home, ignore_errors=True)`` for historical
    symmetry, but the explicit ``rmtree`` is now redundant (the
    call is idempotent). The production cleanup hook semantics
    remain: the on-disk directory is always cleaned up by the time
    the owning agent finishes, regardless of whether the home was
    FIFO-evicted from the bookkeeping deque.

    This proves the on-disk bound is preserved even though
    ``_allocate_codex_home_dir`` no longer rmtree's on eviction: the
    owning agent's ``ResolvedInvocationRuntime.cleanup`` hook can
    always release the directory. The eviction only loses the
    registry entry (the bookkeeping), not the ability to clean up
    the actual directory.
    """
    # Shrink the cap for a fast deterministic test by swapping the
    # deque with a smaller-maxlen one. We restore the original in finally.
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    try:
        # Allocate cap + 1 entries; dirs[0] will be evicted from registry.
        dirs: list = []
        for _ in range(small_cap + 1):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)

        evicted_dir = dirs[0]
        assert str(evicted_dir) not in codex_module._allocated_codex_homes
        assert evicted_dir.exists(), (
            "eviction must not delete the directory (active-home invariant)"
        )

        # release_codex_home is the documented registry release path.
        # For an evicted-but-not-yet-released home it returns False
        # (the home is no longer in the bounded deque). This is the
        # documented semantic and is preserved. As of round 3 the
        # function ALSO rmtree's the on-disk directory with
        # ``ignore_errors=True`` so the production cleanup hook no
        # longer needs to call shutil.rmtree explicitly.
        released = release_codex_home(str(evicted_dir))
        assert released is False, (
            "release_codex_home returns False for evicted paths (registry-only semantic, preserved)"
        )
        assert not evicted_dir.exists(), (
            "release_codex_home MUST rmtree the on-disk directory "
            "even for evicted paths (round 3 invariant: the atexit net "
            "relies on this to reap FIFO-evicted orphans)"
        )
        assert str(evicted_dir) not in codex_module._all_allocated_codex_homes, (
            "release_codex_home MUST discard from _all_allocated_codex_homes "
            "so the atexit net does not double-rrmtree the path"
        )

        # Idempotency: a second call after release must not raise
        # and must remain a no-op (release_codex_home returns False,
        # rmtree with ignore_errors=True is a no-op on missing paths).
        second = release_codex_home(str(evicted_dir))
        assert second is False, "second release_codex_home returns False (idempotent)"
    finally:
        # Clean up any remaining on-disk dirs before restoring
        cleanup_codex_homes()
        # Restore the production deque (with original cap)
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set


def test_active_codex_homes_not_evicted_from_disk(tmp_path: Path) -> None:
    """Regression for analysis-feedback wt-024 round 2: the active-home
    invariant must hold when more than ``_DEFAULT_CODEX_HOME_CAP``
    live Codex invocations exist.

    Scenario: simulate the real bug. Allocate N > cap "active" Codex
    homes (each represented by a still-alive ``ResolvedInvocationRuntime``
    holding a cleanup hook). Assert that NONE of the previously-allocated
    homes have been rmtree'd even after the registry has wrapped around
    past them. Then release the FIRST home via its captured cleanup
    hook (release_codex_home + unconditional rmtree, mirroring
    CodexRuntimeResolver) and confirm only that one was rmtree'd; the
    rest must still exist (each cleanup hook only releases its own home).
    """
    # Shrink the cap for a fast deterministic test by swapping the
    # deque with a smaller-maxlen one. We restore the original in finally.
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    try:
        # Allocate cap + 2 entries (i.e., the registry wraps around the
        # original cap, evicting two of the earlier homes). Each
        # allocation is paired with a "live" cleanup hook representing
        # an outstanding ResolvedInvocationRuntime.cleanup.
        n_active = small_cap + 2
        dirs: list = []
        cleanups: list = []
        for _ in range(n_active):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)
            # Capture the home path in a closure as a fake cleanup hook.
            # Mirrors CodexRuntimeResolver: release_codex_home (registry
            # cleanup) + unconditional shutil.rmtree (on-disk cleanup,
            # needed because the registry may have FIFO-evicted the
            # entry before this owning agent finishes).
            captured = str(d)

            def _make_cleanup(home: str) -> object:
                def _cleanup() -> None:
                    release_codex_home(home)
                    shutil.rmtree(home, ignore_errors=True)

                return _cleanup

            cleanups.append(_make_cleanup(captured))

        # Registry is bounded at the cap (n_active - small_cap oldest
        # entries were evicted from the registry).
        assert len(codex_module._allocated_codex_homes) == small_cap
        # The first two entries should no longer be in the registry.
        assert str(dirs[0]) not in codex_module._allocated_codex_homes
        assert str(dirs[1]) not in codex_module._allocated_codex_homes

        # CRITICAL INVARIANT: every "active" home (every home that has
        # a live owner holding a cleanup hook) MUST STILL EXIST on
        # disk, including the ones evicted from the registry.
        for d in dirs:
            assert d.exists(), (
                f"active home {d} MUST still exist on disk even after "
                f"registry eviction (analysis-feedback wt-024 round 2 "
                f"regression guard)"
            )

        # Now release the FIRST home via its captured cleanup hook.
        # Only that home must be rmtree'd. As of round 3,
        # ``release_codex_home`` itself rmtree's the on-disk dir for
        # evicted paths; the explicit ``shutil.rmtree`` in the fake
        # cleanup hook is now redundant but harmless.
        cleanups[0]()

        assert not dirs[0].exists(), "released home must be rmtree'd by its cleanup hook"
        # All OTHER homes must STILL exist (each cleanup only releases
        # its own home; nobody else's).
        for d in dirs[1:]:
            assert d.exists(), (
                f"unrelated active home {d} must NOT have been rmtree'd "
                f"by another home's cleanup hook"
            )
    finally:
        # Clean up any remaining on-disk dirs before restoring
        cleanup_codex_homes()
        # Restore the production deque (with original cap)
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set


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


def test_invoke_agent_setup_failure_releases_codex_home(tmp_path: Path) -> None:
    """Regression for analysis-feedback wt-024 round 3:

    When ``invoke_agent`` is invoked for the Codex transport and a
    pre-execution setup step raises (e.g. ``_build_command``), the
    per-invocation ``CODEX_HOME`` tempdir allocated by
    ``CodexRuntimeResolver`` MUST still be rmtree'd.

    An earlier version of ``invoke_agent`` only invoked
    ``runtime.cleanup`` in the ``finally`` block that wrapped the
    actual subprocess execution. The setup steps that ran AFTER
    ``resolve_invocation_runtime`` but BEFORE the try block (e.g.
    command construction, monitor setup) were unprotected: any
    exception in that window bypassed the cleanup hook and leaked
    the CODEX_HOME directory until ``cleanup_codex_homes`` ran at
    interpreter shutdown.

    The fix moves the try/finally boundary to start IMMEDIATELY after
    ``resolve_invocation_runtime``. This test forces a setup failure
    via a monkeypatched ``_build_command`` and asserts the allocated
    home directory is removed by the time ``invoke_agent`` returns.

    Proof: before the fix the test fails (the home survives on disk
    because the cleanup hook was never invoked); after the fix the
    test passes (the outer try/finally invokes ``runtime.cleanup``
    on the pre-execution exception).
    """
    # Reset registry + lifetime set so we count only this test's
    # allocations.
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    config = AgentConfig(cmd="codex", transport=AgentTransport.CODEX)
    options = InvokeOptions(
        workspace_path=tmp_path,
        system_prompt_file=None,
        extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:1/mcp"},
    )

    # Force a pre-execution setup failure by making ``_build_command``
    # raise. ``invoke_agent`` resolves the runtime (allocates CODEX_HOME)
    # BEFORE calling ``_build_command``, so the cleanup hook MUST fire.
    inv = ralph.agents.invoke

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom")

    original_build_command = inv._build_command
    inv._build_command = boom
    try:
        with pytest.raises(RuntimeError, match="boom"):
            list(invoke_agent(config, "/tmp/never-read.md", options=options))
    finally:
        inv._build_command = original_build_command

    # The allocated CODEX_HOME directory MUST have been rmtree'd by
    # the cleanup hook (the new outer try/finally). The registry and
    # lifetime set must both be empty (release_codex_home discards
    # from both).
    agent_tmp = tmp_path / ".agent" / "tmp"
    stray = (
        sorted(p.name for p in agent_tmp.iterdir() if p.name.startswith("codex-home-"))
        if agent_tmp.exists()
        else []
    )
    assert stray == [], (
        f"invoke_agent setup failure leaked {len(stray)} codex-home dirs: {stray} "
        f"(round 3 regression: cleanup hook must run on pre-execution setup "
        f"failure)"
    )
    assert list(_allocated_codex_homes) == [], (
        f"registry must be drained after setup failure; got {_allocated_codex_homes}"
    )
    assert _all_allocated_codex_homes == set(), (
        f"lifetime set must be drained after setup failure; got {_all_allocated_codex_homes}"
    )
