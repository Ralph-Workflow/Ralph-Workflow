"""Parser for OpenCode's NDJSON streaming format."""

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


# Structured JSON event types that carry only lifecycle metadata — suppress silently.
_LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "thread.started",
        "turn.started",
        "message_start",
        "heartbeat",
        "ping",
        "ready",
        "assistant",
        "user",
    }
)


class OpenCodeParser:
    """Parser for OpenCode's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``step_finish`` / ``done`` (end of step/message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``_flush_all_accumulators()``)
    """

    _STOP_EVENT_TYPES: Final[frozenset[str]] = frozenset({"step_start", "step_finish", "done"})

    def __init__(self) -> None:
        # Accumulator keyed by part id or synthetic stream key
        self._text_accumulator: dict[str, _TextAccumulator] = {}
        self._current_part_id: str | None = None
        self._stream_counter = 0

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse OpenCode streaming NDJSON lines."""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed: object = json.loads(stripped, strict=False)
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            if not isinstance(parsed, dict):
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            obj = cast("dict[str, object]", parsed)
            yield from self._parse_object(obj, stripped)

        # Final flush: if iterator exhausted with pending accumulators, flush them all
        yield from self._flush_all_accumulators()

    def _parse_object(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse a JSON object into AgentOutputLine instances."""
        event_type = str(obj.get("type", "unknown"))

        # Suppress lifecycle-only events that carry no user payload.
        if event_type in _LIFECYCLE_EVENT_TYPES:
            return

        # Handle lifecycle events
        if event_type == "step_start":
            # Track the step id for accumulator keying
            step_id = str(obj.get("id", ""))
            if step_id:
                self._current_part_id = step_id
            return
        if event_type == "step_finish":
            # Flush accumulators for this step
            if self._current_part_id and self._current_part_id in self._text_accumulator:
                yield from self._flush_accumulator(self._current_part_id)
            self._current_part_id = None
            return
        if event_type == "done":
            yield from self._flush_all_accumulators()
            self._current_part_id = None
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        part_obj = obj.get("part")
        part: dict[str, object] = {}
        if isinstance(part_obj, dict):
            part = cast("dict[str, object]", part_obj)

        handler_map = {
            "stream": self._parse_stream,
            "text": self._parse_text,
            "error": self._parse_error,
            "tool_use": self._parse_tool_use,
            "tool_result": self._parse_tool_result,
        }

        handler = handler_map.get(event_type)
        if handler:
            yield from handler(obj, part, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _parse_stream(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content = obj.get("content", "")
        if not isinstance(content, str) or not content:
            return

        # Use part id for accumulator keying if available
        part_id = self._current_part_id
        if part_id is None:
            # No active step context, yield immediately
            yield AgentOutputLine(type="text", content=content, raw=raw)
            return

        key = part_id
        if key not in self._text_accumulator:
            self._text_accumulator[key] = _TextAccumulator()

        acc = self._text_accumulator[key]
        acc.buffer += content
        acc.raw_lines.append(raw)

        # Check for paragraph boundary - flush on \n\n
        if "\n\n" in acc.buffer:
            parts = acc.buffer.split("\n\n", 1)
            remaining = parts[1]
            flushed_content = parts[0]
            # Only yield if there's actual content (skip empty flush at boundary)
            if flushed_content:
                # Build raw from all but the last raw line (the \n\n line itself)
                raw_parts = acc.raw_lines[: len(acc.raw_lines) - 1]
                flushed_raw = "\n".join(raw_parts) if raw_parts else ""
                yield AgentOutputLine(type="text", content=flushed_content, raw=flushed_raw)
            # Reset for remaining content
            acc.buffer = remaining
            # If there's remaining content, keep raw_lines starting with current raw
            # Always keep current line after \n\n for proper raw tracking
            acc.raw_lines = [raw]

    def _parse_text(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        part_text = part.get("text")
        if isinstance(part_text, str) and part_text:
            yield AgentOutputLine(type="text", content=part_text, raw=raw, metadata=obj)
            return

        content = obj.get("content", "")
        if isinstance(content, str) and content:
            yield AgentOutputLine(type="text", content=content, raw=raw, metadata=obj)
            return

        yield AgentOutputLine(type="text", raw=raw, metadata=obj)

    def _parse_error(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_obj = obj.get("error")
        if isinstance(error_obj, dict):
            error_msg = str(error_obj.get("message", error_obj.get("name", "unknown error")))
        else:
            error_msg = str(obj.get("message", "unknown error"))
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_tool_use(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(part.get("tool", obj.get("tool", "unknown")))
        state_obj = part.get("state")
        metadata = self._tool_metadata(obj, part)

        if not isinstance(state_obj, dict):
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)
            return

        status = str(state_obj.get("status", ""))
        if status == "completed":
            output = state_obj.get("output", "")
            yield AgentOutputLine(
                type="tool_result",
                content=self._stringify_output(output),
                raw=raw,
                metadata=metadata,
            )
            return

        if status == "error":
            err = str(state_obj.get("error", "tool error"))
            yield AgentOutputLine(type="error", content=err, raw=raw, metadata=metadata)
            return

        yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        metadata = self._tool_metadata(obj, part)
        if "result" in obj:
            result = self._stringify_output(obj.get("result", ""))
        else:
            state_obj = part.get("state")
            result = (
                self._stringify_output(state_obj.get("output", ""))
                if isinstance(state_obj, dict)
                else ""
            )
        yield AgentOutputLine(type="tool_result", content=result, raw=raw, metadata=metadata)

    def _tool_metadata(
        self,
        obj: dict[str, object],
        part: dict[str, object],
    ) -> dict[str, object]:
        metadata = dict(obj)
        tool_name = part.get("tool", obj.get("tool"))
        if isinstance(tool_name, str) and tool_name:
            metadata["tool"] = tool_name

        input_obj = part.get("input")
        if isinstance(input_obj, dict):
            metadata["input"] = input_obj
            return metadata

        state_obj = part.get("state")
        if isinstance(state_obj, dict):
            nested_input = state_obj.get("input")
            if isinstance(nested_input, dict):
                metadata["input"] = nested_input

        return metadata

    def _stringify_output(self, output: object) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            text_parts = [
                str(item.get("text", ""))
                for item in output
                if isinstance(item, dict) and "text" in item
            ]
            if text_parts:
                return "\n".join(part for part in text_parts if part)
        return str(output)

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        """Flush a single accumulator and remove it."""
        if key not in self._text_accumulator:
            return

        acc = self._text_accumulator.pop(key)
        buffer = acc.buffer
        raw_lines = acc.raw_lines

        if buffer:
            raw_joined = "\n".join(raw_lines) if raw_lines else ""
            yield AgentOutputLine(type="text", content=buffer, raw=raw_joined)

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on stop or iterator exhaustion."""
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_accumulator(key)
