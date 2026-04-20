"""Parser for Claude's NDJSON streaming format."""

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


class ClaudeParser:
    """Parser for Claude's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``content_block_stop`` (end of a content block)
    - ``message_stop`` (end of the message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    """

    _LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
        {"message_start", "message_stop", "content_block_stop"}
    )

    def __init__(self) -> None:
        # Accumulator keyed by (message_id, content_block_index)
        self._text_accumulator: dict[tuple[str, int], _TextAccumulator] = {}
        self._current_message_id: str | None = None
        self._seen_content_blocks: set[tuple[str, int]] = set()

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse Claude streaming NDJSON lines."""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            prefixed_lines = self._parse_prefixed_transcript_line(stripped)
            if prefixed_lines is not None:
                yield from prefixed_lines
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

        # Final flush: if iterator exhausted with pending accumulators, flush them all
        yield from self._flush_all_accumulators()

    def _parse_top_level_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type == "message_start":
            self._record_message_start(obj)
            return
        if event_type == "message_stop":
            yield from self._flush_all_accumulators()
            self._current_message_id = None
            self._seen_content_blocks.clear()
            return
        if event_type == "content_block_stop":
            yield from self._flush_content_block(obj)
            return
        if event_type in self._LIFECYCLE_EVENT_TYPES:
            return

        yield from self._parse_non_lifecycle_object(event_type, obj, raw)

    def _record_message_start(self, obj: dict[str, object]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        msg_id = str(message.get("id", ""))
        if msg_id:
            self._current_message_id = msg_id

    def _flush_content_block(self, obj: dict[str, object]) -> Iterator[AgentOutputLine]:
        index = obj.get("index")
        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._text_accumulator:
                yield from self._flush_accumulator(key)

    def _parse_non_lifecycle_object(
        self,
        event_type: str,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        if event_type == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield from self._parse_stream_inner(event, raw)
            else:
                yield AgentOutputLine(type="stream_event", raw=raw, metadata=obj)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(obj, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(obj)
            yield from self._parse_content_block_start(obj, raw)
        elif event_type == "assistant":
            yield from self._parse_assistant_message(obj, raw)
        elif event_type == "result":
            yield from self._parse_result_event(obj, raw)
        elif event_type == "error":
            yield from self._parse_error_event(obj, raw)
        else:
            yield AgentOutputLine(type=event_type, raw=raw, metadata=obj)

    def _track_content_block_start(self, obj: dict[str, object]) -> None:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict) or self._current_message_id is None:
            return
        index = obj.get("index")
        if not isinstance(index, int):
            return
        block_type = str(content_block.get("type", ""))
        if block_type != "text":
            return
        key = (self._current_message_id, index)
        if key not in self._text_accumulator:
            self._text_accumulator[key] = _TextAccumulator()

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
            # Track content block start for text blocks
            self._track_content_block_start(event)
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

        delta_type = str(delta.get("type", "text_delta" if "text" in delta else ""))
        if delta_type != "text_delta":
            return

        text = str(delta.get("text", ""))
        if not text:
            return

        # Get block index for accumulator keying
        index = obj.get("index")
        block_key: tuple[str, int] | None = None

        if isinstance(index, int) and self._current_message_id is not None:
            block_key = (self._current_message_id, index)
            if block_key in self._text_accumulator:
                # Accumulate into existing block
                acc = self._text_accumulator[block_key]
                acc.buffer += text
                acc.raw_lines.append(raw)

                # Check for paragraph boundary - flush on \n\n
                if "\n\n" in acc.buffer:
                    parts = acc.buffer.split("\n\n", 1)
                    acc.buffer = parts[1]
                    yield AgentOutputLine(
                        type="text",
                        content=parts[0],
                        raw="\n".join(acc.raw_lines),
                    )
                    acc.raw_lines = [raw] if acc.buffer else []
                return

        # No active accumulator - yield immediately (fallback for non-indexed deltas)
        yield AgentOutputLine(type="text", content=text, raw=raw)

    def _flush_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        """Flush a single accumulator and remove it."""
        if key not in self._text_accumulator:
            return

        acc = self._text_accumulator.pop(key)
        buffer = acc.buffer
        raw_lines = acc.raw_lines

        if buffer:
            raw_joined = "\n".join(raw_lines) if raw_lines else ""
            yield AgentOutputLine(
                type="text",
                content=buffer,
                raw=raw_joined,
            )

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on message_stop or iterator exhaustion."""
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_accumulator(key)

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

        yield from self._parse_message_content(content, raw)

    def _parse_message_content(
        self,
        content: list[object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        for block in content:
            if not isinstance(block, dict):
                continue

            block_obj = cast("dict[str, object]", block)
            block_type = str(block_obj.get("type", ""))
            if block_type == "text":
                text = str(block_obj.get("text", ""))
                if text:
                    yield AgentOutputLine(type="text", content=text, raw=raw, metadata=block_obj)
                continue

            if block_type == "tool_use":
                tool_name = str(block_obj.get("name", "unknown"))
                yield AgentOutputLine(
                    type="tool_use", content=tool_name, raw=raw, metadata=block_obj
                )
                continue

            if block_type == "tool_result":
                tool_result = self._stringify_tool_content(block_obj.get("content", ""))
                yield AgentOutputLine(
                    type="tool_result",
                    content=tool_result,
                    raw=raw,
                    metadata=block_obj,
                )

    def _parse_prefixed_transcript_line(self, raw: str) -> list[AgentOutputLine] | None:
        if raw.startswith("[claude]:"):
            return []

        if raw.startswith("claude: "):
            return [AgentOutputLine(type="text", content=raw.removeprefix("claude: "), raw=raw)]

        if raw.startswith("claude tool: "):
            return self._parse_prefixed_tool_line(raw)

        if raw.startswith("claude message_delta:") or raw.startswith("claude system: status="):
            return []

        return self._parse_prefixed_message_line(raw)

    def _parse_prefixed_tool_line(self, raw: str) -> list[AgentOutputLine]:
        payload = raw.removeprefix("claude tool: ").strip()
        tool_name, has_details, detail_suffix = payload.partition(" (")
        metadata: dict[str, object] = {}
        if has_details and detail_suffix.endswith(")"):
            metadata["input"] = {"args": detail_suffix[:-1]}
        return [
            AgentOutputLine(
                type="tool_use",
                content=tool_name.strip() or "unknown",
                raw=raw,
                metadata=metadata,
            )
        ]

    def _parse_prefixed_message_line(self, raw: str) -> list[AgentOutputLine] | None:
        for role in ("user", "assistant"):
            prefix = f"claude {role}: message="
            if not raw.startswith(prefix):
                continue

            try:
                parsed: object = json.loads(raw.removeprefix(prefix))
            except json.JSONDecodeError:
                return None

            if not isinstance(parsed, dict):
                return None

            content = parsed.get("content")
            if not isinstance(content, list):
                return []

            return list(self._parse_message_content(content, raw))

        return None

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
