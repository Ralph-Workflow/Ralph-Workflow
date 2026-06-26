"""Parser for Codex's NDJSON streaming format."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


def _parse_codex_object(
    obj: dict[str, object],
    stripped: str,
) -> Iterator[AgentOutputLine]:
    """Public parse-object helper preserved for back-compat with downstream tests.

    Delegates to the same per-event dispatch that ``CodexParser`` uses.
    Kept as a module-level function so external callers (e.g. legacy tests
    importing ``_parse_codex_object``) keep working unchanged.
    """
    yield from _CodexDispatch().dispatch(obj, stripped)


class _CodexDispatch:
    """Per-event-type dispatch for CodexParser.

    Encapsulates the historical ``handler_map`` + ``_parse_object`` method
    body as a plain callable so the subclass ``_dispatch_json_object`` can
    delegate to it without re-implementing the routing.  Holds a reference
    to the owning parser so accumulator state stays in one place.
    """

    def __init__(self, owner: CodexParser | None = None) -> None:
        self._owner = owner
        self._accumulators: dict[str, TextAccumulator] = {}  # bounded-accumulator-ok: drained
        self._current_response_id: str | None = None
        self._stream_counter = 0

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in CodexParser._STOP_EVENT_TYPES:
            yield from self._flush_all()
            self._current_response_id = None
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

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

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _flush_all(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            yield from self._flush_accumulator(key)

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

        if not content:
            return

        response_id = str(obj.get("response_id", obj.get("responseId", "")) or "")
        if not response_id:
            if self._current_response_id:
                response_id = self._current_response_id
            else:
                yield AgentOutputLine(type="text", content=content, raw=stripped)
                return

        key = response_id
        accumulators = self._accumulators
        if self._owner is not None:
            accumulators = self._owner._accumulators
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        yield from accumulators[key].accumulate(
            content, stripped, kind="text", keep_current_when_empty=True
        )

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        accumulators = self._accumulators
        if self._owner is not None:
            accumulators = self._owner._accumulators
        if key not in accumulators:
            return
        acc = accumulators.pop(key)
        yield from acc.flush(kind="text")

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
        error_msg = extract_error_message(obj)
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

        if item_type == "reasoning" and text:
            yield AgentOutputLine(type="thinking", content=text, raw=stripped, metadata=item_obj)
            return

        if item_type == "agent_message" and text:
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


class CodexParser(NdjsonParserBase):
    """Parser for Codex's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``response.completed`` / ``turn.completed`` / ``message_stop`` (end of message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``flush_accumulators()``)

    Inherits from :class:`NdjsonParserBase` which owns the
    ``data:`` strip, ``[DONE]`` short-circuit, JSON parse dispatch,
    lifecycle suppression, and error extraction.  The subclass
    ``_dispatch_json_object`` delegates to ``_CodexDispatch`` for the
    per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"turn.completed", "message_stop", "done", "stop", "response.completed"}
    )

    def __init__(self, subagent_pid_registry: SubagentPidRegistry | None = None) -> None:
        super().__init__()
        # Store the registry (forward-compat; Codex's NDJSON events do
        # not currently carry embedded PIDs). The stored reference lets
        # future code paths register a discovered child PID into the
        # shared registry without re-plumbing the constructor signature.
        self._subagent_pid_registry: SubagentPidRegistry | None = (
            subagent_pid_registry
        )
        self._accumulators: dict[str, TextAccumulator] = {}  # bounded-accumulator-ok: drained
        self._current_response_id: str | None = None
        self._stream_counter = 0
        self._dispatcher = _CodexDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            if key not in self._accumulators:
                continue
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")
