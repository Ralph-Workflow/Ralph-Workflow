"""Parser for Codex's NDJSON streaming format.

Codex emits a flat NDJSON stream similar to Claude but with different
event type names and payload shapes. This parser normalizes Codex output
to AgentOutputLine instances.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any


class CodexParser:
    """Parser for Codex's NDJSON streaming output.

    Codex streams output as NDJSON with text, tool_use, tool_result, and
    done event types. This parser normalizes these to AgentOutputLine instances.
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Codex streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from Codex stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
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
        event_type = str(obj.get("type", "unknown"))

        if event_type in ("text", "content"):
            yield from self._parse_text_content(obj, stripped)
        elif event_type == "text_delta":
            yield from self._parse_text_delta(obj, stripped)
        elif event_type == "tool_use":
            yield from self._parse_tool_use(obj, stripped)
        elif event_type in ("tool_result", "tool_result_delta"):
            yield from self._parse_tool_result(obj, stripped)
        elif event_type in ("done", "stop"):
            yield AgentOutputLine(type="stop", raw=stripped)
        elif event_type in ("error", "error_delta"):
            yield from self._parse_error(obj, stripped)
        elif event_type == "message_start":
            yield AgentOutputLine(type="message_start", raw=stripped, metadata=obj)
        elif event_type == "message_stop":
            yield AgentOutputLine(type="stop", raw=stripped)
        elif event_type == "assistant":
            yield from self._parse_assistant(obj, stripped)
        else:
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _parse_text_content(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse text/content event."""
        content = str(obj.get("content", "") or obj.get("text", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_text_delta(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse text_delta event."""
        content = str(obj.get("delta", {}).get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_tool_use(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_use event."""
        tool_name = str(obj.get("tool", obj.get("name", "unknown")))
        tool_input = obj.get("input", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "input": tool_input},
        )

    def _parse_tool_result(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse tool_result/tool_result_delta event."""
        result = str(obj.get("result", obj.get("content", "")))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse error/error_delta event."""
        error_msg = str(
            obj.get("error", {}).get("message", "")
            if isinstance(obj.get("error"), dict)
            else obj.get("error", "unknown error")
        )
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_assistant(self, obj: dict[str, Any], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse assistant event."""
        content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)
        yield AgentOutputLine(type="assistant", raw=stripped, metadata=obj)
