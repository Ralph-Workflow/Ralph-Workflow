"""Per-transport RuntimeResolver classes for agent invocation runtime environment wiring.

This module defines the RuntimeResolver Protocol and the RUNTIME_RESOLVERS dispatch
dictionary that maps every AgentTransport value to its corresponding RuntimeResolver class.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ralph.config.models import AgentConfig


@runtime_checkable
class RuntimeResolver(Protocol):
    """Protocol for per-transport runtime environment wiring.

    Each transport-specific RuntimeResolver implementation provides a resolve()
    method that builds the runtime environment dictionary and MCP configuration
    for the agent subprocess.
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        """Build the runtime configuration for agent invocation.

        Args:
            config: Agent configuration.
            extra_env: Additional environment variables.
            workspace_path: Workspace directory path.
            base_env: Base environment variables.
            system_prompt_file: Path to system prompt file.
            unsafe_mode: Whether to allow unsafe mode.

        Returns:
            ResolvedInvocationRuntime with agent_env, server_env, and mcp_endpoint.
        """
        ...


def _get_endpoint(runtime_env: dict[str, str], base_env: Mapping[str, str]) -> str | None:
    """Get MCP endpoint from runtime_env or base_env."""
    return runtime_env.get(MCP_ENDPOINT_ENV) or base_env.get(MCP_ENDPOINT_ENV)


class OpencodeRuntimeResolver:
    """RuntimeResolver for AgentTransport.OPENCODE."""

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import (  # noqa: PLC0415
            _apply_upstream_env,
            build_opencode_provider_config,
        )

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        if endpoint is None:
            raise RuntimeError("endpoint must be set for OPENCODE transport")

        opencode_config = runtime_env.get("OPENCODE_CONFIG_CONTENT") or _env.get(
            "OPENCODE_CONFIG_CONTENT"
        )
        provider_config, upstreams = build_opencode_provider_config(
            opencode_config,
            endpoint,
            unsafe_mode=unsafe_mode,
        )
        runtime_env["OPENCODE_CONFIG_CONTENT"] = provider_config

        _apply_upstream_env(upstreams, workspace_path, runtime_env, server_env)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


class NanocoderRuntimeResolver:
    """RuntimeResolver for AgentTransport.NANOCODER."""

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import _apply_upstream_env  # noqa: PLC0415

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            raise RuntimeError("endpoint must be set for NANOCODER transport")

        runtime_env.setdefault("NANOCODER_TRUST_DIRECTORY", "1")
        nanocoder_mcp_servers = runtime_env.get("NANOCODER_MCPSERVERS") or _env.get(
            "NANOCODER_MCPSERVERS"
        )

        from ralph.agents.invoke import (  # noqa: PLC0415
            _canonical_http_mcp_tool_names,
            build_nanocoder_mcp_config,
            load_existing_nanocoder_upstream_servers,
        )

        mcp_config, env_upstreams = build_nanocoder_mcp_config(
            nanocoder_mcp_servers,
            endpoint,
            always_allow=_canonical_http_mcp_tool_names(endpoint),
            unsafe_mode=unsafe_mode,
            workspace_path=workspace_path,
            env=runtime_env or dict(_env),
        )
        runtime_env["NANOCODER_MCPSERVERS"] = mcp_config

        _apply_upstream_env(
            load_existing_nanocoder_upstream_servers(
                workspace_path,
                env=runtime_env or dict(_env),
            )
            + env_upstreams,
            workspace_path,
            runtime_env,
            server_env,
        )

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


class CodexRuntimeResolver:
    """RuntimeResolver for AgentTransport.CODEX."""

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import (  # noqa: PLC0415
            _apply_upstream_env,
            prepare_codex_home_with_upstreams,
        )

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint and system_prompt_file is None:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        codex_home, upstreams = prepare_codex_home_with_upstreams(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
            system_prompt_file=system_prompt_file,
            unsafe_mode=unsafe_mode,
        )
        runtime_env["CODEX_HOME"] = codex_home

        _apply_upstream_env(upstreams, workspace_path, runtime_env, server_env)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


class ClaudeRuntimeResolver:
    """RuntimeResolver for AgentTransport.CLAUDE and AgentTransport.CLAUDE_INTERACTIVE.

    Both CLAUDE and CLAUDE_INTERACTIVE use the same resolver class.
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import (  # noqa: PLC0415
            _apply_upstream_env,
            load_existing_claude_upstream_servers,
        )

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        _apply_upstream_env(
            load_existing_claude_upstream_servers(workspace_path),
            workspace_path,
            runtime_env,
            server_env,
        )

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


class AgyRuntimeResolver:
    """RuntimeResolver for AgentTransport.AGY."""

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import (  # noqa: PLC0415
            _apply_upstream_env,
            load_existing_agy_upstream_servers,
        )

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        _apply_upstream_env(
            load_existing_agy_upstream_servers(workspace_path),
            workspace_path,
            runtime_env,
            server_env,
        )

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


class DefaultRuntimeResolver:
    """Default RuntimeResolver for AgentTransport.GENERIC.

    This resolver handles the GENERIC transport. It raises UnsupportedMcpTransportError
    if an MCP endpoint is provided, and otherwise returns a minimal runtime with no
    server_env and no mcp_endpoint.
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        endpoint = _get_endpoint(runtime_env, _env)

        if endpoint is not None:
            msg = "Agent transport 'generic' does not declare how to receive Ralph MCP wiring"
            raise UnsupportedMcpTransportError(msg)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=None,
            mcp_endpoint=None,
        )


class PiRuntimeResolver:
    """RuntimeResolver for AgentTransport.PI.

    Pi has no documented CLI MCP wiring path
    (https://pi.dev/docs/latest/usage: "It intentionally does not include
    built-in MCP, sub-agents, permission popups, plan mode, to-dos, or
    background bash").  The resolver therefore does not forward Ralph's
    MCP endpoint into the Pi process.  Pi still runs through its documented
    NDJSON CLI path, and workflow artifacts complete through the prompt-side
    file fallback.
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        runtime_env = dict(extra_env or {})
        runtime_env.pop(MCP_ENDPOINT_ENV, None)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=None,
            mcp_endpoint=None,
        )


RUNTIME_RESOLVERS: dict[AgentTransport, type[RuntimeResolver]] = {
    AgentTransport.OPENCODE: OpencodeRuntimeResolver,
    AgentTransport.NANOCODER: NanocoderRuntimeResolver,
    AgentTransport.CODEX: CodexRuntimeResolver,
    AgentTransport.CLAUDE: ClaudeRuntimeResolver,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeRuntimeResolver,
    AgentTransport.AGY: AgyRuntimeResolver,
    AgentTransport.PI: PiRuntimeResolver,
    AgentTransport.GENERIC: DefaultRuntimeResolver,
}

__all__ = [
    "RUNTIME_RESOLVERS",
    "AgyRuntimeResolver",
    "ClaudeRuntimeResolver",
    "CodexRuntimeResolver",
    "DefaultRuntimeResolver",
    "NanocoderRuntimeResolver",
    "OpencodeRuntimeResolver",
    "PiRuntimeResolver",
    "RuntimeResolver",
]
