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


# =============================================================================
# Upstream multimodal boundary rejection tests (Task 6)
# =============================================================================


class TestUpstreamMultimodalBoundary:
    """Tests for upstream multimodal content rejection policy (Task 6)."""

    def _make_http_client(
        self, server: UpstreamMcpServer, responses: list[dict[str, object]]
    ) -> HttpUpstreamClient:
        """Create an HTTP upstream client that returns pre-programmed responses."""
        responses_copy = list(responses)
        index = {"value": 0}

        def caller(method: str, params: dict[str, object]) -> dict[str, object]:
            idx = index["value"]
            index["value"] += 1
            if idx < len(responses_copy):
                return responses_copy[idx]
            return {}

        return HttpUpstreamClient(server, caller=caller)

    def test_upstream_image_content_block_raises_upstream_call_error(self) -> None:
        """Upstream image block raises UpstreamCallError with clear message."""
        server = UpstreamMcpServer(name="image_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {
                    "tools": [
                        {
                            "name": "get_screenshot",
                            "description": "Screenshot",
                            "inputSchema": {},
                        }
                    ]
                },
                {
                    "content": [
                        {"type": "text", "text": "Loading..."},
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "image/png",
                        },
                    ]
                },
            ],
        )

        # Register tools
        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        # Attempt to call the tool
        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__image_server__get_screenshot", {})

        error_message = str(exc_info.value)
        # Error must mention multimodal
        assert "multimodal" in error_message.lower()
        # Error must identify the problematic type
        assert "image" in error_message
        # Error must mention the tool or server
        assert "get_screenshot" in error_message or "image_server" in error_message

    def test_upstream_video_content_block_raises_upstream_call_error(self) -> None:
        """Upstream tool returning video content block raises UpstreamCallError."""
        server = UpstreamMcpServer(name="media_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "get_clip", "description": "Video clip", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "video",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "video/mp4",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__media_server__get_clip", {})

        error_message = str(exc_info.value)
        assert "multimodal" in error_message.lower()
        assert "video" in error_message

    def test_upstream_embedded_image_in_content_list_raises(self) -> None:
        """Upstream tool returning content list with non-text block at any index raises error."""
        server = UpstreamMcpServer(name="mixed_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {
                    "tools": [
                        {"name": "get_mixed", "description": "Mixed content", "inputSchema": {}}
                    ]
                },
                {
                    "content": [
                        {"type": "text", "text": "Here is your result"},
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "image/png",
                        },
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__mixed_server__get_mixed", {})

        # Error should clearly reject the multimodal payload
        error_message = str(exc_info.value)
        assert "multimodal" in error_message.lower()

    def test_upstream_text_only_content_passthrough_works(self) -> None:
        """Upstream tool returning only text content blocks succeeds normally."""
        server = UpstreamMcpServer(name="text_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {}}]},
                {"content": [{"type": "text", "text": "hello world"}]},
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        result = registry.call_tool("ralph_upstream__text_server__echo", {})

        # Should pass through without error
        assert isinstance(result, dict)
        content = result.get("content", [])
        assert len(content) == 1
        assert content[0].get("type") == "text"
        assert content[0].get("text") == "hello world"

    def test_upstream_tool_without_content_field_succeeds(self) -> None:
        """Upstream tool returning result without content field succeeds."""
        server = UpstreamMcpServer(name="minimal_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "ping", "description": "Ping", "inputSchema": {}}]},
                {"result": "pong"},
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        result = registry.call_tool("ralph_upstream__minimal_server__ping", {})
        assert result == {"result": "pong"}

    def test_upstream_tool_with_empty_content_succeeds(self) -> None:
        """Upstream tool returning empty content list succeeds."""
        server = UpstreamMcpServer(name="empty_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "noop", "description": "No-op", "inputSchema": {}}]},
                {"content": []},
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        result = registry.call_tool("ralph_upstream__empty_server__noop", {})
        assert result == {"content": []}

    def test_no_silent_fallback_for_multimodal_content(self) -> None:
        """There is no silent fallback path that stringifies multimodal content."""
        server = UpstreamMcpServer(name="strict_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {
                    "tools": [
                        {
                            "name": "get_both",
                            "description": "Text and image",
                            "inputSchema": {},
                        }
                    ]
                },
                {
                    "content": [
                        {"type": "text", "text": "see image below"},
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "image/png",
                        },
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=lambda srv: client,  # type: ignore[arg-type]
        )

        # Must raise, NOT silently convert image to string
        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__strict_server__get_both", {})

        error_message = str(exc_info.value)
        # Must NOT have silently stringified the image block
        assert "SGVsbG8gV29ybGQ=" not in error_message or "multimodal" in error_message.lower()
        # Must clearly reject
        assert "not supported" in error_message.lower() or "multimodal" in error_message.lower()
