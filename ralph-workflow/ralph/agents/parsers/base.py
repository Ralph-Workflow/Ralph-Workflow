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
    if not isinstance(obj, dict):
        return "unknown error"
    error_val = obj.get("error")
    if isinstance(error_val, dict):
        for key in ("message", "type"):
            candidate = error_val.get(key)
            if candidate:
                return str(candidate)
        data = error_val.get("data")
        if isinstance(data, dict):
            nested = data.get("message")
            if nested:
                return str(nested)
        return str(error_val.get("name", "unknown error"))

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
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]: ...
