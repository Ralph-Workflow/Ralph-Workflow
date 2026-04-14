"""Parser for OpenCode's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class OpenCodeParser:
    """Parser for OpenCode's NDJSON streaming output."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse OpenCode streaming NDJSON lines."""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                parsed: object = json.loads(stripped)
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            if not isinstance(parsed, dict):
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            obj = cast("dict[str, object]", parsed)
            yield from self._parse_object(obj, stripped)

    def _parse_object(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse a JSON object into AgentOutputLine instances."""
        event_type = str(obj.get("type", "unknown"))
        part_obj = obj.get("part")
        part: dict[str, object] = {}
        if isinstance(part_obj, dict):
            part = cast("dict[str, object]", part_obj)

        handler_map = {
            "stream": self._parse_stream,
            "text": self._parse_text,
            "step_start": self._parse_step_start,
            "done": self._parse_done,
            "step_finish": self._parse_done,
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
        if isinstance(content, str) and content:
            yield AgentOutputLine(type="text", content=content, raw=raw)

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

    def _parse_step_start(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="message_start", raw=raw, metadata=obj)

    def _parse_done(
        self,
        _obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="stop", raw=raw)

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

        if not isinstance(state_obj, dict):
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=obj)
            return

        status = str(state_obj.get("status", ""))
        if status == "completed":
            output = state_obj.get("output", "")
            yield AgentOutputLine(type="tool_result", content=str(output), raw=raw, metadata=obj)
            return

        if status == "error":
            err = str(state_obj.get("error", "tool error"))
            yield AgentOutputLine(type="error", content=err, raw=raw, metadata=obj)
            return

        yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=obj)

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        if "result" in obj:
            result = str(obj.get("result", ""))
        else:
            state_obj = part.get("state")
            result = str(state_obj.get("output", "")) if isinstance(state_obj, dict) else ""
        yield AgentOutputLine(type="tool_result", content=result, raw=raw, metadata=obj)
