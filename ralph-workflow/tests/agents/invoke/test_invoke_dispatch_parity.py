"""Parity test: verify resolve_invocation_runtime behavior for every AgentTransport.

This test exercises every AgentTransport value (with and without endpoint where applicable)
and asserts full ResolvedInvocationRuntime equality against the expected runtime wiring
behavior. MCP helpers are monkeypatched with fakes to produce deterministic assertions.

The test locks the behavioral contract for each transport and must continue to pass
after any refactor to prove the dispatch-based implementation is behaviorally equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ralph.agents.invoke import resolve_invocation_runtime
from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path


class FakeUpstream:
    def __init__(self, name: str = "fake-upstream") -> None:
        self.name = name
        self.env = {f"{name.upper()}_VAR": "test-value"}


def _make_fake_apply_upstream_env() -> Callable[..., None]:
    def fake_apply_upstream_env(
        upstreams: list[Any],
        workspace_path: object,
        runtime_env: dict[str, str],
        server_env: dict[str, str],
    ) -> None:
        server_env.clear()
        for upstream in upstreams:
            if hasattr(upstream, "env"):
                server_env.update(upstream.env)
    return fake_apply_upstream_env


class TestResolveInvocationRuntimeParity:
    """Parametric parity tests for every AgentTransport value.

    Each test monkeypatches the MCP-wiring helpers to return controlled values,
    then asserts the returned ResolvedInvocationRuntime has the expected field contents.
    """

    @pytest.mark.parametrize("has_endpoint", [False, True])
    def test_opencode_resolver_parity(
        self,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test OPENCODE resolver with and without endpoint."""
        extra_env: dict[str, str] = {}
        endpoint = "http://localhost:8080" if has_endpoint else None
        if has_endpoint:
            extra_env[MCP_ENDPOINT_ENV] = endpoint

        config = AgentConfig(cmd="test-agent", transport=AgentTransport.OPENCODE)

        fake_provider_config = "fake-opencode-provider-config"
        fake_upstream = FakeUpstream("test-upstream")

        def fake_build_opencode_provider_config(
            opencode_config: str | None,
            ep: str,
            *,
            unsafe_mode: bool = False,
        ) -> tuple[str, list[FakeUpstream]]:
            return fake_provider_config, [fake_upstream]

        monkeypatch.setattr(
            "ralph.agents.invoke.build_opencode_provider_config",
            fake_build_opencode_provider_config,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._apply_upstream_env",
            _make_fake_apply_upstream_env(),
        )

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        if has_endpoint:
            assert isinstance(result.agent_env, dict)
            assert "OPENCODE_CONFIG_CONTENT" in result.agent_env
            assert result.agent_env["OPENCODE_CONFIG_CONTENT"] == fake_provider_config
            assert isinstance(result.server_env, dict)
            assert result.mcp_endpoint == endpoint
        else:
            assert result.agent_env is None
            assert result.server_env is None
            assert result.mcp_endpoint is None

    @pytest.mark.parametrize("has_endpoint", [False, True])
    def test_agy_resolver_parity(
        self,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test AGY resolver with and without endpoint."""
        extra_env: dict[str, str] = {}
        endpoint = "http://localhost:8080" if has_endpoint else None
        if has_endpoint:
            extra_env[MCP_ENDPOINT_ENV] = endpoint

        config = AgentConfig(cmd="test-agent", transport=AgentTransport.AGY)
        fake_upstream = FakeUpstream("test-upstream")

        def fake_load_existing_agy_upstream_servers(
            workspace_path: object,
        ) -> list[FakeUpstream]:
            return [fake_upstream]

        monkeypatch.setattr(
            "ralph.agents.invoke.load_existing_agy_upstream_servers",
            fake_load_existing_agy_upstream_servers,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._apply_upstream_env",
            _make_fake_apply_upstream_env(),
        )

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        if has_endpoint:
            assert isinstance(result.agent_env, dict)
            assert isinstance(result.server_env, dict)
            assert result.mcp_endpoint == endpoint
        else:
            assert result.agent_env is None
            assert result.server_env is None
            assert result.mcp_endpoint is None

    @pytest.mark.parametrize("has_endpoint", [True])
    def test_nanocoder_resolver_parity(
        self,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test NANOCODER resolver with endpoint (raises without)."""
        extra_env: dict[str, str] = {MCP_ENDPOINT_ENV: "http://localhost:8080"}
        config = AgentConfig(cmd="test-agent", transport=AgentTransport.NANOCODER)

        fake_mcp_config = "fake-nanocoder-mcp-config"
        fake_upstream = FakeUpstream("test-upstream")

        def fake_build_nanocoder_mcp_config(
            nanocoder_mcp_servers: str | None,
            ep: str,
            *,
            always_allow: tuple[str, ...] = (),
            unsafe_mode: bool = False,
            workspace_path: object = None,
            env: Mapping[str, str] | None = None,
        ) -> tuple[str, list[FakeUpstream]]:
            return fake_mcp_config, [fake_upstream]

        def fake_load_existing_nanocoder_upstream_servers(
            workspace_path: object,
            *,
            env: Mapping[str, str] | None = None,
        ) -> list[FakeUpstream]:
            return [fake_upstream]

        monkeypatch.setattr(
            "ralph.agents.invoke.build_nanocoder_mcp_config",
            fake_build_nanocoder_mcp_config,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke.load_existing_nanocoder_upstream_servers",
            fake_load_existing_nanocoder_upstream_servers,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._apply_upstream_env",
            _make_fake_apply_upstream_env(),
        )

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        assert isinstance(result.agent_env, dict)
        assert "NANOCODER_MCPSERVERS" in result.agent_env
        assert result.agent_env["NANOCODER_MCPSERVERS"] == fake_mcp_config
        assert isinstance(result.server_env, dict)
        assert result.mcp_endpoint == "http://localhost:8080"

    @pytest.mark.parametrize("has_endpoint", [False, True])
    def test_codex_resolver_parity(
        self,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test CODEX resolver with and without endpoint."""
        extra_env: dict[str, str] = {}
        if has_endpoint:
            extra_env[MCP_ENDPOINT_ENV] = "http://localhost:8080"

        config = AgentConfig(cmd="test-agent", transport=AgentTransport.CODEX)

        fake_codex_home = "/fake/codex/home"
        fake_upstream = FakeUpstream("test-upstream")

        def fake_prepare_codex_home_with_upstreams(
            ep: str | None,
            *,
            workspace_path: object = None,
            existing_home: str | None = None,
            system_prompt_file: str | None = None,
            unsafe_mode: bool = False,
        ) -> tuple[str, list[FakeUpstream]]:
            return fake_codex_home, [fake_upstream]

        monkeypatch.setattr(
            "ralph.agents.invoke.prepare_codex_home_with_upstreams",
            fake_prepare_codex_home_with_upstreams,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._apply_upstream_env",
            _make_fake_apply_upstream_env(),
        )

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        if has_endpoint:
            assert isinstance(result.agent_env, dict)
            assert "CODEX_HOME" in result.agent_env
            assert result.agent_env["CODEX_HOME"] == fake_codex_home
            assert isinstance(result.server_env, dict)
            assert result.mcp_endpoint == "http://localhost:8080"
        else:
            assert result.agent_env is None
            assert result.server_env is None
            assert result.mcp_endpoint is None

    @pytest.mark.parametrize(
        "transport", [AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE]
    )
    @pytest.mark.parametrize("has_endpoint", [False, True])
    def test_claude_resolver_parity(
        self,
        transport: AgentTransport,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test CLAUDE and CLAUDE_INTERACTIVE resolvers with and without endpoint."""
        extra_env: dict[str, str] = {}
        endpoint = "http://localhost:8080" if has_endpoint else None
        if has_endpoint:
            extra_env[MCP_ENDPOINT_ENV] = endpoint

        config = AgentConfig(cmd="test-agent", transport=transport)
        fake_upstream = FakeUpstream("test-upstream")

        def fake_load_existing_claude_upstream_servers(
            workspace_path: object,
        ) -> list[FakeUpstream]:
            return [fake_upstream]

        monkeypatch.setattr(
            "ralph.agents.invoke.load_existing_claude_upstream_servers",
            fake_load_existing_claude_upstream_servers,
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._apply_upstream_env",
            _make_fake_apply_upstream_env(),
        )

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        if has_endpoint:
            assert isinstance(result.agent_env, dict)
            assert isinstance(result.server_env, dict)
            assert result.mcp_endpoint == endpoint
        else:
            assert result.agent_env is None
            assert result.server_env is None
            assert result.mcp_endpoint is None

    @pytest.mark.parametrize("has_endpoint", [False])
    def test_generic_resolver_parity(
        self,
        has_endpoint: bool,
        tmp_path: Path,
    ) -> None:
        """Test GENERIC resolver without endpoint (raises with endpoint)."""
        config = AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC)

        result = resolve_invocation_runtime(
            config,
            extra_env={},
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        assert result.agent_env is None
        assert result.server_env is None
        assert result.mcp_endpoint is None

    def test_generic_with_endpoint_raises(self, tmp_path: Path) -> None:
        """GENERIC transport with MCP endpoint must raise UnsupportedMcpTransportError."""
        extra_env: dict[str, str] = {str(MCP_ENDPOINT_ENV): "http://localhost:8080"}
        config = AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC)

        with pytest.raises(UnsupportedMcpTransportError):
            resolve_invocation_runtime(
                config,
                extra_env=extra_env,
                workspace_path=tmp_path,
                _base_env={},
            )

    def test_nanocoder_requires_endpoint(self, tmp_path: Path) -> None:
        """NANOCODER transport requires endpoint - without it, raises RuntimeError."""
        config = AgentConfig(cmd="test-agent", transport=AgentTransport.NANOCODER)

        with pytest.raises(RuntimeError, match="endpoint must be set for NANOCODER"):
            resolve_invocation_runtime(
                config,
                extra_env={},
                workspace_path=tmp_path,
                _base_env={},
            )

    @pytest.mark.parametrize(
        "transport",
        [
            AgentTransport.OPENCODE,
            AgentTransport.CODEX,
            AgentTransport.CLAUDE,
            AgentTransport.CLAUDE_INTERACTIVE,
            AgentTransport.GENERIC,
            AgentTransport.AGY,
        ],
    )
    def test_no_endpoint_no_extra_env(
        self,
        transport: AgentTransport,
        tmp_path: Path,
    ) -> None:
        """Assert behavior when extra_env is None (no endpoint)."""
        config = AgentConfig(cmd="test-agent", transport=transport)

        result = resolve_invocation_runtime(
            config,
            extra_env=None,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        assert result.agent_env is None
        assert result.server_env is None
        assert result.mcp_endpoint is None

    @pytest.mark.parametrize(
        "transport",
        [
            AgentTransport.OPENCODE,
            AgentTransport.NANOCODER,
            AgentTransport.CODEX,
            AgentTransport.CLAUDE,
            AgentTransport.CLAUDE_INTERACTIVE,
            AgentTransport.GENERIC,
            AgentTransport.AGY,
        ],
    )
    def test_no_endpoint_with_base_env(
        self,
        transport: AgentTransport,
        tmp_path: Path,
    ) -> None:
        """Assert behavior when _base_env is provided without endpoint."""
        base_env: Mapping[str, str] = {"PATH": "/usr/bin", "HOME": "/home/test"}
        config = AgentConfig(cmd="test-agent", transport=transport)

        if transport == AgentTransport.NANOCODER:
            with pytest.raises(RuntimeError, match="endpoint must be set for NANOCODER"):
                resolve_invocation_runtime(
                    config,
                    extra_env={},
                    workspace_path=tmp_path,
                    _base_env=base_env,
                )
            return

        result = resolve_invocation_runtime(
            config,
            extra_env={},
            workspace_path=tmp_path,
            _base_env=base_env,
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        assert result.mcp_endpoint is None
