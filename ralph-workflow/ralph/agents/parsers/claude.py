
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from ._assistant_envelope import (
    ENVELOPE_API_ERROR,
    ENVELOPE_SYNTHETIC,
    AssistantEnvelopeDecision,
    classify_assistant_record,
)
from ._event_classification import is_lifecycle_event
from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message, stringify_text_blocks
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_CLAUDE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^claude(?:/[^:\s]+)?(?=[ :]|$)")

_CLAUDE_TOP_LEVEL_LIFECYCLE: Final[frozenset[str]] = frozenset(
    {"message_start", "message_stop", "content_block_stop"}
)

_CLAUDE_NOISE_SYSTEM_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"init", "hook_started", "hook_response", "thinking_tokens"}
)

_CLAUDE_EXPECTED_STOP_REASONS: Final[frozenset[str]] = frozenset({"end_turn", "tool_use"})

_CLAUDE_RATE_LIMIT_OK_STATUSES: Final[frozenset[str]] = frozenset({"allowed"})


def _content_block_fingerprint(block: dict[str, object]) -> str:
    return json.dumps(block, sort_keys=True, separators=(",", ":"), default=str)


class ClaudeParser(NdjsonParserBase):

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: dict[
            tuple[str, int], TextAccumulator
        ] = {}
        self._thinking_accumulator: dict[
            tuple[str, int], TextAccumulator
        ] = {}
        self._fallback_accumulator: TextAccumulator | None = None
        self._fallback_thinking_accumulator: TextAccumulator | None = None
        self._current_message_id: str | None = None
        self._seen_content_blocks: set[tuple[str, int]] = set()
        self._emitted_text: set[str] = set()
        self._streamed_block_fingerprints: set[str] = set()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        if not stripped:
            return

        prefixed_lines = self._parse_prefixed_transcript_line(stripped)
        if prefixed_lines is not None:
            yield from prefixed_lines
            return

        yield from super().classify_line(stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_text_accumulator(key)
        for key in list(self._thinking_accumulator.keys()):
            yield from self._flush_thinking_accumulator(key)
        yield from self._flush_fallback_accumulator()
        yield from self._flush_fallback_thinking_accumulator()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type == "message_start":
            self._record_message_start(obj)
            return iter(())
        if event_type == "message_stop":
            flushed = self.flush_accumulators()
            self._current_message_id = None
            return flushed
        if event_type == "content_block_stop":
            return self._flush_content_block(obj)
        if event_type in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            return iter(())
        return None

    def _record_message_start(self, obj: dict[str, object]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        msg_id = str(message.get("id", ""))
        if msg_id:
            self._seen_content_blocks.clear()
            self._emitted_text.clear()
            self._streamed_block_fingerprints.clear()
            self._current_message_id = msg_id

    def _flush_content_block(self, obj: dict[str, object]) -> Iterator[AgentOutputLine]:
        index = obj.get("index")
        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._text_accumulator:
                yield from self._flush_text_accumulator(key)
            if key in self._thinking_accumulator:
                yield from self._flush_thinking_accumulator(key)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        event_type = str(obj.get("type", "unknown"))

        self._try_register_subagent_pid_from_obj(obj)

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
        elif event_type in ("assistant", "user"):
            yield from self._parse_role_message(obj, raw)
        elif event_type == "result":
            yield from self._parse_result_event(obj, raw)
        elif event_type == "error":
            yield from self._parse_error_event(obj, raw)
        elif event_type == "system":
            yield from self._parse_system_event(obj, raw)
        elif event_type == "rate_limit_event":
            yield from self._parse_rate_limit_event(obj, raw)
        else:
            yield self._unclassified_event_line(event_type, obj, raw)

    def _parse_system_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        subtype = str(obj.get("subtype", ""))
        if subtype in _CLAUDE_NOISE_SYSTEM_SUBTYPES:
            return
        status = obj.get("status")
        if isinstance(status, str) and status:
            content = f"{subtype} ({status})" if subtype else status
        else:
            content = subtype or "unknown"
        yield AgentOutputLine(type="system", content=content, raw=raw, metadata=obj)

    def _parse_rate_limit_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        info = obj.get("rate_limit_info")
        info_dict: dict[str, object] = info if isinstance(info, dict) else {}
        status = str(info_dict.get("status", ""))
        using_overage = bool(info_dict.get("isUsingOverage", False))
        if status in _CLAUDE_RATE_LIMIT_OK_STATUSES and not using_overage:
            return
        yield AgentOutputLine(
            type="rate_limit_event",
            content=status or "unknown",
            raw=raw,
            metadata=obj,
        )

    def _unclassified_event_line(
        self,
        event_type: str,
        obj: dict[str, object],
        raw: str,
    ) -> AgentOutputLine:
        subtype = obj.get("subtype")
        content = str(subtype) if subtype else ""
        return AgentOutputLine(type=event_type, content=content, raw=raw, metadata=obj)

    def _track_content_block_start(self, obj: dict[str, object]) -> None:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict) or self._current_message_id is None:
            return
        index = obj.get("index")
        if not isinstance(index, int):
            return
        block_type = str(content_block.get("type", ""))
        key = (self._current_message_id, index)
        self._seen_content_blocks.add(key)
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

        if event_type == "message_start":
            self._record_message_start(event)
        elif event_type == "message_stop":
            yield from self.flush_accumulators()
            self._current_message_id = None
        elif event_type == "content_block_stop":
            yield from self._flush_content_block(event)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(event, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(event)
            yield from self._parse_stream_content_block_start(event, raw)
        elif event_type == "error":
            yield from self._parse_stream_error(event, raw)
        elif event_type == "message_delta":
            yield from self._parse_stream_message_delta(event, raw)
        elif event_type not in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            yield self._unclassified_event_line(event_type, event, raw)

    def _parse_stream_message_delta(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        delta = event.get("delta")
        delta_dict: dict[str, object] = delta if isinstance(delta, dict) else {}
        stop_reason = delta_dict.get("stop_reason")
        if stop_reason is None or str(stop_reason) in _CLAUDE_EXPECTED_STOP_REASONS:
            return
        yield AgentOutputLine(
            type="message_delta", content=str(stop_reason), raw=raw, metadata=event
        )

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

        index = obj.get("index")
        block_key: tuple[str, int] | None = None

        if isinstance(index, int) and self._current_message_id is not None:
            block_key = (self._current_message_id, index)
            if block_key in self._text_accumulator:
                yield from self._text_accumulator[block_key].accumulate(
                    text, raw, kind="text", keep_current_when_empty=False
                )
                return

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
        text = str(delta.get("thinking", delta.get("text", "")))
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

        if self._fallback_thinking_accumulator is None:
            self._fallback_thinking_accumulator = TextAccumulator()
        yield from self._fallback_thinking_accumulator.accumulate(
            text, raw, kind="thinking", keep_current_when_empty=True
        )

    def _flush_text_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._text_accumulator:
            return
        acc = self._text_accumulator.pop(key)
        for line in acc.flush(kind="text"):
            self._emitted_text.add(line.content)
            yield line

    def _flush_thinking_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._thinking_accumulator:
            return
        acc = self._thinking_accumulator.pop(key)
        yield from acc.flush(kind="thinking", require_strip=True)

    def _flush_fallback_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_accumulator is None:
            return
        acc = self._fallback_accumulator
        self._fallback_accumulator = None
        for line in acc.flush(kind="text"):
            self._emitted_text.add(line.content)
            yield line

    def _flush_fallback_thinking_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_thinking_accumulator is None:
            return
        acc = self._fallback_thinking_accumulator
        self._fallback_thinking_accumulator = None
        yield from acc.flush(kind="thinking", require_strip=True)

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
        if result and result not in self._emitted_text:
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
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_stream_content_block_start(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = event.get("content_block")
        if not isinstance(content_block, dict):
            return

        self._streamed_block_fingerprints.add(_content_block_fingerprint(content_block))
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

    def _parse_role_message(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        if obj.get("type") == "assistant":
            decision: AssistantEnvelopeDecision = classify_assistant_record(obj)
            if decision.envelope == ENVELOPE_API_ERROR:
                if decision.error_text:
                    yield AgentOutputLine(
                        type="error",
                        content=decision.error_text,
                        raw=raw,
                        metadata=obj,
                    )
                return
            if decision.envelope == ENVELOPE_SYNTHETIC:
                payload = decision.synthetic_text or "synthetic bookkeeping turn"
                yield AgentOutputLine(
                    type="lifecycle",
                    content=payload,
                    raw=raw,
                    metadata=obj,
                )
                return

        message = obj.get("message")
        if not isinstance(message, dict):
            return

        content = message.get("content")
        if not isinstance(content, list):
            return

        message_id = str(message.get("id", ""))
        yield from self._parse_message_content(content, raw, message_id)

    def _parse_message_content(
        self,
        content: list[object],
        raw: str,
        message_id: str = "",
    ) -> Iterator[AgentOutputLine]:
        for index, block in enumerate(content):
            if message_id and (message_id, index) in self._seen_content_blocks:
                continue
            if not isinstance(block, dict):
                continue

            block_obj = cast("dict[str, object]", block)
            if _content_block_fingerprint(block_obj) in self._streamed_block_fingerprints:
                continue
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
                if text.strip():
                    yield AgentOutputLine(
                        type="thinking", content=text, raw=raw, metadata=block_obj
                    )
                continue

            yield AgentOutputLine(
                type="error",
                content=f"unsupported content block type '{block_type}' in agent output",
                raw=raw,
                metadata=block_obj,
            )

    def _parse_plain_text_prefix(self, raw: str, text: str) -> list[AgentOutputLine]:
        if is_lifecycle_event(text) or text.startswith("system (status="):
            return []
        return [AgentOutputLine(type="text", content=text, raw=raw)]

    def _parse_structured_remainder(self, raw: str, remainder: str) -> list[AgentOutputLine] | None:
        for role in ("user", "assistant"):
            role_prefix = f" {role}: message="
            if remainder.startswith(role_prefix):
                return self._parse_prefixed_message_line(raw, remainder[len(role_prefix) :])
        if remainder.startswith(" message_delta") or remainder.startswith(" system: status="):
            return []
        if remainder.startswith(" ✗: "):
            return [AgentOutputLine(type="error", content=remainder[4:], raw=raw)]
        return None

    def _parse_prefixed_transcript_line(self, raw: str) -> list[AgentOutputLine] | None:
        if raw.startswith("[claude]:"):
            return []
        m = _CLAUDE_PREFIX_RE.match(raw)
        if m is None:
            return None
        remainder = raw[m.end() :]
        if remainder.startswith(": "):
            return self._parse_plain_text_prefix(raw, remainder[2:])
        if remainder.startswith(" tool: "):
            return self._parse_prefixed_tool_line(raw, remainder[7:])
        return self._parse_structured_remainder(raw, remainder)

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

        if block_type == "thinking":
            return

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
        result_type = "error" if block.get("is_error") else "tool_result"
        content = block.get("content")
        if content is None:
            yield AgentOutputLine(type=result_type, content="", raw=raw, metadata=block)
            return

        if isinstance(content, list):
            tool_result = stringify_text_blocks(content, require_text_type=True)
            yield AgentOutputLine(
                type=result_type,
                content=tool_result,
                raw=raw,
                metadata=block,
            )
            return

        yield AgentOutputLine(type=result_type, content=str(content), raw=raw, metadata=block)
