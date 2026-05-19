"""Serializable MCP tool result."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal.artifacts import (
    AudioContent,
    DocumentContent,
    ImageContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
)

from .tool_content import ToolContent

type ContentBlock = (
    ToolContent
    | ImageContent
    | PdfContent
    | DocumentContent
    | AudioContent
    | VideoContent
    | ResourceReferenceContent
)


@dataclass(frozen=True)
class ToolResult:
    """Serializable MCP tool result."""

    content: list[ContentBlock]
    is_error: bool | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize the result to an MCP-compatible dictionary."""
        return {
            "content": [item.to_dict() for item in self.content],
            "isError": self.is_error,
        }
