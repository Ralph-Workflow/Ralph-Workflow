"""Tool specs for web and media operations (conditionally included)."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
                        "Required param: query (string). Optional param: limit (integer, "
                        "default 10, max 25). Returns search results with titles, URLs, "
                        "and snippets. "
                        'Example: {"query": "python 3.12 features", "limit": 5} '
                        "returns 5 search results about Python 3.12."
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
                        },
                        "required": ["query"],
                    },
                    required_capability="WebSearch",
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
                        "Optional param: with_links (boolean, default false) to also include "
                        "up to 100 absolute outbound links. "
                        "Returns JSON with status, title, effective_url, content_type, text, "
                        "and optional links. "
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
                        },
                        "required": ["url"],
                    },
                    required_capability="WebVisit",
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
                        "Returns JSON with status, effective_url, content_type, "
                        "output_path, and bytes_written. "
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
                        },
                        "required": ["url", "output_path"],
                    },
                    required_capability="WebDownload",
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
                        "Returns an image content block with type, base64 data, and MIME type. "
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
                        },
                        "required": ["path"],
                    },
                    required_capability="media.read",
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
                        "For supported inline images within the size limit, returns an image "
                        "content block. For PDFs, audio, video, documents, or oversized images, "
                        "returns a resource_reference block with uri, mimeType, title, modality, "
                        "and delivery fields. The referenced artifact can be retrieved via "
                        "resources/read using the returned URI. "
                        'Example: {"path": "docs/report.pdf"} returns a resource_reference block.'
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
                        },
                        "required": ["path"],
                    },
                    required_capability="media.read",
                    is_multimodal=True,
                ),
                module_name="ralph.mcp.tools.workspace",
                handler_name="handle_read_media",
            ),
        )
    return specs
