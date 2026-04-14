"""Parser for Gemini's SSE+JSON streaming format.

Gemini emits Server-Sent Events (SSE) where each event contains a JSON payload.
This parser handles the SSE format and normalizes Gemini output to AgentOutputLine
instances.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.parsers.base import AgentOutputLine

JsonValue = object
JsonDict = dict[str, JsonValue]

if TYPE_CHECKING:
    from collections.abc import Iterator


class GeminiParser:
    """Parser for Gemini's SSE+JSON streaming output.

    Gemini uses SSE with data: lines containing JSON payloads.
    Each payload has a "type" field indicating what kind of content it carries.
    This parser normalizes these to AgentOutputLine instances.
    """

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
                obj = cast("JsonDict", json.loads(stripped))
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            yield from self._parse_object(obj, stripped)

    def _parse_object(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse a JSON object into AgentOutputLine instances.

        Args:
            obj: Parsed JSON object.
            stripped: Original stripped line.

        Yields:
            AgentOutputLine instances.
        """
        event_type = str(obj.get("type", obj.get("event", "unknown")))

        if event_type in ("text", "content"):
            yield from self._parse_text_content(obj, stripped)
        elif event_type in ("block", "content_block"):
            yield from self._parse_block(obj, stripped)
        elif event_type in ("tool_call", "tool_use"):
            yield from self._parse_tool_call(obj, stripped)
        elif event_type in ("tool_result", "function_call"):
            yield from self._parse_tool_result(obj, stripped)
        elif event_type in ("done", "stop", "message_end"):
            yield AgentOutputLine(type="stop", raw=stripped)
        elif event_type in ("error", "error_details"):
            yield from self._parse_error(obj, stripped)
        elif event_type in ("candidate", "prompt_feedback"):
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)
        elif event_type in ("message", "server_message"):
            yield from self._parse_message(obj, stripped)
        else:
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _parse_text_content(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse text/content event."""
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", "") or obj.get("text", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_block(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        """Parse block/content_block event."""
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

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
