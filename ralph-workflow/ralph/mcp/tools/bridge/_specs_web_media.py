"""Tool specs for web and media operations (conditionally included)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.tools.bridge._spec_helpers import _metadata
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.names import (
    DOWNLOAD_URL_TOOL,
    READ_IMAGE_TOOL,
    READ_MEDIA_TOOL,
    VISIT_URL_TOOL,
    WEB_SEARCH_TOOL,
)

if TYPE_CHECKING:
    from ralph.config.mcp_models import McpConfig


def web_media_specs(mcp_config: McpConfig) -> list[ToolSpec]:
    """Return conditionally-included tool specs for web and media operations."""
    specs: list[ToolSpec] = []
    if mcp_config.web_search.enabled:
        specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=WEB_SEARCH_TOOL,
                    description=(
                        "Search the web using a multi-backend fallback chain. "
                        "Required param: query (string). Optional params: limit "
                        "(integer, default 10, max 25), format ('raw'|'summary', "
                        "default 'raw'). Returns search results with titles, "
                        "URLs, and snippets by default. "
                        "``format='summary'`` returns a compact JSON envelope "
                        "with truncated snippets (<=240 chars), a "
                        "``backend_chain_used`` counter, and ``bytes_in``/"
                        "``bytes_out`` size counters. "
                        'Example: {"query": "python 3.12 features", "limit": 5} '
                        "returns 5 search results about Python 3.12; "
                        '{"query": "python", "limit": 5, "format": "summary"} '
                        "returns the same results in a compact envelope."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Search query as a string "
                                    "(example values: 'python features', 'rust async')."
                                ),
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 25,
                                "description": (
                                    "Maximum number of results to return as an integer "
                                    "(default: 10, max: 25, example values: 5, 10, 20)."
                                ),
                                "default": 10,
                            },
                            "format": {
                                "type": "string",
                                "enum": ["raw", "summary"],
                                "description": (
                                    "Output shape. ``raw`` is the legacy "
                                    "Title/URL/Snippet text blocks; "
                                    "``summary`` is a compact JSON envelope "
                                    "with truncated snippets and a "
                                    "``backend_chain_used`` counter."
                                ),
                                "default": "raw",
                            },
                        },
                        "required": ["query"],
                    },
                    required_capability=McpCapability.WEB_SEARCH.value,
                ),
                module_name="ralph.mcp.tools.websearch",
                handler_name="handle_web_search",
            ),
        )
    if mcp_config.web_visit.enabled:
        specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=VISIT_URL_TOOL,
                    description=(
                        "Fetch a single URL and return readable extracted text. "
                        "Required param: url (string, http/https). "
                        "Optional params: with_links (boolean, default false), "
                        "format ('raw'|'metadata', default 'raw'). "
                        "``format='raw'`` returns the full text body plus "
                        "up to 100 outbound links when requested. "
                        "``format='metadata'`` returns bounded metadata "
                        "(head_preview, byte_count, bytes_in/bytes_out) "
                        "and drops the full text body inline. "
                        "On failure returns is_error=true with a status code "
                        "(timeout, unreachable, http_error, unsupported_content, too_large, "
                        "blocked_by_policy, invalid_url)."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": (
                                    "URL to fetch as a string, must use http or https scheme "
                                    "(example values: 'https://example.com/', "
                                    "'https://docs.python.org/3/')."
                                ),
                            },
                            "with_links": {
                                "type": "boolean",
                                "description": (
                                    "Whether to include up to 100 absolute outbound links "
                                    "extracted from the page (default: false)."
                                ),
                                "default": False,
                            },
                            "format": {
                                "type": "string",
                                "enum": ["raw", "metadata"],
                                "description": (
                                    "Output shape. ``raw`` returns the full "
                                    "text body plus optional links; "
                                    "``metadata`` returns bounded metadata "
                                    "(head_preview, byte_count, bytes_in/"
                                    "bytes_out) and drops the full text "
                                    "body inline."
                                ),
                                "default": "raw",
                            },
                        },
                        "required": ["url"],
                    },
                    required_capability=McpCapability.WEB_VISIT.value,
                ),
                module_name="ralph.mcp.tools.webvisit",
                handler_name="handle_visit_url",
            ),
        )
        specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=DOWNLOAD_URL_TOOL,
                    description=(
                        "Download a URL and save its content to a workspace file. "
                        "Required params: url (string, http/https), "
                        "output_path (string, relative path in workspace). "
                        "Optional param: format ('raw'|'summary', default 'raw'). "
                        "``format='raw'`` returns status, effective_url, "
                        "content_type, output_path, and bytes_written. "
                        "``format='summary'`` adds a sha256 fingerprint and a "
                        "bounded head_preview (first 240 bytes) and does NOT "
                        "echo the downloaded body inline. "
                        "On failure returns is_error=true with a status code "
                        "(timeout, unreachable, http_error, unsupported_content, too_large, "
                        "blocked_by_policy, invalid_url)."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": (
                                    "URL to download as a string, must use http or https scheme "
                                    "(example values: 'https://example.com/data.json', "
                                    "'https://cdn.example.com/lib.js')."
                                ),
                            },
                            "output_path": {
                                "type": "string",
                                "description": (
                                    "Relative path in workspace where content will be saved "
                                    "(example values: 'downloads/data.json', 'lib/vendor.js'). "
                                    "Parent directories are created automatically."
                                ),
                            },
                            "format": {
                                "type": "string",
                                "enum": ["raw", "summary"],
                                "description": (
                                    "Output shape. ``raw`` is the legacy "
                                    "metadata-only envelope; ``summary`` "
                                    "adds a sha256 fingerprint and a "
                                    "bounded head_preview and does NOT "
                                    "echo the downloaded body inline."
                                ),
                                "default": "raw",
                            },
                        },
                        "required": ["url", "output_path"],
                    },
                    required_capability=McpCapability.WEB_DOWNLOAD.value,
                ),
                module_name="ralph.mcp.tools.webvisit",
                handler_name="handle_download_url",
            ),
        )
    if mcp_config.media.enabled:
        specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=READ_IMAGE_TOOL,
                    description=(
                        "Read an image file and return it as a base64-encoded content block. "
                        "Requires MediaRead capability and explicit media support enablement. "
                        "Required param: path (string, relative or absolute path). "
                        "Optional param: format ('inline'|'metadata', default 'inline'). "
                        "``format='inline'`` returns the image content block with base64 data "
                        "and MIME type. ``format='metadata'`` returns a bounded JSON "
                        "envelope with mime_type, size_bytes, sha256, width, height, and "
                        "an ``inline_only`` flag; no image bytes are echoed inline. "
                        "Supported formats: png, jpg, jpeg, gif, webp. "
                        'Example: {"path": "docs/screenshot.png"} returns the image as base64.'
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "File path as a string, relative or absolute inside "
                                    "the workspace (example values: 'docs/screenshot.png')."
                                ),
                            },
                            "format": {
                                "type": "string",
                                "enum": ["inline", "metadata"],
                                "description": (
                                    "Output shape. ``inline`` returns the "
                                    "image content block (base64 + MIME); "
                                    "``metadata`` returns a bounded JSON "
                                    "envelope with size, sha256, width, "
                                    "height, and an ``inline_only`` flag."
                                ),
                                "default": "inline",
                            },
                        },
                        "required": ["path"],
                    },
                    required_capability=Capability.MEDIA_READ.value,
                    is_multimodal=True,
                ),
                module_name="ralph.mcp.tools.workspace",
                handler_name="handle_read_image",
            ),
        )
        specs.append(
            ToolSpec(
                metadata=_metadata(
                    name=READ_MEDIA_TOOL,
                    description=(
                        "Read a media file and return the appropriate content block. "
                        "Supports images, PDFs, audio, video, and visually meaningful documents. "
                        "Required param: path (string, relative or absolute path). "
                        "Optional param: format ('inline'|'metadata', default 'inline'). "
                        "``format='inline'`` returns the same block the legacy tool "
                        "returns: image content block for supported inline images; "
                        "resource_reference block for PDFs, audio, video, documents, "
                        "or oversized images. ``format='metadata'`` returns a bounded "
                        "JSON envelope (mime_type, size_bytes, sha256, modality, "
                        "resource_handle) and does NOT echo inline media bytes; the "
                        "artifact is retrievable via ``resources/read`` on the returned "
                        "handle. "
                        'Example: {"path": "docs/report.pdf"} returns a resource_reference '
                        "block; ``format='metadata'`` returns bounded metadata only."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "File path as a string, relative or absolute inside "
                                    "the workspace (example values: 'docs/report.pdf', "
                                    "'audio/clip.mp3', 'screenshot.png')."
                                ),
                            },
                            "format": {
                                "type": "string",
                                "enum": ["inline", "metadata"],
                                "description": (
                                    "Output shape. ``inline`` returns the "
                                    "same block the legacy tool returns; "
                                    "``metadata`` returns a bounded JSON "
                                    "envelope with size, sha256, modality, "
                                    "and a replayable resource handle."
                                ),
                                "default": "inline",
                            },
                        },
                        "required": ["path"],
                    },
                    required_capability=Capability.MEDIA_READ.value,
                    is_multimodal=True,
                ),
                module_name="ralph.mcp.tools.workspace",
                handler_name="handle_read_media",
            ),
        )
    return specs
