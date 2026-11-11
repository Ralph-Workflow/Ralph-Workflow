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
    return RUNTIME_RESOLVERS[AgentTransport.CODEX]


def test_live_codex_runtimes_active_homes_survive_registry_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

        for i, rt in enumerate(runtimes):
            assert rt.cleanup is not None, (
                f"runtime #{i} MUST carry a cleanup hook "
                f"(analysis-feedback wt-024 round 2 invariant)"
            )
            assert isinstance(rt.agent_env, dict)
            codex_home = rt.agent_env["CODEX_HOME"]

        assert len(codex_module._allocated_codex_homes) == small_cap
        assert str(runtimes[0].agent_env["CODEX_HOME"]) not in (codex_module._allocated_codex_homes)
        assert str(runtimes[1].agent_env["CODEX_HOME"]) not in (codex_module._allocated_codex_homes)

        for i, rt in enumerate(runtimes):
            codex_home = Path(rt.agent_env["CODEX_HOME"])
            assert codex_home.exists(), (
                f"runtime #{i} CODEX_HOME {codex_home} MUST still exist "
                f"on disk even after registry eviction "
                f"(analysis-feedback wt-024 round 2 regression guard)"
            )
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque


def test_live_codex_runtimes_cleanup_hook_releases_each_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

        for rt in runtimes:
            assert rt.cleanup is not None

        first_cleanup = runtimes[0].cleanup
        assert first_cleanup is not None
        first_home = Path(runtimes[0].agent_env["CODEX_HOME"])
        first_cleanup()

        assert not first_home.exists(), (
            "first runtime's CODEX_HOME must be rmtree'd by its cleanup hook"
        )

        for i, rt in enumerate(runtimes[1:], start=1):
            other_home = Path(rt.agent_env["CODEX_HOME"])
            assert other_home.exists(), (
                f"runtime #{i} CODEX_HOME {other_home} must STILL "
                f"exist (its cleanup hook was not invoked)"
            )

        first_cleanup()
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque


def test_resolve_invocation_runtime_passes_through_codex_cleanup_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    codex_home = Path(runtime.agent_env["CODEX_HOME"])
    assert codex_home.exists()
    runtime.cleanup()
    assert not codex_home.exists()

    cleanup_codex_homes()
