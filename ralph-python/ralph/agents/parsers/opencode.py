"""Parser for OpenCode's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class OpenCodeParser:
    """Parser for OpenCode's NDJSON streaming output.

    OpenCode uses a streaming format similar to Claude but with
    different event types. This parser handles OpenCode-specific events.
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse OpenCode streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from OpenCode stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                obj: dict[str, object] = json.loads(stripped)
            except json.JSONDecodeError:
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            yield from self._parse_object(obj, stripped)

    def _parse_object(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        """Parse a JSON object into AgentOutputLine instances.

        Args:
            obj: Parsed JSON object.
            stripped: Original stripped line.

        Yields:
            AgentOutputLine instances.
        """
        event_type = str(obj.get("type", "unknown"))

        if event_type == "stream":
            # OpenCode streaming output
            content = obj.get("content", "")
            if isinstance(content, str) and content:
                yield AgentOutputLine(type="text", content=content, raw=stripped)

        elif event_type == "done":
            yield AgentOutputLine(type="stop", raw=stripped)

        elif event_type == "error":
            error_msg = str(obj.get("message", "unknown error"))
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
            )

        elif event_type == "tool_use":
            tool_name = str(obj.get("tool", "unknown"))
            yield AgentOutputLine(
                type="tool_use",
                content=tool_name,
                raw=stripped,
                metadata=obj,
            )

        elif event_type == "tool_result":
            result = str(obj.get("result", ""))
            yield AgentOutputLine(
                type="tool_result",
                content=result,
                raw=stripped,
                metadata=obj,
            )

        else:
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)
