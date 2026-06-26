"""wt-024 memory-perf round-2 regression: active Codex homes survive registry
FIFO eviction when the owning ResolvedInvocationRuntime has not been released.

Analysis feedback (wt-024 round 2) found that
``ralph.mcp.transport.codex._allocate_codex_home_dir`` previously
rmtree'd the oldest registry entry on every allocation past the cap.
Combined with the production flow (Codex subprocess can outlive the
calling frame that allocated its home), this meant a long-lived
process with more than ``_DEFAULT_CODEX_HOME_CAP`` concurrent Codex
sessions could delete the CODEX_HOME directory out from under a
running agent.

The fix:
  1. ``ResolvedInvocationRuntime`` carries a ``cleanup`` callable
     that the invoker invokes in its finally block (after the Codex
     subprocess finishes).
  2. ``CodexRuntimeResolver`` populates ``cleanup`` with a closure
     that calls ``release_codex_home`` + an unconditional
     ``shutil.rmtree(..., ignore_errors=True)``.
  3. ``_allocate_codex_home_dir`` no longer rmtree's on registry
     FIFO eviction \u2014 it only removes the bookkeeping entry. The
     on-disk directory survives until the owning agent's cleanup
     hook (or the atexit net) releases it.

This integration test exercises the REAL ``CodexRuntimeResolver``
(not the ``_allocate_codex_home_dir`` seam) across multiple live
sessions to prove the active-home invariant holds end-to-end:

  a. Allocate N > cap live Codex runtimes (each holds a cleanup hook
     representing an in-flight subprocess).
  b. Assert every previously-allocated CODEX_HOME path still exists
     (none were deleted by FIFO eviction).
  c. Release finished sessions one at a time via their cleanup
     hooks; assert cleanup happens for the released sessions AND
     that unrelated active sessions remain intact.
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import resolve_invocation_runtime
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS, CodexRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.transport import codex as codex_module
from ralph.mcp.transport.codex import cleanup_codex_homes

if TYPE_CHECKING:
    import pytest


def _codex_config() -> AgentConfig:
    return AgentConfig(cmd="codex", transport=AgentTransport.CODEX)


def _make_codex_resolver() -> type[CodexRuntimeResolver]:
    """Return the registered CodexRuntimeResolver class.

    Centralized so a future rename / move of the resolver is a
    one-line update; tests assert against the resolver dispatch
    table rather than hard-coding an import path.
    """
    return RUNTIME_RESOLVERS[AgentTransport.CODEX]


def test_live_codex_runtimes_active_homes_survive_registry_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allocate cap + 2 live Codex runtimes, then assert every previously
    allocated CODEX_HOME directory still exists on disk.

    Mirrors the real bug: a long-lived process with more than the
    registry cap of concurrent Codex invocations must not have its
    oldest active CODEX_HOME directory rmtree'd out from under it.
    Each ResolvedInvocationRuntime's cleanup hook captures the home
    path at allocation time and is the only thing that releases the
    directory; FIFO eviction must not race that release.
    """
    # Shrink the deque cap so the test is fast + deterministic. The
    # production cap is 64; we use a smaller one to force eviction
    # without allocating 64 directories.
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    try:
        # Use a per-test HOME so prepare_codex_home_with_upstreams's
        # _mirror_codex_home sees an empty source dir (it would
        # otherwise try to copy a real ~/.codex).
        empty_home = tmp_path / "fake-home"
        empty_home.mkdir()
        monkeypatch.setenv("HOME", str(empty_home))

        resolver = _make_codex_resolver()
        config = _codex_config()
        extra_env = {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:1/mcp"}

        n_active = small_cap + 2  # Crosses the cap; forces eviction.
        runtimes: list = []
        for _ in range(n_active):
            runtime = resolver().resolve(
                config,
                extra_env=extra_env,
                workspace_path=tmp_path,
                base_env={},
            )
            runtimes.append(runtime)

        # Every runtime must have populated a cleanup hook (per
        # CodexRuntimeResolver contract).
        for i, rt in enumerate(runtimes):
            assert rt.cleanup is not None, (
                f"runtime #{i} MUST carry a cleanup hook "
                f"(analysis-feedback wt-024 round 2 invariant)"
            )
            assert isinstance(rt.agent_env, dict)
            codex_home = rt.agent_env["CODEX_HOME"]

        # Registry is bounded at the cap (n_active - small_cap oldest
        # entries were FIFO-evicted from the registry bookkeeping).
        assert len(codex_module._allocated_codex_homes) == small_cap
        # The first two runtimes' CODEX_HOME paths must have been
        # evicted from the registry (they are the oldest).
        assert str(runtimes[0].agent_env["CODEX_HOME"]) not in (
            codex_module._allocated_codex_homes
        )
        assert str(runtimes[1].agent_env["CODEX_HOME"]) not in (
            codex_module._allocated_codex_homes
        )

        # CRITICAL INVARIANT: every previously-allocated CODEX_HOME
        # directory MUST still exist on disk, including the two that
        # were evicted from the registry. Their owning runtimes are
        # still "live" (the test still holds a reference to each
        # cleanup hook), so the directories MUST NOT be rmtree'd.
        for i, rt in enumerate(runtimes):
            codex_home = Path(rt.agent_env["CODEX_HOME"])
            assert codex_home.exists(), (
                f"runtime #{i} CODEX_HOME {codex_home} MUST still exist "
                f"on disk even after registry eviction "
                f"(analysis-feedback wt-024 round 2 regression guard)"
            )
    finally:
        # Restore the production deque; cleanup_codex_homes removes
        # everything still in the registry.
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque


def test_live_codex_runtimes_cleanup_hook_releases_each_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each ResolvedInvocationRuntime.cleanup hook releases ONLY its own
    home; other live sessions are unaffected.

    The fix's correctness hinges on the cleanup hook being a closure
    over the allocated ``codex_home`` path (NOT a shared operation
    that touches the whole registry). This test releases finished
    sessions one at a time and asserts the directory cleanup is
    scoped per-call.
    """
    # Shrink the deque cap to keep the test fast.
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    try:
        empty_home = tmp_path / "fake-home"
        empty_home.mkdir()
        monkeypatch.setenv("HOME", str(empty_home))

        resolver = _make_codex_resolver()
        config = _codex_config()
        extra_env = {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:1/mcp"}

        # Allocate cap + 2 live runtimes so some are evicted from
        # the registry.
        n_active = small_cap + 2
        runtimes: list = []
        for _ in range(n_active):
            runtime = resolver().resolve(
                config,
                extra_env=extra_env,
                workspace_path=tmp_path,
                base_env={},
            )
            runtimes.append(runtime)

        # Sanity: every cleanup hook is independent.
        for rt in runtimes:
            assert rt.cleanup is not None

        # Release the FIRST runtime's cleanup hook. ONLY its home
        # should be rmtree'd; the rest must still exist.
        first_cleanup = runtimes[0].cleanup
        assert first_cleanup is not None
        first_home = Path(runtimes[0].agent_env["CODEX_HOME"])
        first_cleanup()

        assert not first_home.exists(), (
            "first runtime's CODEX_HOME must be rmtree'd by its "
            "cleanup hook"
        )

        # Every OTHER runtime's CODEX_HOME must STILL exist (their
        # cleanup hooks were not invoked).
        for i, rt in enumerate(runtimes[1:], start=1):
            other_home = Path(rt.agent_env["CODEX_HOME"])
            assert other_home.exists(), (
                f"runtime #{i} CODEX_HOME {other_home} must STILL "
                f"exist (its cleanup hook was not invoked)"
            )

        # The cleanup hook must be idempotent (safe to call twice).
        # After the first release the directory is already gone; the
        # second call must not raise (release_codex_home returns
        # False, shutil.rmtree with ignore_errors=True is a no-op).
        first_cleanup()  # second call; MUST NOT RAISE
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque


def test_resolve_invocation_runtime_passes_through_codex_cleanup_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_invocation_runtime`` must preserve the cleanup hook
    populated by CodexRuntimeResolver on the returned runtime.

    The hook must be propagated all the way up to the caller of
    ``resolve_invocation_runtime`` (the ``invoke_agent`` finally
    block) \u2014 not silently dropped at any layer.
    """
    empty_home = tmp_path / "fake-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))

    config = _codex_config()
    extra_env = {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:1/mcp"}

    runtime = resolve_invocation_runtime(
        config,
        extra_env=extra_env,
        workspace_path=tmp_path,
        _base_env={},
    )

    assert runtime.cleanup is not None, (
        "resolve_invocation_runtime MUST preserve the CodexRuntimeResolver "
        "cleanup hook so invoke_agent's finally block can release the home"
    )

    # Invoking the hook must release the home end-to-end (rmtree).
    codex_home = Path(runtime.agent_env["CODEX_HOME"])
    assert codex_home.exists()
    runtime.cleanup()
    assert not codex_home.exists()

    cleanup_codex_homes()
