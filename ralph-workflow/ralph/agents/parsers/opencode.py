"""Parser for OpenCode's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, cast

from ._event_classification import is_lifecycle_event
from ._template import ParserTemplateBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message, stringify_text_blocks
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator


class OpenCodeParser(ParserTemplateBase):
    """Parser for OpenCode's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``step_finish`` / ``done`` (end of step/message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``flush_accumulators()``)
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"step_start", "step_finish", "done"})

    def __init__(self) -> None:
        self._accumulators: dict[str, TextAccumulator] = {}
        self._current_part_id: str | None = None
        self._stream_counter = 0

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        if not stripped:
            return

        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
            return

        obj = cast("dict[str, object]", json.loads(stripped, strict=False))
        yield from self._parse_object(obj, stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            yield from self._flush_accumulator(key)

    def _parse_object(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if is_lifecycle_event(event_type):
            return

        if event_type == "step_start":
            step_id = str(obj.get("id", ""))
            if step_id:
                self._current_part_id = step_id
            return
        if event_type == "step_finish":
            if self._current_part_id and self._current_part_id in self._accumulators:
                yield from self._flush_accumulator(self._current_part_id)
            self._current_part_id = None
            return
        if event_type == "done":
            yield from self.flush_accumulators()
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

        part_id = self._current_part_id
        if part_id is None:
            yield AgentOutputLine(type="text", content=content, raw=raw)
            return

        key = part_id
        if key not in self._accumulators:
            self._accumulators[key] = TextAccumulator()
        yield from self._accumulators[key].accumulate(
            content, raw, kind="text", keep_current_when_empty=True
        )

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
        error_msg = extract_error_message(obj)
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
                content=stringify_text_blocks(output),
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
            result = stringify_text_blocks(obj.get("result", ""))
        else:
            state_obj = part.get("state")
            result = (
                stringify_text_blocks(state_obj.get("output", ""))
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

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        if key not in self._accumulators:
            return
        acc = self._accumulators.pop(key)
        yield from acc.flush(kind="text")
