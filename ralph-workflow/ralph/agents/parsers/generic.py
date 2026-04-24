"""Generic NDJSON parser for other agents.

This parser handles NDJSON output from agents that don't have
a dedicated parser. It attempts to extract text content and
error information from common NDJSON formats, with robust delta
accumulation for streaming text responses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _TextAccumulator:
    buffer: str = ""
    raw_lines: list[str] = field(default_factory=list)


# Threshold: content shorter than this without paragraph boundary
# is treated as a streaming delta and accumulated.
# Content at or above this threshold is treated as a standalone message.
_SHORT_CONTENT_THRESHOLD = 200

# Bare JSON event type values that carry no user payload — suppress silently.
# Only exact matches after str.lower() on the "type" field are suppressed;
# longer strings like "starting the analysis" are never touched.
_LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"start", "begin", "ready", "thread.started", "turn.started", "message_start", "heartbeat"}
)


class GenericParser:
    """Generic NDJSON parser for unknown or simple agent formats.

    This parser handles NDJSON by:
    1. Parsing each line as JSON
    2. Looking for common text fields (content, text, message, output)
    3. Accumulating short text content and flushing on paragraph boundaries
    4. Extracting error information
    5. Falling back to raw line storage for unparseable content

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Stop/done markers (end of message)
    - Iterator exhaustion (final flush via ``_flush_all_accumulators()``)

    Short content (below threshold) that doesn't end with ``\\n\\n`` is treated
    as a streaming delta and accumulated. Content at or above the threshold,
    or ending with ``\\n\\n``, is emitted immediately.
    """

    _STOP_TYPES: frozenset[str] = frozenset({"stop", "done", "complete", "finish", "end"})

    def __init__(self) -> None:
        self._text_accumulator: _TextAccumulator | None = None

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse generic streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from agent stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed: object = json.loads(stripped, strict=False)
            except json.JSONDecodeError:
                # Not JSON, treat as raw text - flush any pending accumulator first
                yield from self._flush_accumulator()
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            if not isinstance(parsed, dict):
                # Not a dict JSON object, treat as raw text - flush any pending accumulator first
                yield from self._flush_accumulator()
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            obj = cast("dict[str, object]", parsed)

            # Suppress lifecycle-only events that carry no user payload.
            type_val = str(obj.get("type", "")).lower()
            if type_val in _LIFECYCLE_EVENT_TYPES:
                continue

            # Check for stop/done markers first
            if self._is_stop(obj):
                yield from self._flush_accumulator()
                yield AgentOutputLine(type="stop", raw=stripped)
                continue

            # Check for error indicators
            if self._is_error(obj):
                # Flush pending text before emitting error
                yield from self._flush_accumulator()
                error_msg = self._extract_error(obj)
                yield AgentOutputLine(
                    type="error",
                    content=error_msg,
                    raw=stripped,
                    metadata=obj,
                )
                continue

            # Look for text content in common fields
            content = self._extract_content(obj)
            if content:
                yield from self._process_content(content, stripped)
                continue

            # If no content was extracted but we have valid JSON, store metadata
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="unknown", raw=stripped, metadata=obj)

        # Final flush: if iterator exhausted with pending accumulators, flush them all
        yield from self._flush_all_accumulators()

    def _is_short_content(self, content: str) -> bool:
        """Return True if content appears to be a short streaming delta.

        Content shorter than the threshold that doesn't end with a paragraph
        boundary is treated as a potential streaming delta.
        """
        if len(content) >= _SHORT_CONTENT_THRESHOLD:
            return False
        return not content.endswith("\n\n")

    def _process_content(self, content: str, raw: str) -> Iterator[AgentOutputLine]:
        """Process text content with delta accumulation and paragraph-boundary flush.

        Args:
            content: Extracted text content.
            raw: Raw line for tracking.

        Yields:
            AgentOutputLine instances, possibly flushing accumulated content.
        """
        if not content:
            return

        # If content ends with \n\n (paragraph boundary), flush and emit immediately
        if content.endswith("\n\n"):
            yield from self._flush_accumulator()
            emit_content = content[:-2]
            if emit_content:
                yield AgentOutputLine(type="text", content=emit_content, raw=raw)
            return

        # Short content without paragraph boundary -> treat as streaming delta
        if self._is_short_content(content):
            if self._text_accumulator is None:
                self._text_accumulator = _TextAccumulator()

            acc = self._text_accumulator
            acc.buffer += content
            acc.raw_lines.append(raw)

            # Check for \n\n in accumulated buffer (paragraph boundary reached
            # through incremental accumulation)
            if "\n\n" in acc.buffer:
                parts = acc.buffer.split("\n\n", 1)
                flushed_content = parts[0]
                remaining = parts[1]

                if flushed_content:
                    raw_parts = acc.raw_lines[: len(acc.raw_lines) - 1]
                    flushed_raw = "\n".join(raw_parts) if raw_parts else ""
                    yield AgentOutputLine(type="text", content=flushed_content, raw=flushed_raw)

                acc.buffer = remaining
                acc.raw_lines = [raw] if remaining else []
            return

        # Long content or content with sentence-ending punctuation -> standalone
        # Flush any pending accumulator first, then emit immediately
        yield from self._flush_accumulator()
        yield AgentOutputLine(type="text", content=content, raw=raw)

    def _flush_accumulator(self) -> Iterator[AgentOutputLine]:
        """Flush the single text accumulator and remove it."""
        if self._text_accumulator is None:
            return

        acc = self._text_accumulator
        self._text_accumulator = None

        if acc.buffer:
            raw_joined = "\n".join(acc.raw_lines) if acc.raw_lines else ""
            yield AgentOutputLine(type="text", content=acc.buffer, raw=raw_joined)

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on stop or iterator exhaustion."""
        yield from self._flush_accumulator()

    def _extract_content(self, obj: dict[str, object]) -> str:
        """Extract text content from JSON object.

        Args:
            obj: Parsed JSON object.

        Returns:
            Extracted text content or empty string.
        """
        # Check common content fields in order of preference
        for field_name in ("content", "text", "message", "output", "response", "result"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                # Sometimes content is nested
                nested = value.get("text") or value.get("content")
                if isinstance(nested, str) and nested:
                    return nested
        return ""

    def _is_error(self, obj: dict[str, object]) -> bool:
        """Check if object represents an error.

        Args:
            obj: Parsed JSON object.

        Returns:
            True if object appears to be an error.
        """
        type_val = str(obj.get("type", "")).lower()
        return "error" in type_val or bool(obj.get("error"))

    def _extract_error(self, obj: dict[str, object]) -> str:
        """Extract error message from object.

        Args:
            obj: Parsed JSON object.

        Returns:
            Error message string.
        """
        error = obj.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return str(error.get("message", error.get("type", "unknown error")))
        return str(obj.get("message", obj.get("msg", "unknown error")))

    def _is_stop(self, obj: dict[str, object]) -> bool:
        """Check if object represents end of output.

        Args:
            obj: Parsed JSON object.

        Returns:
            True if object represents end of stream.
        """
        type_val = str(obj.get("type", "")).lower()
        return type_val in self._STOP_TYPES


__all__ = ["GenericParser"]
