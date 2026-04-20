from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
from loguru import logger

from ralph.mcp.upstream.client import HttpUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.mcp.upstream.validation import UpstreamValidationError


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


class TestUpstreamRegistryWarningBehavior:
    def _make_tools_caller(self, tools: list[dict[str, object]]) -> object:
        def caller(method: str, params: dict[str, object]) -> dict[str, object]:
            if method == "tools/list":
                return {"tools": tools}  # type: ignore[return-value]
            return {}

        return caller

    def test_warning_is_emitted_when_an_upstream_server_is_unreachable(self) -> None:
        healthy = UpstreamMcpServer(name="healthy", transport="http", url="http://unused")
        broken = UpstreamMcpServer(name="broken", transport="http", url="http://unused")

        good_caller = self._make_tools_caller(
            [{"name": "ping", "description": "Ping", "inputSchema": {}}]
        )

        def bad_caller(method: str, params: dict[str, object]) -> dict[str, object]:
            raise UpstreamCallError("server unreachable")

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            if server.name == "healthy":
                return HttpUpstreamClient(server, caller=good_caller)  # type: ignore[arg-type]
            return HttpUpstreamClient(server, caller=bad_caller)

        stream = StringIO()
        sink_id = logger.add(stream, level="WARNING")
        try:
            registry = UpstreamRegistry.build(
                [healthy, broken],
                client_factory=client_factory,  # type: ignore[arg-type]
                on_unreachable="warn_and_skip",
            )
        finally:
            logger.remove(sink_id)

        aliases = {tool.alias for tool in registry.tool_definitions()}
        warning_output = stream.getvalue()

        assert "ralph_upstream__healthy__ping" in aliases
        assert "broken" not in aliases
        assert "broken" in warning_output
        assert "server unreachable" in warning_output

    def test_warning_does_not_leak_upstream_env_secrets(self) -> None:
        secret = "super-secret-token"
        healthy = UpstreamMcpServer(name="healthy", transport="http", url="http://unused")
        broken = UpstreamMcpServer(
            name="broken",
            transport="http",
            url="http://unused",
            env={"API_KEY": secret},
        )

        good_caller = self._make_tools_caller(
            [{"name": "ping", "description": "Ping", "inputSchema": {}}]
        )

        def bad_caller(method: str, params: dict[str, object]) -> dict[str, object]:
            raise UpstreamCallError("server unreachable")

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            if server.name == "healthy":
                return HttpUpstreamClient(server, caller=good_caller)  # type: ignore[arg-type]
            return HttpUpstreamClient(server, caller=bad_caller)

        stream = StringIO()
        sink_id = logger.add(stream, level="WARNING")
        try:
            UpstreamRegistry.build(
                [healthy, broken],
                client_factory=client_factory,  # type: ignore[arg-type]
                on_unreachable="warn_and_skip",
            )
        finally:
            logger.remove(sink_id)

        warning_output = stream.getvalue()

        assert "broken" in warning_output
        assert "server unreachable" in warning_output
        assert "API_KEY" not in warning_output
        assert secret not in warning_output

    def test_build_raises_by_default_when_upstream_is_unreachable(self) -> None:
        secret = "super-secret-token"
        broken = UpstreamMcpServer(
            name="broken",
            transport="http",
            url="http://unused",
            env={"API_KEY": secret},
        )

        def bad_caller(method: str, params: dict[str, object]) -> dict[str, object]:
            raise UpstreamCallError("server unreachable")

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            return HttpUpstreamClient(server, caller=bad_caller)  # type: ignore[arg-type]

        with pytest.raises(UpstreamValidationError) as excinfo:
            UpstreamRegistry.build(
                [broken],
                client_factory=client_factory,  # type: ignore[arg-type]
            )

        message = str(excinfo.value)
        assert "broken" in message
        assert "API_KEY" in message
        assert secret not in message
