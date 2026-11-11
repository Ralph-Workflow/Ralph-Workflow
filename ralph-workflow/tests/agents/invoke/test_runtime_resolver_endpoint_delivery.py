from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._errors import UnsupportedMcpTransportError
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.agents.invoke._runtime_resolvers import ResolvedInvocationRuntime


_ENDPOINT = "http://127.0.0.1:9/mcp"


def _no_tool_discovery(endpoint: str) -> list[str]:
    return []


@pytest.fixture(autouse=True)
def _stub_tool_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ralph.agents.invoke.discover_http_mcp_tool_names", _no_tool_discovery)


def _make_config(transport: AgentTransport) -> AgentConfig:
    return AgentConfig(cmd=transport.value.lower(), transport=transport)


def _resolve(transport: AgentTransport, workspace_path: Path) -> ResolvedInvocationRuntime:
    resolver_cls = RUNTIME_RESOLVERS[transport]
    return resolver_cls().resolve(
        config=_make_config(transport),
        extra_env={"RALPH_MCP_ENDPOINT": _ENDPOINT, "RALPH_MCP_RUN_ID": "test"},
        workspace_path=workspace_path,
        base_env={},
    )


_DELIVERY_CHANNELS: tuple[
    tuple[AgentTransport, tuple[str, ...], Callable[[Path], Path | None]], ...
] = (
    (AgentTransport.CLAUDE, ("RALPH_MCP_ENDPOINT",), lambda _ws: None),
    (
        AgentTransport.CLAUDE_INTERACTIVE,
        ("RALPH_MCP_ENDPOINT",),
        lambda _ws: None,
    ),
    (AgentTransport.AGY, ("HOME",), lambda _ws: None),
    (AgentTransport.CURSOR, ("HOME",), lambda _ws: None),
    (
        AgentTransport.CODEX,
        ("RALPH_MCP_ENDPOINT",),
        lambda _ws: None,
    ),
    (
        AgentTransport.OPENCODE,
        ("RALPH_MCP_ENDPOINT", "OPENCODE_CONFIG_CONTENT"),
        lambda _ws: None,
    ),
    (
        AgentTransport.NANOCODER,
        ("RALPH_MCP_ENDPOINT", "NANOCODER_MCPSERVERS"),
        lambda _ws: None,
    ),
    (
        AgentTransport.PI,
        ("RALPH_PI_MCP_EXTENSION",),
        lambda ws: ws / ".agent" / "tmp" / "ralph_pi_mcp_extension.ts",
    ),
    (AgentTransport.KIMI, ("KIMI_CODE_HOME",), lambda _ws: None),
)


def _private_config_path(transport: AgentTransport, runtime: ResolvedInvocationRuntime) -> Path:
    env = runtime.agent_env
    assert env is not None
    match transport:
        case AgentTransport.CURSOR:
            return Path(env["HOME"]) / ".cursor" / "mcp.json"
        case AgentTransport.AGY:
            return Path(env["HOME"]) / ".gemini" / "config" / "mcp_config.json"
        case AgentTransport.KIMI:
            return Path(env["KIMI_CODE_HOME"]) / "mcp.json"
        case unexpected:
            raise AssertionError(f"private config is not defined for {unexpected.name}")


class TestRuntimeResolverEndpointDelivery:
    def test_delivery_table_covers_every_non_generic_transport(self) -> None:
        table_transports = {entry[0] for entry in _DELIVERY_CHANNELS}
        registered_transports = {
            transport for transport in RUNTIME_RESOLVERS if transport is not AgentTransport.GENERIC
        }
        missing = registered_transports - table_transports
        assert not missing, (
            f"_DELIVERY_CHANNELS is missing entries for: "
            f"{sorted(t.name for t in missing)}. "
            f"Add a (transport, env_keys, file_locator) tuple so the "
            f"per-transport channel assertion covers the new transport."
        )
        extras = table_transports - registered_transports
        assert not extras, (
            f"_DELIVERY_CHANNELS has stale entries for: "
            f"{sorted(t.name for t in extras)}. "
            f"Either the transport was removed from RUNTIME_RESOLVERS or "
            f"this entry is orphaned; remove the row."
        )

    def test_delivery_table_covers_exactly_one_generic_case(self) -> None:
        table_transports = {entry[0] for entry in _DELIVERY_CHANNELS}
        assert AgentTransport.GENERIC not in table_transports, (
            "GENERIC transport must not appear in _DELIVERY_CHANNELS; "
            "it raises UnsupportedMcpTransportError and has no delivery "
            "channel to assert."
        )

    @pytest.mark.parametrize(
        ("transport", "env_keys", "_file_locator"),
        _DELIVERY_CHANNELS,
        ids=[entry[0].name for entry in _DELIVERY_CHANNELS],
    )
    def test_mcp_endpoint_echo(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        _file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        runtime = _resolve(transport, tmp_path)
        assert runtime.mcp_endpoint == _ENDPOINT, (
            f"transport {transport.name!r}: mcp_endpoint echoed "
            f"{runtime.mcp_endpoint!r}, expected {_ENDPOINT!r}"
        )

    @pytest.mark.parametrize(
        "transport",
        (AgentTransport.CURSOR, AgentTransport.AGY, AgentTransport.KIMI),
    )
    def test_isolated_runtimes_overlap_with_distinct_endpoints_without_shared_mutation(
        self, transport: AgentTransport, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        operator_home = tmp_path / "operator-home"
        operator_config = operator_home / ".cursor" / "mcp.json"
        operator_config.parent.mkdir(parents=True)
        operator_bytes = b'{"mcpServers":{"operator":{"url":"http://operator"}}}'
        operator_config.write_bytes(operator_bytes)
        second_endpoint = "http://127.0.0.1:10/mcp"
        resolver_cls = RUNTIME_RESOLVERS[transport]
        first_runtime = _resolve(transport, workspace)
        second_runtime = resolver_cls().resolve(
            config=_make_config(transport),
            extra_env={"RALPH_MCP_ENDPOINT": second_endpoint, "RALPH_MCP_RUN_ID": "second"},
            workspace_path=workspace,
            base_env={},
        )
        try:
            first_config = _private_config_path(transport, first_runtime)
            second_config = _private_config_path(transport, second_runtime)
            assert first_config != second_config
            assert _ENDPOINT in first_config.read_text(encoding="utf-8")
            assert second_endpoint in second_config.read_text(encoding="utf-8")
            assert operator_config.read_bytes() == operator_bytes
            assert not (workspace / ".cursor").exists()
            assert not (workspace / ".kimi-code").exists()
            assert not (workspace / ".agents").exists()
        finally:
            first_cleanup = first_runtime.cleanup
            second_cleanup = second_runtime.cleanup
            assert first_cleanup is not None
            assert second_cleanup is not None
            first_cleanup()
            second_cleanup()

        first_root = (
            first_config.parent if transport is AgentTransport.KIMI else first_config.parent.parent
        )
        second_root = (
            second_config.parent
            if transport is AgentTransport.KIMI
            else second_config.parent.parent
        )
        assert not first_root.exists()
        assert not second_root.exists()

    @pytest.mark.parametrize(
        ("transport", "env_keys", "_file_locator"),
        _DELIVERY_CHANNELS,
        ids=[entry[0].name for entry in _DELIVERY_CHANNELS],
    )
    def test_env_keys_carry_endpoint(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        _file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        runtime = _resolve(transport, tmp_path)
        env = runtime.agent_env or {}
        for key in env_keys:
            assert key in env, (
                f"transport {transport.name!r}: agent_env missing key "
                f"{key!r}; the table declared it as a delivery channel"
            )
            value = env[key]
            if transport in {
                AgentTransport.PI,
                AgentTransport.AGY,
                AgentTransport.CURSOR,
                AgentTransport.KIMI,
            }:
                continue
            assert isinstance(value, str), (
                f"transport {transport.name!r}: agent_env[{key!r}] is "
                f"not a string (got {type(value).__name__})"
            )
            assert _ENDPOINT in value, (
                f"transport {transport.name!r}: agent_env[{key!r}] does "
                f"not contain the endpoint; the channel contract is broken"
            )

    @pytest.mark.parametrize(
        ("transport", "env_keys", "file_locator"),
        [entry for entry in _DELIVERY_CHANNELS if entry[2](Path("/tmp")) is not None],
        ids=[entry[0].name for entry in _DELIVERY_CHANNELS if entry[2](Path("/tmp")) is not None],
    )
    def test_file_channel_carries_endpoint(
        self,
        transport: AgentTransport,
        env_keys: tuple[str, ...],
        file_locator: Callable[[Path], Path | None],
        tmp_path: Path,
    ) -> None:
        runtime = _resolve(transport, tmp_path)
        try:
            file_path = file_locator(tmp_path)
            if file_path is None:
                pytest.skip("no file channel for this transport")
            assert file_path.is_file(), (
                f"transport {transport.name!r}: expected file channel at {file_path} does not exist"
            )
            content = file_path.read_text(encoding="utf-8")
            assert _ENDPOINT in content, (
                f"transport {transport.name!r}: file channel at "
                f"{file_path} does not contain the endpoint; the "
                f"channel contract is broken"
            )
        finally:
            if runtime.cleanup is not None:
                runtime.cleanup()

    def test_codex_config_toml_carries_endpoint(self, tmp_path: Path) -> None:
        runtime = _resolve(AgentTransport.CODEX, tmp_path)
        try:
            codex_home = runtime.agent_env["CODEX_HOME"]
            config_toml = Path(codex_home) / "config.toml"
            assert config_toml.is_file(), f"CODEX config.toml not written at {config_toml}"
            content = config_toml.read_text(encoding="utf-8")
            assert _ENDPOINT in content, (
                f"CODEX config.toml at {config_toml} does not contain "
                f"the endpoint; the per-run MCP wiring is broken"
            )
        finally:
            if runtime.cleanup is not None:
                runtime.cleanup()

    def test_generic_resolver_raises_when_endpoint_supplied(self, tmp_path: Path) -> None:
        resolver_cls = RUNTIME_RESOLVERS[AgentTransport.GENERIC]
        config = _make_config(AgentTransport.GENERIC)
        with pytest.raises(UnsupportedMcpTransportError):
            resolver_cls().resolve(
                config=config,
                extra_env={"RALPH_MCP_ENDPOINT": _ENDPOINT, "RALPH_MCP_RUN_ID": "test"},
                workspace_path=tmp_path,
                base_env={},
            )

    def test_generic_resolver_succeeds_without_endpoint(self, tmp_path: Path) -> None:
        resolver_cls = RUNTIME_RESOLVERS[AgentTransport.GENERIC]
        config = _make_config(AgentTransport.GENERIC)
        runtime = resolver_cls().resolve(
            config=config,
            extra_env=None,
            workspace_path=tmp_path,
            base_env={},
        )
        assert runtime.mcp_endpoint is None
        assert runtime.agent_env is None or "RALPH_MCP_ENDPOINT" not in (runtime.agent_env or {})

    def test_opencode_resolver_returns_endpoint_after_s2_fix(self, tmp_path: Path) -> None:
        runtime = _resolve(AgentTransport.OPENCODE, tmp_path)
        assert runtime.mcp_endpoint == _ENDPOINT, (
            "OpencodeRuntimeResolver regression: mcp_endpoint not set "
            "after the S-2 fix; the multimodal smoke for OPENCODE will "
            "re-fail with the S-1 evidence 'endpoint never reached the agent'"
        )
