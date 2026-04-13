"""Parser for Gemini's SSE+JSON streaming format.

Gemini emits Server-Sent Events (SSE) where each event contains a JSON payload.
This parser handles the SSE format and normalizes Gemini output to AgentOutputLine
instances.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any


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
                obj: dict[str, Any] = json.loads(stripped)
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            yield from self._parse_object(obj, stripped)

    def _parse_object(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
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

    def _parse_text_content(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse text/content event."""
        content = str(
            obj.get("content", "")
            or obj.get("text", "")
            or obj.get("parts", [{}])[0].get("text", "")
            if isinstance(obj.get("parts"), list)
            else ""
        )
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_block(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse block/content_block event."""
        parts = obj.get("parts", [])
        content = obj.get("content", "") or (parts[0].get("text", "") if parts else "")
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_tool_call(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_call/tool_use event."""
        func_call = obj.get("function_call", {})
        tool_name = str(
            obj.get("name", "")
            or obj.get("tool", "")
            or (func_call.get("name", "") if isinstance(func_call, dict) else "")
        )
        args_str = str(
            obj.get("args", "")
            or obj.get("arguments", "")
            or (json.dumps(func_call.get("args", {})) if isinstance(func_call, dict) else "")
        )
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args_str},
        )

    def _parse_tool_result(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_result/function_call event."""
        result = str(obj.get("response", "") or obj.get("result", "") or obj.get("content", ""))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse error/error_details event."""
        error_msg = str(
            obj.get("error", {}).get("message", "")
            if isinstance(obj.get("error"), dict)
            else obj.get("error", "unknown error")
        )
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_message(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse message/server_message event."""
        parts = obj.get("parts", [])
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    text = str(part.get("text", ""))
                    if text:
                        yield AgentOutputLine(type="text", content=text, raw=stripped)
                    func = part.get("function_call", {})
                    if isinstance(func, dict) and func:
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
