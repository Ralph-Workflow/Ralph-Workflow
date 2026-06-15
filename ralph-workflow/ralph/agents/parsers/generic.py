"""Generic NDJSON parser for other agents.

This parser handles NDJSON output from agents that don't have
a dedicated parser. It attempts to extract text content and
error information from common NDJSON formats, with robust delta
accumulation for streaming text responses.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.display.vt_normalizer import normalize_vt_text

from ._event_classification import is_lifecycle_event
from ._template import ParserTemplateBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator


_SHORT_CONTENT_THRESHOLD = 200

_PLAIN_TOOL_PREFIX = "[plain] tool:"


def _classify_plaintext_tool_line(stripped: str) -> tuple[str, str] | None:
    if stripped.startswith(_PLAIN_TOOL_PREFIX):
        tool_name = stripped[len(_PLAIN_TOOL_PREFIX) :].strip()
        return ("tool_use", tool_name)
    return None


class GenericParser(ParserTemplateBase):
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
    - Iterator exhaustion (final flush via ``flush_accumulators()``)

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

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        line = normalize_vt_text(line)
        stripped = line.strip()
        if not stripped:
            return

        try:
            parsed: object = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            yield from self._flush_accumulator()
            classification = _classify_plaintext_tool_line(stripped)
            if classification is not None:
                line_type, content = classification
                yield AgentOutputLine(type=line_type, content=content, raw=stripped)
                return
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return

        if not isinstance(parsed, dict):
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return

        obj = cast("dict[str, object]", parsed)
        yield from self._classify_parsed_json(obj, stripped)

    def _classify_parsed_json(
        self, obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        type_val = str(obj.get("type", "")).lower()
        if is_lifecycle_event(type_val):
            return

        if self._is_stop(obj):
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        if self._is_error(obj):
            yield from self._flush_accumulator()
            error_msg = self._extract_error(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
            )
            return

        content = self._extract_content(obj)
        if content:
            yield from self._process_content(content, stripped)
            return

        thinking = self._extract_thinking_content(obj)
        if thinking:
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="thinking", content=thinking, raw=stripped, metadata=obj)
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="unknown", raw=stripped, metadata=obj)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._flush_accumulator()

    def _is_short_content(self, content: str) -> bool:
        if len(content) >= _SHORT_CONTENT_THRESHOLD:
            return False
        return not content.endswith("\n\n")

    def _process_content(self, content: str, raw: str) -> Iterator[AgentOutputLine]:
        if not content:
            return

        if content.endswith("\n\n"):
            yield from self._flush_accumulator()
            emit_content = content[:-2]
            if emit_content:
                yield AgentOutputLine(type="text", content=emit_content, raw=raw)
            return

        if self._is_short_content(content):
            if self._text_accumulator is None:
                self._text_accumulator = TextAccumulator()
            yield from self._text_accumulator.accumulate(
                content, raw, kind="text", keep_current_when_empty=False
            )
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="text", content=content, raw=raw)

    def _flush_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")

    def _extract_content(self, obj: dict[str, object]) -> str:
        for field_name in ("content", "text", "message", "output", "response", "result"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get("text") or value.get("content")
                if isinstance(nested, str) and nested:
                    return nested
        return ""

    def _extract_thinking_content(self, obj: dict[str, object]) -> str:
        for field_name in ("thought", "reasoning"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
        return ""

    def _is_error(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return "error" in type_val or bool(obj.get("error"))

    def _extract_error(self, obj: dict[str, object]) -> str:
        error = obj.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return str(error.get("message", error.get("type", "unknown error")))
        return str(obj.get("message", obj.get("msg", "unknown error")))

    def _is_stop(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return type_val in self._STOP_TYPES
