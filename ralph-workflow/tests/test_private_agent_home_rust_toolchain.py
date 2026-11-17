from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import resolve_invocation_runtime
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from ralph.agents.invoke import ResolvedInvocationRuntime

_TOOLCHAIN_DIRECTORIES = {"RUSTUP_HOME": ".rustup", "CARGO_HOME": ".cargo"}


def _resolve_agy(
    workspace: Path, base_env: dict[str, str], extra_env: dict[str, str]
) -> ResolvedInvocationRuntime:
    return resolve_invocation_runtime(
        AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp", **extra_env},
        workspace,
        _base_env=base_env,
    )


def _resolve_cursor(
    workspace: Path, base_env: dict[str, str], extra_env: dict[str, str]
) -> ResolvedInvocationRuntime:
    return resolve_invocation_runtime(
        AgentConfig(cmd="cursor-agent", transport=AgentTransport.CURSOR),
        extra_env,
        workspace,
        _base_env=base_env,
    )


_RESOLVERS = pytest.mark.parametrize("resolve", [_resolve_agy, _resolve_cursor])


def _toolchain_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolve: object,
    *,
    create_toolchain: bool = True,
    base_overrides: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path, dict[str, str | None]]:
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    if create_toolchain:
        for directory in _TOOLCHAIN_DIRECTORIES.values():
            (operator_home / directory).mkdir()
    for variable in _TOOLCHAIN_DIRECTORIES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(operator_home))
    assert callable(resolve)
    runtime = resolve(
        tmp_path, {"HOME": str(operator_home), **(base_overrides or {})}, extra_env or {}
    )
    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["HOME"] != str(operator_home)
        return operator_home, {
            variable: runtime.agent_env.get(variable) for variable in _TOOLCHAIN_DIRECTORIES
        }
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@_RESOLVERS
def test_a_private_agent_home_shares_the_operators_rust_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolve: object
) -> None:
    operator_home, toolchain_env = _toolchain_env(tmp_path, monkeypatch, resolve)

    assert toolchain_env == {
        variable: str(operator_home / directory)
        for variable, directory in _TOOLCHAIN_DIRECTORIES.items()
    }


@_RESOLVERS
def test_a_private_agent_home_keeps_a_toolchain_home_the_base_env_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolve: object
) -> None:
    _operator_home, toolchain_env = _toolchain_env(
        tmp_path,
        monkeypatch,
        resolve,
        base_overrides={"RUSTUP_HOME": "/opt/rustup", "CARGO_HOME": "/opt/cargo"},
    )

    assert toolchain_env == {"RUSTUP_HOME": None, "CARGO_HOME": None}


@_RESOLVERS
def test_a_private_agent_home_keeps_a_toolchain_home_the_extra_env_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolve: object
) -> None:
    _operator_home, toolchain_env = _toolchain_env(
        tmp_path,
        monkeypatch,
        resolve,
        extra_env={"RUSTUP_HOME": "/opt/rustup", "CARGO_HOME": "/opt/cargo"},
    )

    assert toolchain_env == {"RUSTUP_HOME": "/opt/rustup", "CARGO_HOME": "/opt/cargo"}


@_RESOLVERS
def test_a_private_agent_home_sets_no_toolchain_home_the_operator_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolve: object
) -> None:
    _operator_home, toolchain_env = _toolchain_env(
        tmp_path, monkeypatch, resolve, create_toolchain=False
    )

    assert toolchain_env == {"RUSTUP_HOME": None, "CARGO_HOME": None}
