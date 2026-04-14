"""Parser for Codex's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class CodexParser:
    """Parser for Codex's NDJSON streaming output."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Codex streaming NDJSON lines."""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("data:"):
                stripped = stripped.removeprefix("data:").strip()
            if not stripped:
                continue

            if stripped == "[DONE]":
                yield AgentOutputLine(type="stop", raw=stripped)
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

        handler_map = {
            "text": self._parse_text_content,
            "content": self._parse_text_content,
            "text_delta": self._parse_text_delta,
            "response.output_text": self._parse_text_content,
            "response.output_text.delta": self._parse_text_delta,
            "tool_use": self._parse_tool_use,
            "tool_result": self._parse_tool_result,
            "tool_result_delta": self._parse_tool_result,
            "error": self._parse_error,
            "error_delta": self._parse_error,
            "assistant": self._parse_assistant,
            "item.started": self._parse_item_event,
            "item.completed": self._parse_item_event,
            "result": self._parse_result,
            "turn.failed": self._parse_turn_failed,
        }

        handler = handler_map.get(event_type)
        if handler:
            yield from handler(obj, stripped)
            return

        if event_type in {"thread.started", "turn.started", "message_start"}:
            yield AgentOutputLine(type="message_start", raw=stripped, metadata=obj)
            return

        if event_type in {"turn.completed", "message_stop", "done", "stop", "response.completed"}:
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _parse_text_content(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(obj.get("content", "") or obj.get("text", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_text_delta(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        delta_val = obj.get("delta")
        if isinstance(delta_val, dict):
            delta_obj = cast("dict[str, object]", delta_val)
            content_val = delta_obj.get("content") or delta_obj.get("text")
            content = str(content_val or "")
        elif isinstance(delta_val, str):
            content = delta_val
        else:
            content = ""

        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_tool_use(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        tool_name = str(obj.get("tool", obj.get("name", "unknown")))
        tool_input = obj.get("input", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "input": tool_input},
        )

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        result = str(obj.get("result", obj.get("content", obj.get("output", ""))))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        error_val = obj.get("error")
        if isinstance(error_val, dict):
            error_msg = str(cast("dict[str, object]", error_val).get("message", ""))
        else:
            error_msg = str(obj.get("message") or error_val or "unknown error")
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_turn_failed(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_message = str(obj.get("error", "turn failed"))
        yield AgentOutputLine(type="error", content=error_message, raw=stripped, metadata=obj)

    def _parse_assistant(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)
        yield AgentOutputLine(type="assistant", raw=stripped, metadata=obj)

    def _parse_result(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        result = str(obj.get("result", ""))
        if result:
            yield AgentOutputLine(type="text", content=result, raw=stripped, metadata=obj)

    def _parse_item_event(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        item_obj = obj.get("item")
        if not isinstance(item_obj, dict):
            yield AgentOutputLine(type=str(obj.get("type", "item")), raw=stripped, metadata=obj)
            return

        item_type = str(item_obj.get("type", "unknown"))
        text = str(item_obj.get("text", ""))

        if item_type in {"agent_message", "reasoning"} and text:
            yield AgentOutputLine(type="text", content=text, raw=stripped, metadata=item_obj)
            return

        if item_type == "mcp_tool_call":
            tool_name = str(item_obj.get("tool", "unknown"))
            arguments: object = item_obj.get("arguments", {})
            yield AgentOutputLine(
                type="tool_use",
                content=tool_name,
                raw=stripped,
                metadata={"tool": tool_name, "input": arguments},
            )
            return

        if item_type == "command_execution":
            command = str(item_obj.get("command", ""))
            if command:
                yield AgentOutputLine(
                    type="tool_use",
                    content="bash",
                    raw=stripped,
                    metadata=item_obj,
                )
            else:
                yield AgentOutputLine(
                    type="item_command_execution",
                    raw=stripped,
                    metadata=item_obj,
                )
            return

        if item_type in {"mcp_tool_result", "tool_result", "mcp_result"}:
            tool_name = str(item_obj.get("tool", "unknown"))
            result_obj = item_obj.get("result", item_obj.get("output", item_obj.get("content", "")))
            content = result_obj if isinstance(result_obj, str) else ""
            yield AgentOutputLine(
                type="tool_result",
                content=content,
                raw=stripped,
                metadata={"tool": tool_name, "result": result_obj},
            )
            return

        yield AgentOutputLine(type=f"item_{item_type}", raw=stripped, metadata=item_obj)
