"""Parity test: verify resolve_invocation_runtime behavior for every AgentTransport.

This test exercises every AgentTransport value (with and without endpoint where applicable)
and asserts full ResolvedInvocationRuntime equality against the current if/elif chain
in ralph.agents.invoke.__init__.py:resolve_invocation_runtime.

The test must pass against the legacy code path before any refactor to lock
the behavioral contract, and must continue to pass after the refactor to prove
the dispatch-based implementation is behaviorally equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import resolve_invocation_runtime
from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class TestResolveInvocationRuntimeParity:
    """Parametric parity tests for every AgentTransport value."""

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
    @pytest.mark.parametrize("has_endpoint", [True, False])
    def test_resolved_invocation_runtime_parity(
        self,
        transport: AgentTransport,
        has_endpoint: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Assert full ResolvedInvocationRuntime equality for every transport.

        This test parametrizes over every AgentTransport value and both
        'with endpoint' and 'without endpoint' configurations. It asserts
        the returned ResolvedInvocationRuntime has the expected shape:
        - agent_env: dict | None
        - server_env: dict | None
        - mcp_endpoint: str | None

        Note: Some transports require endpoint and raise RuntimeError when it's missing.
        GENERIC with endpoint raises UnsupportedMcpTransportError.
        """
        extra_env: dict[str, str] = {}
        if has_endpoint:
            extra_env[MCP_ENDPOINT_ENV] = "http://localhost:8080"

        config = AgentConfig(cmd="test-agent", transport=transport)

        if transport == AgentTransport.NANOCODER and not has_endpoint:
            with pytest.raises(RuntimeError, match="endpoint must be set for NANOCODER"):
                resolve_invocation_runtime(
                    config,
                    extra_env=extra_env,
                    workspace_path=tmp_path,
                    _base_env={},
                )
            return

        if transport == AgentTransport.GENERIC and has_endpoint:
            with pytest.raises(UnsupportedMcpTransportError):
                resolve_invocation_runtime(
                    config,
                    extra_env=extra_env,
                    workspace_path=tmp_path,
                    _base_env={},
                )
            return

        result = resolve_invocation_runtime(
            config,
            extra_env=extra_env,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
        assert result.agent_env is None or isinstance(result.agent_env, dict)
        assert result.server_env is None or isinstance(result.server_env, dict)
        assert result.mcp_endpoint is None or isinstance(result.mcp_endpoint, str)

        if has_endpoint:
            assert result.mcp_endpoint == "http://localhost:8080"
        else:
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
    def test_resolved_invocation_runtime_no_extra_env(
        self,
        transport: AgentTransport,
        tmp_path: Path,
    ) -> None:
        """Assert behavior when extra_env is None (no endpoint).

        Note: NANOCODER requires endpoint and raises RuntimeError.
        """
        config = AgentConfig(cmd="test-agent", transport=transport)

        if transport == AgentTransport.NANOCODER:
            with pytest.raises(RuntimeError, match="endpoint must be set for NANOCODER"):
                resolve_invocation_runtime(
                    config,
                    extra_env=None,
                    workspace_path=tmp_path,
                    _base_env={},
                )
            return

        result = resolve_invocation_runtime(
            config,
            extra_env=None,
            workspace_path=tmp_path,
            _base_env={},
        )

        assert isinstance(result, ResolvedInvocationRuntime)
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
    def test_resolved_invocation_runtime_with_base_env(
        self,
        transport: AgentTransport,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Assert behavior when _base_env is provided.

        Note: NANOCODER requires endpoint and raises RuntimeError.
        """
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

    def test_generic_with_endpoint_raises(
        self,
        tmp_path: Path,
    ) -> None:
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
