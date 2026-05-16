from __future__ import annotations

import base64
from io import StringIO
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
from loguru import logger

from ralph.mcp.multimodal.resources import MediaManifest, parse_media_uri
from ralph.mcp.upstream.client import HttpUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import UpstreamClientFactory, UpstreamRegistry
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
                return {"tools": cast("object", tools)}
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
                return cast("Any", HttpUpstreamClient(server, caller=good_caller))
            return HttpUpstreamClient(server, caller=bad_caller)

        stream = StringIO()
        sink_id = logger.add(stream, level="WARNING")
        try:
            registry = UpstreamRegistry.build(
                [healthy, broken],
                client_factory=cast("UpstreamClientFactory", client_factory),
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
                return cast("Any", HttpUpstreamClient(server, caller=good_caller))
            return HttpUpstreamClient(server, caller=bad_caller)

        stream = StringIO()
        sink_id = logger.add(stream, level="WARNING")
        try:
            UpstreamRegistry.build(
                [healthy, broken],
                client_factory=cast("UpstreamClientFactory", client_factory),
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
            return cast("Any", HttpUpstreamClient(server, caller=bad_caller))

        with pytest.raises(UpstreamValidationError) as excinfo:
            UpstreamRegistry.build(
                [broken],
                client_factory=cast("UpstreamClientFactory", client_factory),
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

    def test_upstream_image_content_block_normalized_to_resource_reference(self) -> None:
        """Upstream image block is normalized to resource_reference content block."""
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

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__image_server__get_screenshot", {}, session=_FakeSession()
        )

        # Image block must be normalized to resource_reference, not rejected
        content = result.get("content", [])
        assert len(content) == 2
        assert content[0].get("type") == "text"
        assert content[1].get("type") == "resource_reference"
        assert str(content[1].get("uri", "")).startswith("ralph://media/")

    def test_upstream_video_content_block_normalized_to_resource_reference(self) -> None:
        """Upstream video block is normalized to resource_reference, not rejected."""
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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__media_server__get_clip", {}, session=_FakeSession()
        )

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        assert block.get("modality") == "video"
        assert block.get("mimeType") == "video/mp4"
        assert str(block.get("uri", "")).startswith("ralph://media/")

    def test_upstream_audio_content_block_normalized_to_resource_reference(self) -> None:
        """Upstream audio block is normalized to resource_reference."""
        server = UpstreamMcpServer(name="audio_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "get_clip", "description": "Audio", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "audio",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "audio/mpeg",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__audio_server__get_clip", {}, session=_FakeSession()
        )

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        assert block.get("modality") == "audio"
        assert block.get("mimeType") == "audio/mpeg"

    def test_upstream_pdf_content_block_normalized_to_resource_reference(self) -> None:
        """Upstream PDF block is normalized to resource_reference."""
        server = UpstreamMcpServer(name="pdf_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "get_doc", "description": "PDF", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "pdf",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "application/pdf",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__pdf_server__get_doc", {}, session=_FakeSession()
        )

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        assert block.get("modality") == "pdf"
        assert block.get("mimeType") == "application/pdf"

    def test_upstream_document_content_block_normalized_to_resource_reference(self) -> None:
        """Upstream document block is normalized to resource_reference."""
        server = UpstreamMcpServer(name="doc_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "get_file", "description": "Document", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "document",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": (
                                "application/vnd.openxmlformats-officedocument"
                                ".wordprocessingml.document"
                            ),
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__doc_server__get_file", {}, session=_FakeSession()
        )

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        assert block.get("modality") == "document"

    def test_upstream_uri_backed_image_preserves_upstream_uri(self) -> None:
        """URI-backed upstream image block preserves the upstream URI."""
        server = UpstreamMcpServer(name="uri_server", transport="http", url="http://unused")
        upstream_uri = "https://example.com/screenshot.png"
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "screenshot", "description": "Screenshot", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "image",
                            "uri": upstream_uri,
                            "mimeType": "image/png",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        result = registry.call_tool("ralph_upstream__uri_server__screenshot", {})

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        assert block.get("uri") == upstream_uri

    def test_upstream_embedded_image_stored_in_session_manifest(self) -> None:
        """Embedded image bytes are stored in session manifest for retrieval."""
        server = UpstreamMcpServer(name="img_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "screenshot", "description": "Screenshot", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "image/png",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        session = _FakeSession()
        result = registry.call_tool("ralph_upstream__img_server__screenshot", {}, session=session)

        content = result.get("content", [])
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference"
        uri = str(block.get("uri", ""))
        assert uri.startswith("ralph://media/")

        # Bytes must be retrievable from the manifest
        artifact_id = parse_media_uri(uri)
        assert artifact_id is not None
        entry = session.media_manifest.get(artifact_id)
        assert entry is not None
        assert entry.raw_bytes == base64.b64decode("SGVsbG8gV29ybGQ=")

    def test_upstream_unknown_block_type_raises_error(self) -> None:
        """Unknown block types raise UpstreamCallError with accepted types listed."""
        server = UpstreamMcpServer(name="bad_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "bad_tool", "description": "Bad", "inputSchema": {}}]},
                {"content": [{"type": "binary_blob", "data": "SGVsbG8="}]},
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__bad_server__bad_tool", {})

        error_message = str(exc_info.value)
        assert "binary_blob" in error_message
        assert "Accepted types" in error_message

    def test_upstream_media_block_missing_uri_and_data_raises_error(self) -> None:
        """Media block with neither uri nor data raises UpstreamCallError."""
        server = UpstreamMcpServer(name="bad_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "bad_tool", "description": "Bad", "inputSchema": {}}]},
                {"content": [{"type": "image", "mimeType": "image/png"}]},
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__bad_server__bad_tool", {})

        assert "cannot normalize" in str(exc_info.value)

    def test_upstream_mime_modality_mismatch_raises_error(self) -> None:
        """Block with MIME type inconsistent with declared block type raises error."""
        server = UpstreamMcpServer(name="mismatch_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "bad_tool", "description": "Bad", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "audio/mpeg",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__mismatch_server__bad_tool", {})

        error_message = str(exc_info.value)
        assert "inconsistent" in error_message or "derived modality" in error_message

    def test_upstream_mixed_modalities_in_single_response(self) -> None:
        """Mixed text+image+audio response is fully normalized."""
        server = UpstreamMcpServer(name="mix_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "mix", "description": "Mixed", "inputSchema": {}}]},
                {
                    "content": [
                        {"type": "text", "text": "caption"},
                        {"type": "image", "data": "SGVsbG8=", "mimeType": "image/png"},
                        {"type": "audio", "data": "V29ybGQ=", "mimeType": "audio/mpeg"},
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool("ralph_upstream__mix_server__mix", {}, session=_FakeSession())

        content = result.get("content", [])
        assert len(content) == 3
        assert content[0].get("type") == "text"
        rr_blocks = [b for b in content if b.get("type") == "resource_reference"]
        assert len(rr_blocks) == 2
        modalities = {b.get("modality") for b in rr_blocks}
        assert modalities == {"image", "audio"}

    def test_upstream_embedded_image_in_content_list_normalized(self) -> None:
        """Upstream content list with image block is normalized to resource_reference."""
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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__mixed_server__get_mixed", {}, session=_FakeSession()
        )

        # Text block passes through; image block becomes resource_reference
        content = result.get("content", [])
        assert len(content) == 2
        assert content[0].get("type") == "text"
        assert content[0].get("text") == "Here is your result"
        assert content[1].get("type") == "resource_reference"
        assert str(content[1].get("uri", "")).startswith("ralph://media/")

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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        result = registry.call_tool("ralph_upstream__empty_server__noop", {})
        assert result == {"content": []}

    def test_no_silent_fallback_for_multimodal_content(self) -> None:
        """Image content is normalized to resource_reference, not silently stringified."""
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
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        class _FakeSession:
            media_manifest = MediaManifest()

        result = registry.call_tool(
            "ralph_upstream__strict_server__get_both", {}, session=_FakeSession()
        )

        content = result.get("content", [])
        # Image must NOT be silently stringified (raw base64 must not appear in any text block)
        text_values = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        assert not any("SGVsbG8gV29ybGQ=" in tv for tv in text_values)
        # Image block must be normalized to resource_reference
        rr_blocks = [
            b for b in content if isinstance(b, dict) and b.get("type") == "resource_reference"
        ]
        assert len(rr_blocks) == 1
        assert str(rr_blocks[0].get("uri", "")).startswith("ralph://media/")

    def test_embedded_media_without_session_raises_explicit_error(self) -> None:
        """Embedded upstream media without a session raises an explicit error."""
        # Verify no synthetic ralph://media/... URI is minted when bytes cannot be stored.
        server = UpstreamMcpServer(name="embed_server", transport="http", url="http://unused")
        client = self._make_http_client(
            server,
            [
                {"tools": [{"name": "snap", "description": "Snapshot", "inputSchema": {}}]},
                {
                    "content": [
                        {
                            "type": "image",
                            "data": "SGVsbG8gV29ybGQ=",
                            "mimeType": "image/png",
                        }
                    ]
                },
            ],
        )

        registry = UpstreamRegistry.build(
            [server],
            client_factory=cast("UpstreamClientFactory", lambda srv: client),
        )

        with pytest.raises(UpstreamCallError) as exc_info:
            registry.call_tool("ralph_upstream__embed_server__snap", {})

        error_message = str(exc_info.value)
        assert "no active session" in error_message
        assert "Embedded media requires" in error_message


# ---------------------------------------------------------------------------
# Named acceptance tests: session manifest storage (Step 3)
# ---------------------------------------------------------------------------


def _make_single_response_client(
    server: UpstreamMcpServer,
    tool_name: str,
    tool_description: str,
    content_block: dict[str, object],
) -> HttpUpstreamClient:
    """Create a fake HTTP client returning a single-block tool response."""
    responses: list[dict[str, object]] = [
        {"tools": [{"name": tool_name, "description": tool_description, "inputSchema": {}}]},
        {"content": [content_block]},
    ]
    responses_copy = list(responses)
    index = {"value": 0}

    def caller(method: str, params: dict[str, object]) -> dict[str, object]:
        idx = index["value"]
        index["value"] += 1
        if idx < len(responses_copy):
            return responses_copy[idx]
        return {}

    return cast("Any", HttpUpstreamClient(server, caller=caller))


def test_upstream_embedded_audio_is_stored_in_session_manifest() -> None:
    """After normalizing an upstream audio block, the session manifest must hold the entry.

    The resource_reference URI in the returned block must correspond to an artifact
    stored in the session manifest with correct modality and mime_type metadata.
    """
    server = UpstreamMcpServer(name="audio_srv", transport="http", url="http://unused")
    client = _make_single_response_client(
        server,
        tool_name="get_clip",
        tool_description="Audio clip",
        content_block={"type": "audio", "data": "SGVsbG8gV29ybGQ=", "mimeType": "audio/mpeg"},
    )

    registry = UpstreamRegistry.build(
        [server],
        client_factory=cast("UpstreamClientFactory", lambda _srv: client),
    )

    class _FakeSession:
        media_manifest = MediaManifest()

    session = _FakeSession()
    result = registry.call_tool("ralph_upstream__audio_srv__get_clip", {}, session=session)

    content = result.get("content", [])
    assert len(content) == 1
    block = content[0]
    assert block.get("type") == "resource_reference"
    uri = str(block.get("uri", ""))
    assert uri.startswith("ralph://media/")

    # Verify the manifest has the entry
    artifact_id = parse_media_uri(uri)
    assert artifact_id is not None, f"Expected valid ralph://media/ URI, got: {uri!r}"
    entry = session.media_manifest.get(artifact_id)
    assert entry is not None, f"Session manifest must store the audio artifact; URI={uri!r}"
    assert entry.modality == "audio", (
        f"Manifest entry modality must be 'audio', got: {entry.modality!r}"
    )
    assert entry.mime_type == "audio/mpeg", (
        f"Manifest MIME must be 'audio/mpeg', got: {entry.mime_type!r}"
    )
    assert entry.raw_bytes, "Manifest entry must carry the decoded bytes"


def test_upstream_embedded_video_is_stored_in_session_manifest() -> None:
    """After normalizing an upstream video block, the session manifest must hold the entry."""
    server = UpstreamMcpServer(name="video_srv", transport="http", url="http://unused")
    client = _make_single_response_client(
        server,
        tool_name="get_video",
        tool_description="Video clip",
        content_block={"type": "video", "data": "SGVsbG8gV29ybGQ=", "mimeType": "video/mp4"},
    )

    registry = UpstreamRegistry.build(
        [server],
        client_factory=cast("UpstreamClientFactory", lambda _srv: client),
    )

    class _FakeSession:
        media_manifest = MediaManifest()

    session = _FakeSession()
    result = registry.call_tool("ralph_upstream__video_srv__get_video", {}, session=session)

    content = result.get("content", [])
    assert len(content) == 1
    block = content[0]
    assert block.get("type") == "resource_reference"
    uri = str(block.get("uri", ""))
    assert uri.startswith("ralph://media/")

    artifact_id = parse_media_uri(uri)
    assert artifact_id is not None
    entry = session.media_manifest.get(artifact_id)
    assert entry is not None, f"Session manifest must store the video artifact; URI={uri!r}"
    assert entry.modality == "video", (
        f"Manifest entry modality must be 'video', got: {entry.modality!r}"
    )
    assert entry.mime_type == "video/mp4", (
        f"Manifest MIME must be 'video/mp4', got: {entry.mime_type!r}"
    )


def test_upstream_mixed_modalities_preserve_order_and_modality_metadata() -> None:
    """Mixed text+image+audio response must preserve block order and modality metadata.

    The normalized output must maintain the same positional order as the upstream
    response and each resource_reference block must carry correct modality and URI.
    """
    server = UpstreamMcpServer(name="mix_srv", transport="http", url="http://unused")
    responses: list[dict[str, object]] = [
        {"tools": [{"name": "multi", "description": "Multi-modal tool", "inputSchema": {}}]},
        {
            "content": [
                {"type": "text", "text": "caption text"},
                {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
                {"type": "audio", "data": "YXVkaW8=", "mimeType": "audio/mpeg"},
            ]
        },
    ]
    responses_copy = list(responses)
    index = {"value": 0}

    def caller(method: str, params: dict[str, object]) -> dict[str, object]:
        idx = index["value"]
        index["value"] += 1
        return responses_copy[idx] if idx < len(responses_copy) else {}

    client = cast("Any", HttpUpstreamClient(server, caller=caller))

    class _FakeSession:
        media_manifest = MediaManifest()

    session = _FakeSession()
    registry = UpstreamRegistry.build(
        [server],
        client_factory=cast("UpstreamClientFactory", lambda _srv: client),
    )

    result = registry.call_tool("ralph_upstream__mix_srv__multi", {}, session=session)

    content = result.get("content", [])
    assert len(content) == 3, f"Expected 3 blocks (order preserved), got: {len(content)}"

    # Order: text, resource_reference(image), resource_reference(audio)
    assert content[0].get("type") == "text", (
        f"Block 0 must be text, got: {content[0].get('type')!r}"
    )
    assert content[1].get("type") == "resource_reference", (
        f"Block 1 must be resource_reference, got: {content[1]}"
    )
    assert content[2].get("type") == "resource_reference", (
        f"Block 2 must be resource_reference, got: {content[2]}"
    )

    # Modality metadata must be correct for each block
    assert content[1].get("modality") == "image", (
        f"Block 1 modality must be 'image', got: {content[1].get('modality')!r}"
    )
    assert content[2].get("modality") == "audio", (
        f"Block 2 modality must be 'audio', got: {content[2].get('modality')!r}"
    )
    assert str(content[1].get("uri", "")).startswith("ralph://media/")
    assert str(content[2].get("uri", "")).startswith("ralph://media/")

    # Both resource_references must be in the manifest with correct metadata
    for block in content[1:]:
        uri = str(block.get("uri", ""))
        artifact_id = parse_media_uri(uri)
        assert artifact_id is not None, f"Block URI must be a valid ralph://media/ URI: {uri!r}"
        entry = session.media_manifest.get(artifact_id)
        assert entry is not None, f"Manifest must store artifact for URI={uri!r}"
        assert entry.modality == block.get("modality"), (
            f"Manifest modality must match block modality for URI={uri!r}"
        )
