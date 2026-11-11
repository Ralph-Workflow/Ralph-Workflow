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
from ralph.mcp.upstream import agent_probe
from ralph.mcp.upstream.agent_probe import _probe_codex
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from pathlib import Path


def test_release_codex_home_removes_from_registry_and_disk(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)
    assert d.exists()
    assert str(d) in _allocated_codex_homes

    released = release_codex_home(str(d))

    assert released is True, "release_codex_home must return True for registered homes"
    assert not d.exists(), "on-disk dir MUST be rmtree'd"
    assert str(d) not in _allocated_codex_homes, "registry MUST be drained"


def test_release_codex_home_returns_false_for_unknown_path(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()

    fake = str(tmp_path / "never-allocated-home")
    assert fake not in _allocated_codex_homes
    assert not (tmp_path / "never-allocated-home").exists()

    released = release_codex_home(fake)

    assert released is False, "release_codex_home(unknown) MUST return False"
    assert list(_allocated_codex_homes) == [], "registry MUST remain empty"


def test_release_codex_home_is_idempotent_per_home(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()

    d = _allocate_codex_home_dir(workspace_path=tmp_path)

    assert release_codex_home(str(d)) is True
    assert release_codex_home(str(d)) is False


def test_allocate_past_cap_evicts_oldest_from_registry_keeps_disk(
    tmp_path: Path,
) -> None:
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    try:
        dirs: list = []
        for _ in range(small_cap + 1):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)

        assert len(codex_module._allocated_codex_homes) == small_cap, (
            f"registry must be capped at {small_cap}; "
            f"got {len(codex_module._allocated_codex_homes)}"
        )

        assert str(dirs[0]) not in codex_module._allocated_codex_homes, (
            f"oldest allocation {dirs[0]} must have been evicted from the registry"
        )
        assert dirs[0].exists(), (
            f"oldest allocation {dirs[0]} must STILL exist on disk "
            f"(eviction must be registry-only, not on-disk)"
        )
        for d in dirs[1:]:
            assert str(d) in codex_module._allocated_codex_homes
            assert d.exists(), f"recent allocation {d} must still exist on disk"
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque


def test_release_codex_home_rmtrees_after_eviction_from_registry(
    tmp_path: Path,
) -> None:
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    try:
        dirs: list = []
        for _ in range(small_cap + 1):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)

        evicted_dir = dirs[0]
        assert str(evicted_dir) not in codex_module._allocated_codex_homes
        assert evicted_dir.exists(), (
            "eviction must not delete the directory (active-home invariant)"
        )

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

        second = release_codex_home(str(evicted_dir))
        assert second is False, "second release_codex_home returns False (idempotent)"
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set


def test_active_codex_homes_not_evicted_from_disk(tmp_path: Path) -> None:
    small_cap = 4
    original_deque = codex_module._allocated_codex_homes
    original_set = codex_module._all_allocated_codex_homes
    codex_module._allocated_codex_homes = collections.deque(maxlen=small_cap)
    codex_module._all_allocated_codex_homes = set()
    try:
        n_active = small_cap + 2
        dirs: list = []
        cleanups: list = []
        for _ in range(n_active):
            d = _allocate_codex_home_dir(workspace_path=tmp_path)
            dirs.append(d)
            captured = str(d)

            def _make_cleanup(home: str) -> object:
                def _cleanup() -> None:
                    release_codex_home(home)
                    shutil.rmtree(home, ignore_errors=True)

                return _cleanup

            cleanups.append(_make_cleanup(captured))

        assert len(codex_module._allocated_codex_homes) == small_cap
        assert str(dirs[0]) not in codex_module._allocated_codex_homes
        assert str(dirs[1]) not in codex_module._allocated_codex_homes

        for d in dirs:
            assert d.exists(), (
                f"active home {d} MUST still exist on disk even after "
                f"registry eviction (analysis-feedback wt-024 round 2 "
                f"regression guard)"
            )

        cleanups[0]()

        assert not dirs[0].exists(), "released home must be rmtree'd by its cleanup hook"
        for d in dirs[1:]:
            assert d.exists(), (
                f"unrelated active home {d} must NOT have been rmtree'd "
                f"by another home's cleanup hook"
            )
    finally:
        cleanup_codex_homes()
        codex_module._allocated_codex_homes = original_deque
        codex_module._all_allocated_codex_homes = original_set


def test_probe_codex_releases_home_in_normal_flow(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()

    server = UpstreamMcpServer(
        name="test-release-path",
        transport="http",
        url="http://127.0.0.1:1",
        command=None,
        args=(),
    )

    with pytest.raises(PreflightError):
        _probe_codex(server, workspace_path=tmp_path)

    assert list(_allocated_codex_homes) == [], (
        f"registry must be empty after probe raises; got {_allocated_codex_homes}"
    )
    remaining = [p for p in tmp_path.iterdir() if p.name.startswith("codex-home-")]
    assert not remaining, f"stray codex home dirs on disk: {remaining}"


def test_probe_codex_registry_does_not_grow_across_repeated_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allocated_codex_homes.clear()

    server = UpstreamMcpServer(
        name="test-no-growth",
        transport="http",
        url="http://127.0.0.1:1",
        command=None,
        args=(),
    )

    def failing_handshake(_server: UpstreamMcpServer) -> None:
        raise PreflightError("simulated handshake failure")

    monkeypatch.setattr(agent_probe, "server_handshake", failing_handshake)

    n_iterations = 10
    for _ in range(n_iterations):
        with pytest.raises(PreflightError):
            _probe_codex(server, workspace_path=tmp_path)
        assert list(_allocated_codex_homes) == [], "registry MUST be drained after each probe call"

    assert len(_allocated_codex_homes) == 0


def test_invoke_agent_setup_failure_releases_codex_home(tmp_path: Path) -> None:
    _allocated_codex_homes.clear()
    _all_allocated_codex_homes.clear()

    config = AgentConfig(cmd="codex", transport=AgentTransport.CODEX)
    options = InvokeOptions(
        workspace_path=tmp_path,
        master_prompt_file=None,
        extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:1/mcp"},
    )

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
