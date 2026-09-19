"""Per-transport RuntimeResolver classes for agent invocation runtime environment wiring.

This module defines the RuntimeResolver Protocol and the RUNTIME_RESOLVERS dispatch
dictionary that maps every AgentTransport value to its corresponding RuntimeResolver class.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.startup import (
    PreflightError,
    ensure_no_preflight_error,
    extract_preflight_tool_names,
    initialize_request,
    initialized_notification,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from ralph.mcp.tool_contract import canonicalize_tool_names
from ralph.mcp.transport.codex import release_codex_home
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.transport.cursor import _mirror_cursor_home
from ralph.mcp.transport.pi import PI_MCP_EXTENSION_ENV, write_pi_mcp_extension
from ralph.mcp.transport.private_config_root import prepare_private_config_root

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.config.models import AgentConfig
    from ralph.mcp.upstream.config import UpstreamMcpServer


class _InvokeCompatibilitySeam(Protocol):
    """Typed package-level compatibility seam retained for resolver tests."""

    def _apply_upstream_env(
        self,
        upstreams: tuple[UpstreamMcpServer, ...],
        workspace_path: Path | None,
        runtime_env: dict[str, str],
        server_env: dict[str, str],
    ) -> None: ...

    def discover_http_mcp_tool_names(self, endpoint: str) -> list[str]: ...

    def build_opencode_provider_config(
        self,
        existing: str | None,
        endpoint: str,
        *,
        unsafe_mode: bool = False,
        workspace_path: Path | None = None,
    ) -> tuple[str, tuple[UpstreamMcpServer, ...]]: ...

    def build_nanocoder_mcp_config(
        self,
        existing: str | None,
        endpoint: str,
        *,
        always_allow: tuple[str, ...] = (),
        unsafe_mode: bool = False,
        workspace_path: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[str, tuple[UpstreamMcpServer, ...]]: ...

    def load_existing_nanocoder_upstream_servers(
        self, workspace_path: Path | None, *, env: dict[str, str] | None = None
    ) -> tuple[UpstreamMcpServer, ...]: ...

    def prepare_codex_home_with_upstreams(
        self,
        endpoint: str | None,
        *,
        workspace_path: Path | None,
        existing_home: str | None,
        master_prompt_file: str | None,
        unsafe_mode: bool = False,
        run_id: str | None = None,
    ) -> tuple[str, tuple[UpstreamMcpServer, ...]]: ...

    def load_existing_claude_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[UpstreamMcpServer, ...]: ...

    def load_existing_agy_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[UpstreamMcpServer, ...]: ...

    def load_existing_cursor_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[UpstreamMcpServer, ...]: ...

    def load_existing_kimi_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[UpstreamMcpServer, ...]: ...


@runtime_checkable
class _InvokeModule(Protocol):
    """Structural compatibility seam retained for runtime resolver tests."""

    def _apply_upstream_env(
        self,
        upstreams: tuple[object, ...],
        workspace_path: Path | None,
        runtime_env: dict[str, str],
        server_env: dict[str, str],
    ) -> None: ...

    def discover_http_mcp_tool_names(self, endpoint: str) -> list[str]: ...

    def build_opencode_provider_config(
        self,
        existing: str | None,
        endpoint: str,
        *,
        unsafe_mode: bool = False,
        workspace_path: Path | None = None,
    ) -> tuple[str, tuple[object, ...]]: ...

    def build_nanocoder_mcp_config(
        self,
        existing: str | None,
        endpoint: str,
        *,
        always_allow: tuple[str, ...] = (),
        unsafe_mode: bool = False,
        workspace_path: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[str, tuple[object, ...]]: ...

    def load_existing_nanocoder_upstream_servers(
        self, workspace_path: Path | None, *, env: dict[str, str] | None = None
    ) -> tuple[object, ...]: ...

    def prepare_codex_home_with_upstreams(
        self,
        endpoint: str | None,
        *,
        workspace_path: Path | None,
        existing_home: str | None,
        master_prompt_file: str | None,
        unsafe_mode: bool = False,
        run_id: str | None = None,
    ) -> tuple[str, tuple[object, ...]]: ...

    def load_existing_claude_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[object, ...]: ...

    def load_existing_agy_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[object, ...]: ...

    def load_existing_cursor_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[object, ...]: ...

    def load_existing_kimi_upstream_servers(
        self, workspace_path: Path | None = None
    ) -> tuple[object, ...]: ...


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


def _invoke_module() -> _InvokeCompatibilitySeam:
    """Return the typed package-level compatibility seam used by runtime tests."""
    return cast("_InvokeCompatibilitySeam", sys.modules["ralph.agents.invoke"])


def _apply_upstream_env(
    upstreams: tuple[UpstreamMcpServer, ...],
    workspace_path: Path | None,
    runtime_env: dict[str, str],
    server_env: dict[str, str],
) -> None:
    """Delegate through the package seam so runtime tests can monkeypatch it."""
    _invoke_module()._apply_upstream_env(upstreams, workspace_path, runtime_env, server_env)


def _canonical_http_mcp_tool_names(endpoint: str) -> tuple[str, ...]:
    try:
        visible_tool_names = _invoke_module().discover_http_mcp_tool_names(endpoint)
    except (PreflightError, ValueError) as exc:
        logger.warning("Failed to discover Ralph MCP tools for provider allowlist: {}", exc)
        return ()
    return canonicalize_tool_names(visible_tool_names)


def _discover_http_mcp_tool_names(endpoint: str) -> list[str]:
    target = parse_http_endpoint(endpoint)
    initialize_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialize_request(),
    )
    ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
    initialized_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialized_notification(),
        session_id=session_id,
    )
    ensure_no_preflight_error(
        "HTTP MCP notifications/initialized", initialized_response.get("error")
    )
    tools_response, _ = post_http_jsonrpc_with_session(
        endpoint,
        target,
        tools_list_request(),
        session_id=session_id,
    )
    ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
    return extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")


def _get_endpoint(runtime_env: dict[str, str], base_env: Mapping[str, str]) -> str | None:
    """Get MCP endpoint from runtime_env or base_env."""
    return runtime_env.get(MCP_ENDPOINT_ENV) or base_env.get(MCP_ENDPOINT_ENV)


def _project_cursor_auth(source: Path, destination: Path) -> None:
    """Project Cursor's auth file into an invocation-owned XDG config root."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
    except FileNotFoundError:
        return
    except OSError:
        try:
            if source.stat().st_size <= 64 * 1024 * 1024:
                shutil.copy2(source, destination)  # filesystem-write-ok: bounded fallback materializes one credential in an invocation-owned private config root
        except FileNotFoundError:
            return


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

        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        provider_config, upstreams = _invoke_module().build_opencode_provider_config(
            opencode_config,
            endpoint,
            unsafe_mode=unsafe_mode,
            workspace_path=workspace_path,
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

        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        runtime_env.setdefault("NANOCODER_TRUST_DIRECTORY", "1")
        nanocoder_mcp_servers = runtime_env.get("NANOCODER_MCPSERVERS") or _env.get(
            "NANOCODER_MCPSERVERS"
        )

        mcp_config, env_upstreams = _invoke_module().build_nanocoder_mcp_config(
            nanocoder_mcp_servers,
            endpoint,
            always_allow=_canonical_http_mcp_tool_names(endpoint),
            unsafe_mode=unsafe_mode,
            workspace_path=workspace_path,
            env=runtime_env or dict(_env),
        )
        runtime_env["NANOCODER_MCPSERVERS"] = mcp_config

        _apply_upstream_env(
            _invoke_module().load_existing_nanocoder_upstream_servers(
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

        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint and master_prompt_file is None:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        run_id = runtime_env.get(str(MCP_RUN_ID_ENV))
        if run_id is None:
            codex_home, upstreams = _invoke_module().prepare_codex_home_with_upstreams(
                endpoint,
                workspace_path=workspace_path,
                existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
                master_prompt_file=master_prompt_file,
                unsafe_mode=unsafe_mode,
            )
        else:
            codex_home, upstreams = _invoke_module().prepare_codex_home_with_upstreams(
                endpoint,
                workspace_path=workspace_path,
                existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
                master_prompt_file=master_prompt_file,
                unsafe_mode=unsafe_mode,
                run_id=run_id,
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
            # filesystem-write-ok: idempotent cleanup of a run-scoped temporary Codex home
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

        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        _apply_upstream_env(
            _invoke_module().load_existing_claude_upstream_servers(workspace_path),
            workspace_path,
            runtime_env,
            server_env,
        )

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )


_AGY_AUTH_ARTIFACTS = (
    "antigravity-oauth-token",
    "settings.json",
    "installation_id",
)


def _project_agy_auth(source_root: Path, destination_root: Path) -> None:
    """Copy AGY's bounded auth artifacts into an invocation-owned private HOME."""
    for artifact_name in _AGY_AUTH_ARTIFACTS:
        source = source_root / artifact_name
        destination = destination_root / artifact_name
        try:
            destination_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)  # filesystem-write-ok: bounded AGY credential projection into an invocation-owned private HOME
        except FileNotFoundError:
            continue


class AgyRuntimeResolver:
    """RuntimeResolver for AgentTransport.AGY.

    AGY receives a private HOME containing generated MCP configs and the
    bounded credential artifacts present in the operator's AGY config directory.
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

        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        source_home = Path(_env.get("HOME", str(Path.home()))).expanduser()
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        resolved_workspace = workspace_path or Path.cwd()
        upstreams = _invoke_module().load_existing_agy_upstream_servers(resolved_workspace)
        _apply_upstream_env(upstreams, resolved_workspace, runtime_env, server_env)
        current_config: dict[str, object] = {
            "mcpServers": {"ralph": {"serverUrl": endpoint}},
            "workspace_path": resolved_workspace,
        }
        payload = json.dumps(
            merge_existing_upstreams(
                "agy", current_config, unsafe_mode=unsafe_mode, workspace_path=resolved_workspace
            ),
            indent=2,
        ).encode("utf-8")
        private_home, cleanup = prepare_private_config_root(
            (
                (Path(".gemini/antigravity-cli/mcp_config.json"), payload),
                (Path(".gemini/config/mcp_config.json"), payload),
            ),
            prefix="ralph-agy-home-",
        )
        _project_agy_auth(
            source_home / ".gemini" / "antigravity-cli",
            private_home / ".gemini" / "antigravity-cli",
        )
        runtime_env["HOME"] = str(private_home)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
            cleanup=cleanup,
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
        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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

    Cursor reads its MCP server configuration from ``~/.cursor/mcp.json``.
    This resolver gives the subprocess a private ``HOME`` with a generated
    ``.cursor/mcp.json`` so concurrent Ralph sessions retain independent
    endpoints without modifying workspace or operator configuration.

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
        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        source_home = Path(_env.get("HOME", str(Path.home()))).expanduser()
        source_config_home = Path(
            _env.get("XDG_CONFIG_HOME") or source_home / ".config"
        ).expanduser()
        runtime_env = dict(extra_env or {})
        credential_store = runtime_env.get(
            "AGENT_CLI_CREDENTIAL_STORE", _env.get("AGENT_CLI_CREDENTIAL_STORE", "file")
        )
        runtime_env["AGENT_CLI_CREDENTIAL_STORE"] = (
            "memory" if credential_store == "memory" else "file"
        )
        cursor_api_key = _env.get("CURSOR_API_KEY")
        if cursor_api_key is not None and "CURSOR_API_KEY" not in runtime_env:
            runtime_env["CURSOR_API_KEY"] = cursor_api_key
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)
        config_files: tuple[tuple[Path, bytes], ...] = ()

        if endpoint:
            resolved_workspace = workspace_path or Path.cwd()
            upstreams = _invoke_module().load_existing_cursor_upstream_servers(resolved_workspace)
            _apply_upstream_env(upstreams, resolved_workspace, runtime_env, server_env)
            current_config: dict[str, object] = {
                "mcpServers": {"ralph": {"url": endpoint}},
                "workspace_path": resolved_workspace,
            }
            payload = json.dumps(
                merge_existing_upstreams(
                    "cursor",
                    current_config,
                    unsafe_mode=unsafe_mode,
                    workspace_path=resolved_workspace,
                ),
                indent=2,
            ).encode("utf-8")
            config_files = ((Path(".cursor/mcp.json"), payload),)

        private_home, cleanup = prepare_private_config_root(
            config_files, prefix="ralph-cursor-home-"
        )
        _mirror_cursor_home(source_home / ".cursor", private_home / ".cursor")
        private_config_home = private_home / "config"
        _project_cursor_auth(
            source_config_home / "cursor" / "auth.json",
            private_config_home / "cursor" / "auth.json",
        )
        runtime_env["HOME"] = str(private_home)
        runtime_env["XDG_CONFIG_HOME"] = str(private_config_home)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
            cleanup=cleanup,
        )


class KimiRuntimeResolver:
    """RuntimeResolver for AgentTransport.KIMI.

    Kimi Code reads ``$KIMI_CODE_HOME/mcp.json``. This resolver assigns a
    private ``KIMI_CODE_HOME`` containing a generated MCP config, so each
    invocation is isolated without touching workspace or operator config.

    The MCP_ENDPOINT_ENV is consumed (and dropped) from the
    ``runtime_env`` implicitly: the endpoint itself is only written into
    the JSON config files, never exported as a literal variable.
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
        _env = (
            base_env if base_env is not None else cast("Mapping[str, str]", os.environ)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        runtime_env = dict(extra_env or {})
        server_env: dict[str, str] = {}
        endpoint = _get_endpoint(runtime_env, _env)

        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)

        resolved_workspace = workspace_path or Path.cwd()
        upstreams = _invoke_module().load_existing_kimi_upstream_servers(resolved_workspace)
        _apply_upstream_env(upstreams, resolved_workspace, runtime_env, server_env)
        current_config: dict[str, object] = {
            "mcpServers": {"ralph": {"url": endpoint}},
            "workspace_path": resolved_workspace,
        }
        payload = json.dumps(
            merge_existing_upstreams(
                "kimi", current_config, unsafe_mode=unsafe_mode, workspace_path=resolved_workspace
            ),
            indent=2,
        ).encode("utf-8")
        private_home, cleanup = prepare_private_config_root(
            ((Path("mcp.json"), payload),), prefix="ralph-kimi-home-"
        )
        runtime_env["KIMI_CODE_HOME"] = str(private_home)

        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
            cleanup=cleanup,
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
    AgentTransport.KIMI: KimiRuntimeResolver,
    AgentTransport.GENERIC: DefaultRuntimeResolver,
}

__all__ = [
    "RUNTIME_RESOLVERS",
    "AgyRuntimeResolver",
    "ClaudeRuntimeResolver",
    "CodexRuntimeResolver",
    "CursorRuntimeResolver",
    "DefaultRuntimeResolver",
    "KimiRuntimeResolver",
    "NanocoderRuntimeResolver",
    "OpencodeRuntimeResolver",
    "PiRuntimeResolver",
    "RuntimeResolver",
]
