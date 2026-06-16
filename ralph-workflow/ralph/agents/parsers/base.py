"""Base types for agent output parsing.

This module defines the parser protocol and shared text-block helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ._event_classification import (
    LIFECYCLE_EVENT_TYPES,
    LIFECYCLE_KINDS,
    is_lifecycle_event,
    is_lifecycle_kind,
    is_session_metadata_event,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .agent_output_line import AgentOutputLine

__all__ = [
    "LIFECYCLE_EVENT_TYPES",
    "LIFECYCLE_KINDS",
    "extract_error_message",
    "is_lifecycle_event",
    "is_lifecycle_kind",
    "is_session_metadata_event",
]


def _multimodal_block_summary(block: dict[str, object]) -> str | None:
    """Return a bounded readable summary for a multimodal content block, or None.

    Returns a short human-readable placeholder for image and resource_reference
    blocks so they are never silently dropped when only text can be emitted.
    """
    block_type = str(block.get("type", ""))
    if block_type == "image":
        source = block.get("source") or block.get("data") or {}
        mime = (
            (source.get("media_type") if isinstance(source, dict) else None)
            or block.get("mimeType")
            or block.get("mime_type")
            or "image"
        )
        return f"[image: {mime}]"
    if block_type == "resource_reference":
        uri = block.get("uri", "")
        modality = block.get("modality", "media")
        return f"[{modality}: {uri}]"
    return None


def extract_error_message(obj: object) -> str:
    """Extract an error message string from a parsed JSON object.

    Resolution order (union of all per-parser bodies):
    1. obj['error'] dict -> message, type, or name field (claude/codex/opencode/gemini)
    2. obj['error'] non-empty string (codex/generic)
    3. obj.get('message') (codex/opencode/generic)
    4. obj.get('error') non-dict truthy value (codex fallback)
    5. obj.get('msg') (generic)
    6. 'unknown error'

    Note: claude.py previously returned 'unknown' (not 'unknown error') when
    error_obj was not a dict. The unified helper returns 'unknown error' in
    that branch, a one-character behavior change documented in the module
    docstring.
    """
    if not isinstance(obj, dict):
        return "unknown error"
    error_val = obj.get("error")
    if isinstance(error_val, dict):
        return str(
            error_val.get(
                "message", error_val.get("type", error_val.get("name", "unknown error"))
            )
        )

    result = "unknown error"
    if isinstance(error_val, str) and error_val:
        result = error_val
    elif obj.get("message"):
        result = str(obj.get("message"))
    elif error_val:
        result = str(error_val)
    elif obj.get("msg"):
        result = str(obj.get("msg"))

    return result


def stringify_text_blocks(value: object, *, require_text_type: bool = False) -> str:
    """Extract text from a string or a list of text-block dicts.

    Args:
        value: A plain string, or a list of dicts with a 'text' field.
        require_text_type: When True, only include dicts where type=='text' (Claude
            tool_result rule). When False, include any dict with a 'text' key
            (OpenCode output rule). In both modes, multimodal blocks (image,
            resource_reference) emit a bounded readable placeholder rather than
            being silently dropped.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", ""))
            if require_text_type:
                if item_type == "text":
                    text = str(item.get("text", ""))
                    if text:
                        parts.append(text)
                    continue
            elif "text" in item:
                text = str(item.get("text", ""))
                if text:
                    parts.append(text)
                continue
            summary = _multimodal_block_summary(item)
            if summary is not None:
                parts.append(summary)
        if parts:
            return "\n".join(part for part in parts if part)
    return str(value)


@runtime_checkable
class AgentParser(Protocol):
    """Protocol all parser modules must implement.

    A parser takes raw lines from an agent's stdout and yields
    normalized AgentOutputLine instances.
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse agent output lines.

        Args:
            lines: Iterator of raw lines from agent stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        ...
