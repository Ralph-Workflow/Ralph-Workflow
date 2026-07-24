"""Per-transport RuntimeResolver classes for agent invocation runtime environment wiring.

This module defines the RuntimeResolver Protocol and the RUNTIME_RESOLVERS dispatch
dictionary that maps every AgentTransport value to its corresponding RuntimeResolver class.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.transport.codex import release_codex_home
from ralph.mcp.transport.cursor import cursor_workspace_mcp_endpoint
from ralph.mcp.transport.pi import PI_MCP_EXTENSION_ENV, write_pi_mcp_extension

if TYPE_CHECKING:
    from collections.abc import Mapping

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
        master_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        """Build the runtime configuration for agent invocation.

        Args:
            config: Agent configuration.
            extra_env: Additional environment variables.
            workspace_path: Workspace directory path.
            base_env: Base environment variables.
            master_prompt_file: Path to master prompt file.
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
        master_prompt_file: str | None = None,
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
        master_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:

        from ralph.agents.invoke import _apply_upstream_env  # noqa: PLC0415

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

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
        master_prompt_file: str | None = None,
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

        if not endpoint and master_prompt_file is None:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        codex_home, upstreams = prepare_codex_home_with_upstreams(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
            master_prompt_file=master_prompt_file,
            unsafe_mode=unsafe_mode,
        )
        runtime_env["CODEX_HOME"] = codex_home

        _apply_upstream_env(upstreams, workspace_path, runtime_env, server_env)

        # Per-invocation cleanup hook: ``prepare_codex_home_with_upstreams``
        # always allocates a fresh ``tempfile.mkdtemp`` under
        # ``workspace_path/.agent/tmp`` (or the system tempdir). Without
        # a release hook the on-disk directory would persist for the
        # entire interpreter lifetime, and the in-memory registry in
        # ``ralph.mcp.transport.codex._allocated_codex_homes`` could
        # never distinguish an active home from a finished one. The
        # ``invoke_agent`` finally block invokes this hook after the
        # Codex subprocess finishes (success, failure, or
        # cancellation) so each per-invocation home is rmtree'd at the
        # right time.
        #
        # The hook unconditionally rmtree's the on-disk directory
        # because the owning agent captured ``codex_home`` at
        # allocation time. The registry may have already FIFO-evicted
        # this entry (analysis-feedback wt-024 round 2 active-home
        # invariant) before the owning agent finished, in which case
        # ``release_codex_home`` would return False (no-op) but the
        # directory still needs cleanup. ``release_codex_home`` is
        # itself idempotent (returns False on a second call) so a
        # duplicate invocation is harmless.
        def _release() -> None:
            release_codex_home(codex_home)
            shutil.rmtree(codex_home, ignore_errors=True)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
            cleanup=_release,
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
        master_prompt_file: str | None = None,
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
        master_prompt_file: str | None = None,
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
        master_prompt_file: str | None = None,
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

    Pi has no native MCP config file or CLI flag, but Pi extensions can
    register tools. Ralph therefore materializes a per-invocation extension
    that registers the visible Ralph MCP tools and proxies each call to the
    active HTTP MCP endpoint.
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        master_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        endpoint = _get_endpoint(runtime_env, _env)
        runtime_env.pop(MCP_ENDPOINT_ENV, None)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        extension_path, cleanup = write_pi_mcp_extension(endpoint, workspace_path=workspace_path)
        runtime_env[PI_MCP_EXTENSION_ENV] = str(extension_path)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=None,
            mcp_endpoint=endpoint,
            cleanup=cleanup,
        )


class CursorRuntimeResolver:
    """RuntimeResolver for AgentTransport.CURSOR.

    Cursor reads its MCP server configuration from the documented
    ``.cursor/mcp.json`` (workspace-local) and ``~/.cursor/mcp.json``
    (user-global) JSON files.  This resolver writes a run-scoped Ralph
    entry to BOTH paths (Cursor may prefer one over the other
    depending on cwd) and restores the original bytes on exit so
    operator-managed MCP servers are preserved across Ralph runs.

    The MCP_ENDPOINT_ENV is consumed (and dropped) from the
    ``runtime_env`` so it does not leak into the spawned agent's
    environment as a literal variable (the endpoint itself is only
    written into the JSON config files, not exported).
    """

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        master_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        from ralph.agents.invoke import (  # noqa: PLC0415
            _apply_upstream_env,
        )

        _env = base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        from ralph.agents.invoke import (  # noqa: PLC0415
            load_existing_cursor_upstream_servers,
        )

        # Write the merged Ralph entry to BOTH the workspace-local
        # ``.cursor/mcp.json`` and the user-global ``~/.cursor/mcp.json``
        # so the agent picks up the MCP endpoint regardless of the cwd
        # it was launched from.  The runtime context manager snapshots
        # the original bytes INSIDE the critical section so a parallel
        # sibling cannot interleave its own write/restore between our
        # read and our restore.
        resolved_workspace = workspace_path or Path.cwd()
        write_ctx = cursor_workspace_mcp_endpoint(
            resolved_workspace, endpoint, unsafe_mode=unsafe_mode
        )
        write_ctx.__enter__()
        try:
            _apply_upstream_env(
                load_existing_cursor_upstream_servers(resolved_workspace),
                resolved_workspace,
                runtime_env,
                server_env,
            )
        finally:
            # Defer the restore until the invoke_agent ``finally`` block
            # so a long-running Cursor run keeps the merged config
            # available for the lifetime of the agent subprocess.  Wrap
            # the contextmanager in a closure that exits it.
            def _release() -> None:
                write_ctx.__exit__(None, None, None)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
            cleanup=_release,
        )


RUNTIME_RESOLVERS: dict[AgentTransport, type[RuntimeResolver]] = {
    AgentTransport.OPENCODE: OpencodeRuntimeResolver,
    AgentTransport.NANOCODER: NanocoderRuntimeResolver,
    AgentTransport.CODEX: CodexRuntimeResolver,
    AgentTransport.CLAUDE: ClaudeRuntimeResolver,
    AgentTransport.CLAUDE_INTERACTIVE: ClaudeRuntimeResolver,
    AgentTransport.AGY: AgyRuntimeResolver,
    AgentTransport.PI: PiRuntimeResolver,
    AgentTransport.CURSOR: CursorRuntimeResolver,
    AgentTransport.GENERIC: DefaultRuntimeResolver,
}

__all__ = [
    "RUNTIME_RESOLVERS",
    "AgyRuntimeResolver",
    "ClaudeRuntimeResolver",
    "CodexRuntimeResolver",
    "CursorRuntimeResolver",
    "DefaultRuntimeResolver",
    "NanocoderRuntimeResolver",
    "OpencodeRuntimeResolver",
    "PiRuntimeResolver",
    "RuntimeResolver",
]
