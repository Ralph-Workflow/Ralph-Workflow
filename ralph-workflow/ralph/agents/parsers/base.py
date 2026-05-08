"""Base types for agent output parsing.

This module defines the protocol that all parser modules must implement.
`AgentOutputLine` remains available for backward compatibility as legacy
parser output while the typed activity model is introduced separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class AgentOutputLine:
    """Legacy normalised line extracted from agent NDJSON stream.

    This type is preserved for backward compatibility while newer cross-layer
    visibility work adopts the typed activity model.

    Attributes:
        type: Type of the output line (text, tool_use, tool_result, error, etc.).
        content: Text content of the line.
        raw: Raw JSON string from the agent.
        metadata: Additional metadata extracted from the line.
    """

    type: str
    content: str = ""
    raw: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TextAccumulator:
    """Shared delta accumulator for paragraph-boundary text flushing."""

    buffer: str = ""
    raw_lines: list[str] = field(default_factory=list)

    def accumulate(
        self,
        text: str,
        raw: str,
        *,
        kind: str = "text",
        keep_current_when_empty: bool,
    ) -> Iterator[AgentOutputLine]:
        """Append text/raw and yield an AgentOutputLine if a paragraph boundary is reached.

        Args:
            text: Text delta to append to the buffer.
            raw: Raw JSON line to track.
            kind: Output type for the emitted line ('text' or 'thinking').
            keep_current_when_empty: When True, always keep the current raw line in the
                tail after a flush even if remaining buffer is empty (unconditional rule).
                When False, only keep it when remaining is non-empty.
        """
        self.buffer += text
        self.raw_lines.append(raw)
        if "\n\n" in self.buffer:
            parts = self.buffer.split("\n\n", 1)
            flushed = parts[0]
            remaining = parts[1]
            if flushed:
                flushed_raw = "\n".join(self.raw_lines[:-1])
                yield AgentOutputLine(type=kind, content=flushed, raw=flushed_raw)
            self.buffer = remaining
            self.raw_lines = [raw] if (remaining or keep_current_when_empty) else []

    def flush(
        self, *, kind: str = "text", require_strip: bool = False
    ) -> Iterator[AgentOutputLine]:
        """Yield remaining buffer content as an AgentOutputLine if non-empty, then reset.

        Args:
            kind: Output type for the emitted line ('text' or 'thinking').
            require_strip: When True, only emit if buffer.strip() is non-empty (for
                thinking accumulators that should suppress whitespace-only content).
        """
        check = self.buffer.strip() if require_strip else self.buffer
        if check:
            raw_joined = "\n".join(self.raw_lines) if self.raw_lines else ""
            yield AgentOutputLine(type=kind, content=self.buffer, raw=raw_joined)
        self.buffer = ""
        self.raw_lines = []


def _multimodal_block_summary(block: dict[str, object]) -> str | None:
    """Return a bounded readable summary for a multimodal content block, or None.

    Returns a short human-readable placeholder for image and resource_reference
    blocks so they are never silently dropped when only text can be emitted.
    """
    block_type = str(block.get("type", ""))
    if block_type == "image":
        source = block.get("source") or block.get("data") or {}
        mime = (
            source.get("media_type") if isinstance(source, dict) else None
        ) or block.get("mimeType") or block.get("mime_type") or "image"
        return f"[image: {mime}]"
    if block_type == "resource_reference":
        uri = block.get("uri", "")
        modality = block.get("modality", "media")
        return f"[{modality}: {uri}]"
    return None


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
