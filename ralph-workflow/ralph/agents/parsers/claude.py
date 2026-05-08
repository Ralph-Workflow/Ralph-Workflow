"""Parser for Claude's NDJSON streaming format."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from ralph.agents.parsers.base import AgentOutputLine, TextAccumulator, stringify_text_blocks

if TYPE_CHECKING:
    from collections.abc import Iterator

# Matches "claude" or "claude/<model>" at line start, followed by space, colon, or end.
_CLAUDE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^claude(?:/[^:\s]+)?(?=[ :]|$)")

# Lifecycle markers emitted by Claude CLI that carry no user payload.
# Unknown free-text after "claude/<model>: " defaults to type='text' (safe default).
_LIFECYCLE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "message_delta",
        "user",
        "thinking",
        "assistant",
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
    }
)


class ClaudeParser:
    """Parser for Claude's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``content_block_stop`` (end of a content block)
    - ``message_stop`` (end of the message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)

    Thinking deltas (``thinking_delta``) are accumulated separately from text
    deltas and emitted as ``type="thinking"`` lines.
    """

    _LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
        {"message_start", "message_stop", "content_block_stop"}
    )

    def __init__(self) -> None:
        # Accumulators keyed by (message_id, content_block_index)
        self._text_accumulator: dict[tuple[str, int], TextAccumulator] = {}
        self._thinking_accumulator: dict[tuple[str, int], TextAccumulator] = {}
        self._fallback_accumulator: TextAccumulator | None = None
        self._fallback_thinking_accumulator: TextAccumulator | None = None
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
                parsed: object = json.loads(stripped, strict=False)
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

        lifecycle_result = self._handle_lifecycle_event(obj, event_type)
        if lifecycle_result is not None:
            yield from lifecycle_result
            return

        yield from self._dispatch_top_level_event(obj, raw, event_type)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type == "message_start":
            self._record_message_start(obj)
            return iter(())
        if event_type == "message_stop":
            flushed = self._flush_all_accumulators()
            self._current_message_id = None
            self._seen_content_blocks.clear()
            return flushed
        if event_type == "content_block_stop":
            return self._flush_content_block(obj)
        if event_type in self._LIFECYCLE_EVENT_TYPES:
            return iter(())
        return None

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
                yield from self._flush_text_accumulator(key)
            if key in self._thinking_accumulator:
                yield from self._flush_thinking_accumulator(key)

    def _dispatch_top_level_event(
        self,
        obj: dict[str, object],
        raw: str,
        event_type: str,
    ) -> Iterator[AgentOutputLine]:
        if event_type == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield from self._parse_stream_inner(event, raw)
            else:
                yield AgentOutputLine(type="stream_event", raw=raw, metadata=obj)
            return

        if event_type == "content_block_delta":
            yield from self._parse_content_block_delta(obj, raw)
            return

        if event_type == "content_block_start":
            self._track_content_block_start(obj)
            yield from self._parse_content_block_start(obj, raw)
            return

        if event_type == "assistant":
            yield from self._parse_assistant_message(obj, raw)
            return

        if event_type == "result":
            yield from self._parse_result_event(obj, raw)
            return

        if event_type == "error":
            yield from self._parse_error_event(obj, raw)
            return

        yield AgentOutputLine(type=event_type, raw=raw, metadata=obj)

    def _track_content_block_start(self, obj: dict[str, object]) -> None:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict) or self._current_message_id is None:
            return
        index = obj.get("index")
        if not isinstance(index, int):
            return
        block_type = str(content_block.get("type", ""))
        key = (self._current_message_id, index)
        if block_type == "text":
            if key not in self._text_accumulator:
                self._text_accumulator[key] = TextAccumulator()
        elif block_type == "thinking" and key not in self._thinking_accumulator:
            self._thinking_accumulator[key] = TextAccumulator()

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
            # Track content block start for text and thinking blocks
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

        if delta_type == "thinking_delta":
            yield from self._accumulate_thinking_delta(obj, delta, raw)
            return

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
                yield from self._text_accumulator[block_key].accumulate(
                    text, raw, kind="text", keep_current_when_empty=False
                )
                return

        # No keyed content block context - accumulate in a fallback stream bucket.
        if self._fallback_accumulator is None:
            self._fallback_accumulator = TextAccumulator()
        yield from self._fallback_accumulator.accumulate(
            text, raw, kind="text", keep_current_when_empty=True
        )

    def _accumulate_thinking_delta(
        self,
        obj: dict[str, object],
        delta: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        # thinking_delta uses field key "thinking" (not "text")
        text = str(delta.get("thinking", delta.get("text", "")))
        # Skip whitespace-only deltas with no paragraph-boundary markers.
        if not text.strip() and "\n\n" not in text:
            return

        index = obj.get("index")

        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._thinking_accumulator:
                yield from self._thinking_accumulator[key].accumulate(
                    text, raw, kind="thinking", keep_current_when_empty=False
                )
                return

        # Fallback thinking accumulator
        if self._fallback_thinking_accumulator is None:
            self._fallback_thinking_accumulator = TextAccumulator()
        yield from self._fallback_thinking_accumulator.accumulate(
            text, raw, kind="thinking", keep_current_when_empty=True
        )

    def _flush_text_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        """Flush a single text accumulator and remove it."""
        if key not in self._text_accumulator:
            return
        acc = self._text_accumulator.pop(key)
        yield from acc.flush(kind="text")

    def _flush_thinking_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        """Flush a single thinking accumulator and remove it."""
        if key not in self._thinking_accumulator:
            return
        acc = self._thinking_accumulator.pop(key)
        yield from acc.flush(kind="thinking", require_strip=True)

    def _flush_fallback_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_accumulator is None:
            return
        acc = self._fallback_accumulator
        self._fallback_accumulator = None
        yield from acc.flush(kind="text")

    def _flush_fallback_thinking_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_thinking_accumulator is None:
            return
        acc = self._fallback_thinking_accumulator
        self._fallback_thinking_accumulator = None
        yield from acc.flush(kind="thinking", require_strip=True)

    def _flush_all_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending accumulators on message_stop or iterator exhaustion."""
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_text_accumulator(key)
        for key in list(self._thinking_accumulator.keys()):
            yield from self._flush_thinking_accumulator(key)
        yield from self._flush_fallback_accumulator()
        yield from self._flush_fallback_thinking_accumulator()

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
                yield from self._parse_tool_result(block_obj, raw)
                continue

            if block_type == "thinking":
                text = str(block_obj.get("thinking", block_obj.get("text", "")))
                # Skip whitespace-only thinking content — carries no user payload.
                if text.strip():
                    yield AgentOutputLine(
                        type="thinking", content=text, raw=raw, metadata=block_obj
                    )
                continue

            # Non-text, non-tool, non-thinking block types (e.g., image) are rejected
            yield AgentOutputLine(
                type="error",
                content=f"unsupported content block type '{block_type}' in agent output",
                raw=raw,
                metadata=block_obj,
            )

    def _parse_prefixed_transcript_line(self, raw: str) -> list[AgentOutputLine] | None:  # noqa: PLR0911
        if raw.startswith("[claude]:"):
            return []

        m = _CLAUDE_PREFIX_RE.match(raw)
        if m is None:
            return None

        remainder = raw[m.end():]

        # "claude: text" or "claude/<model>: text"
        if remainder.startswith(": "):
            text = remainder[2:]
            # Suppress known lifecycle markers (carry no user payload)
            if text in _LIFECYCLE_MARKERS or text.startswith("system (status="):
                return []
            return [AgentOutputLine(type="text", content=text, raw=raw)]

        # "claude tool: ..." or "claude/<model> tool: ..."
        if remainder.startswith(" tool: "):
            payload = remainder[7:]
            return self._parse_prefixed_tool_line(raw, payload)

        # "claude user: message=..." or "claude/<model> user: message=..."
        for role in ("user", "assistant"):
            role_prefix = f" {role}: message="
            if remainder.startswith(role_prefix):
                return self._parse_prefixed_message_line(raw, remainder[len(role_prefix):])

        # "claude message_delta:..." (old bare format) → suppress
        if remainder.startswith(" message_delta") or remainder.startswith(" system: status="):
            return []

        # "claude/<model> ✗: error text" → error
        if remainder.startswith(" ✗: "):
            error_text = remainder[4:]
            return [AgentOutputLine(type="error", content=error_text, raw=raw)]

        return None

    def _parse_prefixed_tool_line(self, raw: str, payload: str) -> list[AgentOutputLine]:
        payload = payload.strip()
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

    def _parse_prefixed_message_line(
        self, raw: str, json_payload: str
    ) -> list[AgentOutputLine] | None:
        try:
            parsed: object = json.loads(json_payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None

        content = parsed.get("content")
        if not isinstance(content, list):
            return []

        return list(self._parse_message_content(content, raw))

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
            yield from self._parse_tool_result(content_block, raw)
            return

        # thinking blocks are handled via delta accumulation; no emission at block_start
        if block_type == "thinking":
            return

        # Non-text, non-tool, non-thinking block types (e.g., image) are rejected
        yield AgentOutputLine(
            type="error",
            content=f"unsupported content block type '{block_type}' in agent output",
            raw=raw,
            metadata=content_block,
        )

    def _parse_tool_result(
        self,
        block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Parse a tool_result content block, preserving multimodal content as bounded summaries."""
        content = block.get("content")
        if content is None:
            yield AgentOutputLine(type="tool_result", content="", raw=raw, metadata=block)
            return

        if isinstance(content, list):
            tool_result = stringify_text_blocks(content, require_text_type=True)
            yield AgentOutputLine(
                type="tool_result",
                content=tool_result,
                raw=raw,
                metadata=block,
            )
            return

        # String content is passed through
        yield AgentOutputLine(type="tool_result", content=str(content), raw=raw, metadata=block)
