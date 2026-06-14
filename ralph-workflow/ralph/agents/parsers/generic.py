"""Generic NDJSON parser for other agents.

This parser handles NDJSON output from agents that don't have
a dedicated parser. It attempts to extract text content and
error information from common NDJSON formats, with robust delta
accumulation for streaming text responses.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from ralph.display.vt_normalizer import normalize_vt_text

from ._event_classification import is_lifecycle_event
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator


# Threshold: content shorter than this without paragraph boundary
# is treated as a streaming delta and accumulated.
# Content at or above this threshold is treated as a standalone message.
_SHORT_CONTENT_THRESHOLD = 200

_PLAIN_TOOL_PREFIX = "[plain] tool:"

# Plain-text tool-call prefixes common in headless agent stdout. The captured
# AGY transcript at tmp/agy-live-transcript.txt did not contain these markers
# for the current binary, but detecting them keeps the generic parser useful
# for agents that emit tool calls as plain text.
_AGY_TOOL_USE_PATTERNS = (
    re.compile(r"^(?:Calling tool|Using tool|Tool call):\s*(\S+)", re.IGNORECASE),
    re.compile(r"^(?:rag_tap|Read|Write|Edit|Glob|Grep|Bash|LS)\s*\(", re.IGNORECASE),
)
_AGY_TOOL_RESULT_PATTERN = re.compile(
    r"^(?:Tool result|Tool output|Result of):\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)


def _classify_plaintext_tool_line(stripped: str) -> tuple[str, str] | None:
    """Return (type, content) for plain-text tool announcements, or None."""
    for pattern in _AGY_TOOL_USE_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            tool_name = match.group(1) if match.lastindex else stripped
            return ("tool_use", tool_name)
    result_match = _AGY_TOOL_RESULT_PATTERN.search(stripped)
    if result_match is not None:
        return ("tool_result", result_match.group(1))
    if stripped.startswith(_PLAIN_TOOL_PREFIX):
        tool_name = stripped[len(_PLAIN_TOOL_PREFIX) :].strip()
        return ("tool_use", tool_name)
    return None


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

    Field priority for content extraction:
    1. content, text, message, output, response, result (type='text')
    2. thought, reasoning (type='thinking') — only when no higher-priority field matches
    """

    _STOP_TYPES: frozenset[str] = frozenset({"stop", "done", "complete", "finish", "end"})

    def __init__(self) -> None:
        self._text_accumulator: TextAccumulator | None = None

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse generic streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from agent stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        for raw_line in lines:
            # Strip ANSI/VT decorations BEFORE classification. Nanocoder (a TUI)
            # piped without a PTY emits colour codes around its output, e.g.
            # ``\x1b[36m[plain] tool: mcp__ralph__list_directory\x1b[0m``. Without
            # normalization the strict ``startswith("[plain] tool:")`` check below
            # fails and tool calls are misclassified as raw content — so MCP tool
            # activity never renders live, even though the failure classifier
            # (substring match) still detects it. Normalizing here keeps both in
            # agreement.
            line = normalize_vt_text(raw_line)
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed: object = json.loads(stripped, strict=False)
            except json.JSONDecodeError:
                # Not JSON, treat as raw text - flush any pending accumulator first
                yield from self._flush_accumulator()
                classification = _classify_plaintext_tool_line(stripped)
                if classification is not None:
                    line_type, content = classification
                    yield AgentOutputLine(type=line_type, content=content, raw=stripped)
                    continue
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
            if is_lifecycle_event(type_val):
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

            # Look for text content in high-priority fields
            content = self._extract_content(obj)
            if content:
                yield from self._process_content(content, stripped)
                continue

            # thought/reasoning fields map to 'thinking' — lower priority than text fields
            thinking = self._extract_thinking_content(obj)
            if thinking:
                yield from self._flush_accumulator()
                yield AgentOutputLine(type="thinking", content=thinking, raw=stripped, metadata=obj)
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
                self._text_accumulator = TextAccumulator()
            yield from self._text_accumulator.accumulate(
                content, raw, kind="text", keep_current_when_empty=False
            )
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
        yield from acc.flush(kind="text")

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on stop or iterator exhaustion."""
        yield from self._flush_accumulator()

    def _extract_content(self, obj: dict[str, object]) -> str:
        """Extract text content from JSON object using high-priority fields.

        Args:
            obj: Parsed JSON object.

        Returns:
            Extracted text content or empty string.
        """
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

    def _extract_thinking_content(self, obj: dict[str, object]) -> str:
        """Extract reasoning/thought text from low-priority fields.

        Only called when no high-priority content field matched.

        Args:
            obj: Parsed JSON object.

        Returns:
            Thinking content string, or empty string if not present.
        """
        for field_name in ("thought", "reasoning"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
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
