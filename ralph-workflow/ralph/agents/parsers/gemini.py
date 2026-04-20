"""Parser for Gemini's SSE+JSON streaming format.

Gemini emits Server-Sent Events (SSE) where each event contains a JSON payload.
This parser handles the SSE format and normalizes Gemini output to AgentOutputLine
instances with robust delta accumulation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

from ralph.agents.parsers.base import AgentOutputLine

JsonValue = object
JsonDict = dict[str, JsonValue]

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _TextAccumulator:
    buffer: str = ""
    raw_lines: list[str] = field(default_factory=list)


class GeminiParser:
    """Parser for Gemini's SSE+JSON streaming output with robust delta accumulation.

    Gemini uses SSE with data: lines containing JSON payloads.
    Each payload has a "type" field indicating what kind of content it carries.
    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``done`` / ``stop`` / ``message_end`` (end of message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``_flush_all_accumulators()``)
    """

    _STOP_EVENT_TYPES: Final[frozenset[str]] = frozenset({"done", "stop", "message_end"})

    def __init__(self) -> None:
        # Single accumulator for gemini's sequential text streaming
        self._text_accumulator: _TextAccumulator | None = None

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Gemini SSE streaming lines.

        Args:
            lines: Iterator of raw lines from Gemini stdout (SSE format).

        Yields:
            Normalized AgentOutputLine instances.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Handle SSE format: "data: <json>"
            if stripped.startswith("data:"):
                stripped = stripped[5:].strip()

            if not stripped or stripped == "[DONE]":
                continue

            try:
                obj = cast("JsonDict", json.loads(stripped, strict=False))
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            yield from self._parse_object(obj, stripped)

        # Final flush: if iterator exhausted with pending accumulators, flush them all
        yield from self._flush_all_accumulators()

    def _parse_object(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse a JSON object into AgentOutputLine instances.

        Args:
            obj: Parsed JSON object.
            stripped: Original stripped line.

        Yields:
            AgentOutputLine instances.
        """
        event_type = str(obj.get("type", obj.get("event", "unknown")))

        # Handle stop events - flush accumulators first
        if event_type in self._STOP_EVENT_TYPES:
            yield from self._flush_all_accumulators()
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        if event_type in ("text", "content"):
            yield from self._parse_text_content(obj, stripped)
        elif event_type in ("block", "content_block"):
            yield from self._parse_block(obj, stripped)
        elif event_type in ("tool_call", "tool_use"):
            yield from self._parse_tool_call(obj, stripped)
        elif event_type in ("tool_result", "function_call"):
            yield from self._parse_tool_result(obj, stripped)
        elif event_type in ("error", "error_details"):
            yield from self._parse_error(obj, stripped)
        elif event_type in ("candidate", "prompt_feedback"):
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)
        elif event_type in ("message", "server_message"):
            yield from self._parse_message(obj, stripped)
        else:
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _parse_text_content(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse text/content event with delta accumulation."""
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", "") or obj.get("text", ""))

        if not content:
            return

        # Accumulate text deltas
        if self._text_accumulator is None:
            self._text_accumulator = _TextAccumulator()

        acc = self._text_accumulator
        acc.buffer += content
        acc.raw_lines.append(stripped)

        # Check for paragraph boundary - flush on \n\n
        if "\n\n" in acc.buffer:
            parts = acc.buffer.split("\n\n", 1)
            remaining = parts[1]
            flushed_content = parts[0]
            # Build raw from all but the last raw line (the \n\n line itself)
            raw_parts = acc.raw_lines[: len(acc.raw_lines) - 1]
            flushed_raw = "\n".join(raw_parts) if raw_parts else ""
            yield AgentOutputLine(type="text", content=flushed_content, raw=flushed_raw)
            # Reset for remaining content
            acc.buffer = remaining
            # Always keep current line after \n\n for proper raw tracking
            acc.raw_lines = [stripped]

    def _parse_block(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse block/content_block event with delta accumulation."""
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", ""))

        if not content:
            return

        # Accumulate text deltas
        if self._text_accumulator is None:
            self._text_accumulator = _TextAccumulator()

        acc = self._text_accumulator
        acc.buffer += content
        acc.raw_lines.append(stripped)

        # Check for paragraph boundary - flush on \n\n
        if "\n\n" in acc.buffer:
            parts = acc.buffer.split("\n\n", 1)
            remaining = parts[1]
            flushed_content = parts[0]
            # Build raw from all but the last raw line (the \n\n line itself)
            raw_parts = acc.raw_lines[: len(acc.raw_lines) - 1]
            flushed_raw = "\n".join(raw_parts) if raw_parts else ""
            yield AgentOutputLine(type="text", content=flushed_content, raw=flushed_raw)
            # Reset for remaining content
            acc.buffer = remaining
            # Always keep current line after \n\n for proper raw tracking
            acc.raw_lines = [stripped]

    def _parse_tool_call(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_call/tool_use event."""
        function_call_obj: JsonValue | None = obj.get("function_call")
        func_call: JsonDict | None = (
            cast("JsonDict", function_call_obj) if isinstance(function_call_obj, dict) else None
        )
        tool_name = str(
            obj.get("name", "")
            or obj.get("tool", "")
            or (func_call.get("name", "") if func_call is not None else "")
        )
        args_source = obj.get("args") or obj.get("arguments")
        args_str = ""
        if args_source:
            args_str = str(args_source)
        elif func_call is not None:
            func_args = func_call.get("args")
            if isinstance(func_args, dict):
                args_str = json.dumps(cast("JsonDict", func_args))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args_str},
        )

    def _parse_tool_result(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_result/function_call event."""
        result = str(obj.get("response", "") or obj.get("result", "") or obj.get("content", ""))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse error/error_details event."""
        error_val = obj.get("error")
        if isinstance(error_val, dict):
            error_msg = str(cast("JsonDict", error_val).get("message", ""))
        else:
            error_msg = str(error_val) if error_val else "unknown error"
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_message(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse message/server_message event."""
        parts = obj.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    part_dict = cast("JsonDict", part)
                    text = str(part_dict.get("text", ""))
                    if text:
                        yield AgentOutputLine(type="text", content=text, raw=stripped)
                    function_call_obj = part_dict.get("function_call")
                    func: JsonDict | None = (
                        cast("JsonDict", function_call_obj)
                        if isinstance(function_call_obj, dict)
                        else None
                    )
                    if func:
                        tool_name = str(func.get("name", ""))
                        args_str = str(func.get("args", ""))
                        yield AgentOutputLine(
                            type="tool_use",
                            content=tool_name,
                            raw=stripped,
                            metadata={"tool": tool_name, "args": args_str},
                        )
        content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _extract_first_part_text(self, obj: JsonDict) -> str:
        """Return the text for the first part entry, if present."""
        parts_val: JsonValue | None = obj.get("parts")
        if isinstance(parts_val, list) and parts_val:
            first_part = parts_val[0]
            if isinstance(first_part, dict):
                part_dict = cast("JsonDict", first_part)
                return str(part_dict.get("text", ""))
        return ""

    def _flush_accumulator(self) -> Iterator[AgentOutputLine]:
        """Flush the single text accumulator and clear it."""
        if self._text_accumulator is None:
            return

        acc = self._text_accumulator
        self._text_accumulator = None
        buffer = acc.buffer
        raw_lines = acc.raw_lines

        if buffer:
            raw_joined = "\n".join(raw_lines) if raw_lines else ""
            yield AgentOutputLine(type="text", content=buffer, raw=raw_joined)

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on stop or iterator exhaustion."""
        yield from self._flush_accumulator()
