from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.vt_normalizer import normalize_vt_text

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_SHORT_CONTENT_THRESHOLD = 200

_PLAIN_TOOL_PREFIX = "[plain] tool:"


def _classify_plaintext_tool_line(stripped: str) -> tuple[str, str] | None:
    if stripped.startswith(_PLAIN_TOOL_PREFIX):
        tool_name = stripped[len(_PLAIN_TOOL_PREFIX) :].strip()
        return ("tool_use", tool_name)
    return None


class GenericParser(NdjsonParserBase):
    _STOP_TYPES: frozenset[str] = frozenset({"stop", "done", "complete", "finish", "end"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator | None = None

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        classification = _classify_plaintext_tool_line(normalized)
        if classification is not None:
            line_type, content = classification
            yield AgentOutputLine(type=line_type, content=content, raw=stripped)
            return
        yield AgentOutputLine(type="raw", content=normalized, raw=stripped)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self._try_register_subagent_pid_from_obj(obj)
        yield from self._classify_parsed_json(obj, raw)

    def _classify_parsed_json(
        self, obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        if self._is_stop(obj):
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        if self._is_error(obj):
            yield from self._flush_accumulator()
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
            )
            return

        content = self._extract_content(obj)
        if content:
            yield from self._process_content(content, stripped)
            return

        thinking = self._extract_thinking_content(obj)
        if thinking:
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="thinking", content=thinking, raw=stripped, metadata=obj)
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="unknown", raw=stripped, metadata=obj)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type in self._STOP_TYPES:
            return self._flush_stop_event(obj, event_type)
        return super()._handle_lifecycle_event(obj, event_type)

    def _flush_stop_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._flush_accumulator()
        yield AgentOutputLine(type="stop", raw=event_type, metadata=obj)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._flush_accumulator()

    def _is_short_content(self, content: str) -> bool:
        if len(content) >= _SHORT_CONTENT_THRESHOLD:
            return False
        return not content.endswith("\n\n")

    def _process_content(self, content: str, raw: str) -> Iterator[AgentOutputLine]:
        if not content:
            return

        if content.endswith("\n\n"):
            yield from self._flush_accumulator()
            emit_content = content[:-2]
            if emit_content:
                yield AgentOutputLine(type="text", content=emit_content, raw=raw)
            return

        if self._is_short_content(content):
            if self._text_accumulator is None:
                self._text_accumulator = TextAccumulator()
            yield from self._text_accumulator.accumulate(
                content, raw, kind="text", keep_current_when_empty=False
            )
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="text", content=content, raw=raw)

    def _flush_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")

    def _extract_content(self, obj: dict[str, object]) -> str:
        for field_name in ("content", "text", "message", "output", "response", "result"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get("text") or value.get("content")
                if isinstance(nested, str) and nested:
                    return nested
        return ""

    def _extract_thinking_content(self, obj: dict[str, object]) -> str:
        for field_name in ("thought", "reasoning"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
        return ""

    def _is_error(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return "error" in type_val or bool(obj.get("error"))

    def _is_stop(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return type_val in self._STOP_TYPES
