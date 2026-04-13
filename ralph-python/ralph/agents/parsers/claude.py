"""Parser for Claude's NDJSON streaming format."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class ClaudeParser:
    """Parser for Claude's NDJSON streaming output.

    Claude streams output as NDJSON with message and content_block_delta events.
    This parser normalizes these to AgentOutputLine instances.
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Claude streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from Claude stdout.

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
                # Non-JSON line, treat as raw text
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            event_type = str(obj.get("type", "unknown"))

            if event_type == "content_block_delta":
                delta = obj.get("delta", {})
                if isinstance(delta, dict):
                    text = str(delta.get("text", ""))
                    if text:
                        yield AgentOutputLine(type="text", content=text, raw=stripped)

            elif event_type == "message_start":
                yield AgentOutputLine(type="message_start", raw=stripped, metadata=obj)

            elif event_type == "message_stop":
                yield AgentOutputLine(type="stop", raw=stripped)

            elif event_type == "content_block_start":
                content_block: dict[str, object] = obj.get("content_block", {})  # type: ignore[assignment]
                block_type = (
                    content_block.get("type", "unknown")
                    if isinstance(content_block, dict)
                    else "unknown"
                )
                yield AgentOutputLine(
                    type=f"block_start_{block_type}",
                    raw=stripped,
                    metadata=obj,
                )

            elif event_type == "content_block_stop":
                yield AgentOutputLine(type="block_stop", raw=stripped)

            elif event_type == "error":
                error_obj: dict[str, object] = obj.get("error", {})  # type: ignore[assignment]
                error_type = (
                    error_obj.get("type", "unknown")
                    if isinstance(error_obj, dict)
                    else "unknown"
                )
                error_msg = str(error_type)
                yield AgentOutputLine(
                    type="error",
                    content=error_msg,
                    raw=stripped,
                    metadata=obj,
                )

            else:
                # Unknown event type, store as metadata
                yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)
