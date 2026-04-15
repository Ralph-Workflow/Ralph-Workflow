"""Parser for Claude's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class ClaudeParser:
    """Parser for Claude's NDJSON streaming output."""

    _LIFECYCLE_EVENT_TYPES = frozenset({"message_start", "message_stop", "content_block_stop"})

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Claude streaming NDJSON lines."""
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
            yield from self._parse_top_level_object(obj, stripped)

    def _parse_top_level_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in self._LIFECYCLE_EVENT_TYPES:
            return

        if event_type == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield from self._parse_stream_inner(event, raw)
            else:
                yield AgentOutputLine(type="stream_event", raw=raw, metadata=obj)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(obj, raw)
        elif event_type == "assistant":
            yield from self._parse_assistant_message(obj, raw)
        elif event_type == "result":
            yield from self._parse_result_event(obj, raw)
        elif event_type == "content_block_start":
            yield from self._parse_content_block_start(obj, raw)
        elif event_type == "error":
            yield from self._parse_error_event(obj, raw)
        else:
            yield AgentOutputLine(type=event_type, raw=raw, metadata=obj)

    def _parse_stream_inner(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(event.get("type", "unknown"))

        if event_type == "content_block_delta":
            yield from self._parse_content_block_delta(event, raw)
            return

        if event_type == "content_block_start":
            yield from self._parse_stream_content_block_start(event, raw)
            return

        if event_type == "error":
            yield from self._parse_stream_error(event, raw)
            return

        if event_type in self._LIFECYCLE_EVENT_TYPES:
            return

        yield AgentOutputLine(type=event_type, raw=raw, metadata=event)

    def _parse_content_block_delta(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        delta = obj.get("delta")
        if not isinstance(delta, dict):
            return

        text = str(delta.get("text", ""))
        if text:
            yield AgentOutputLine(type="text", content=text, raw=raw)

    def _parse_result_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        subtype = str(obj.get("subtype", ""))
        if subtype == "error":
            error = str(obj.get("error", "unknown error"))
            yield AgentOutputLine(type="error", content=error, raw=raw, metadata=obj)
            return

        result = str(obj.get("result", ""))
        if result:
            yield AgentOutputLine(type="text", content=result, raw=raw, metadata=obj)

    def _parse_content_block_start(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict):
            return

        yield from self._parse_content_block(content_block, raw)

    def _parse_error_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_obj = obj.get("error")
        if isinstance(error_obj, dict):
            error_msg = str(error_obj.get("message", error_obj.get("type", "unknown error")))
        else:
            error_msg = "unknown"
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_stream_content_block_start(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = event.get("content_block")
        if not isinstance(content_block, dict):
            return

        yield from self._parse_content_block(content_block, raw)

    def _parse_stream_error(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error = event.get("error")
        if isinstance(error, dict):
            error_msg = str(error.get("message", error.get("code", "unknown error")))
        else:
            error_msg = "unknown error"
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=event)

    def _parse_assistant_message(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return

        content = message.get("content")
        if not isinstance(content, list):
            return

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type", ""))
            if block_type == "text":
                text = str(block.get("text", ""))
                if text:
                    yield AgentOutputLine(type="text", content=text, raw=raw, metadata=block)
                continue

            if block_type == "tool_use":
                tool_name = str(block.get("name", "unknown"))
                yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=block)
                continue

            if block_type == "tool_result":
                tool_result = self._stringify_tool_content(block.get("content", ""))
                yield AgentOutputLine(
                    type="tool_result",
                    content=tool_result,
                    raw=raw,
                    metadata=block,
                )

    def _parse_content_block(
        self,
        content_block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        block_type = str(content_block.get("type", "unknown"))

        if block_type == "text":
            text = str(content_block.get("text", ""))
            if text:
                yield AgentOutputLine(type="text", content=text, raw=raw, metadata=content_block)
            return

        if block_type == "tool_use":
            tool_name = str(content_block.get("name", "unknown"))
            yield AgentOutputLine(
                type="tool_use", content=tool_name, raw=raw, metadata=content_block
            )
            return

        if block_type == "tool_result":
            tool_result = self._stringify_tool_content(content_block.get("content", ""))
            yield AgentOutputLine(
                type="tool_result",
                content=tool_result,
                raw=raw,
                metadata=content_block,
            )

    def _stringify_tool_content(self, content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and str(item.get("type", "")) == "text"
            ]
            if text_parts:
                return "\n".join(part for part in text_parts if part)
        return str(content)
