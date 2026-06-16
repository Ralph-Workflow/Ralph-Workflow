"""Parser for Pi's AgentSessionEvent NDJSON streaming format.

Pi (https://pi.dev) is a terminal coding agent that, in
``--mode json`` headless mode, emits one JSON line per
``AgentSessionEvent``.  The event vocabulary is the documented
TypeScript union at https://pi.dev/docs/latest/json:

  - session header line: ``{type:'session', version, id, timestamp, cwd}``
  - agent lifecycle: ``agent_start``, ``agent_end``
  - turn lifecycle: ``turn_start``, ``turn_end``
  - message lifecycle: ``message_start`` (in LIFECYCLE_EVENT_TYPES so the
    base suppresses it), ``message_update`` (carries
    ``assistantMessageEvent``), ``message_end``
  - tool execution: ``tool_execution_start``, ``tool_execution_update``,
    ``tool_execution_end`` (with ``isError`` boolean)
  - queue: ``queue_update``
  - compaction: ``compaction_start``, ``compaction_end``
  - auto-retry: ``auto_retry_start``, ``auto_retry_end``
  - extension: ``extension_error``

The ``message_update`` events carry an ``assistantMessageEvent`` with
its own sub-type union:

  - ``text_start`` / ``text_delta`` / ``text_end``
  - ``thinking_start`` / ``thinking_delta`` / ``thinking_end``
  - ``toolcall_start`` / ``toolcall_delta`` / ``toolcall_end``
  - ``done`` (``stopReason: 'stop' | 'length' | 'toolUse'``)
  - ``error`` (``reason: 'aborted' | 'error'``)

Pi's ``message_start`` event is the only one that overlaps with the
shared :data:`LIFECYCLE_EVENT_TYPES` frozenset, so the base layer
silences it.  All other pi events are NOT in the lifecycle set and
reach the subclass ``_dispatch_json_object`` hook for per-event routing.

Inherits from :class:`NdjsonParserBase` which owns the 6 shared NDJSON
behaviors (data: strip, [DONE] short-circuit, JSON parse dispatch,
lifecycle suppression, error extraction).  The subclass
``_dispatch_json_object`` delegates to :class:`_PiDispatch` for the
per-event-type routing and accumulator management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


_TEXT_ACCUMULATOR_KEY = "text"
_THINKING_ACCUMULATOR_KEY = "thinking"


# Top-level event types that pass through with their type as the
# ``AgentOutputLine.type`` and the full object as ``metadata``.  Used
# for queue_update / compaction_* / auto_retry_*.
_PI_PASSTHROUGH_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {
        "queue_update",
        "compaction_start",
        "compaction_end",
        "auto_retry_start",
        "auto_retry_end",
    }
)


# Stop events: flush accumulators and emit a single ``type='stop'`` line.
_PI_STOP_EVENTS: frozenset[str] = frozenset({"agent_end", "turn_end"})


# Silent top-level events: lifecycle-like boundaries that pi treats as
# metadata-only and that produce no AgentOutputLine.
_PI_SILENT_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {"agent_start", "turn_start"}
)


# ``assistantMessageEvent`` sub-types that produce no output (start
# boundaries that the parser waits to see a corresponding end for).
_PI_SILENT_SUB_EVENTS: frozenset[str] = frozenset({"text_start", "thinking_start"})


def _make_passthrough(
    event_type: str,
) -> Callable[[dict[str, object], str], Iterator[AgentOutputLine]]:
    """Build a passthrough handler that pins a specific event_type name."""

    def _handler(
        obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    return _handler


class _PiDispatch:
    """Per-event-type dispatch for PiParser.

    Encapsulates the routing logic for the documented AgentSessionEvent
    union.  Holds a reference to the owning parser so accumulator state
    stays in one place.
    """

    def __init__(self, owner: PiParser) -> None:
        self._owner = owner
        # Bind the per-event handlers to ``self`` so the dispatch tables
        # below can be invoked as plain callables (obj, stripped) -> Iterator.
        self._top_level_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "session": self._handle_session,
            "message_end": self._handle_message_end,
            "message_update": self._handle_message_update,
            "tool_execution_start": self._handle_tool_execution_start,
            "tool_execution_update": self._handle_tool_execution_update,
            "tool_execution_end": self._handle_tool_execution_end,
            "extension_error": self._handle_extension_error,
        }
        for _evt in _PI_PASSTHROUGH_TOP_LEVEL_EVENTS:
            self._top_level_handlers[_evt] = _make_passthrough(_evt)
        self._sub_event_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "text_delta": self._handle_text_delta,
            "text_end": self._handle_text_end,
            "thinking_delta": self._handle_thinking_delta,
            "thinking_end": self._handle_thinking_end,
            "toolcall_start": self._handle_toolcall_event,
            "toolcall_delta": self._handle_toolcall_event,
            "toolcall_end": self._handle_toolcall_event,
            "done": self._handle_done,
            "error": self._handle_message_error,
        }

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in _PI_SILENT_TOP_LEVEL_EVENTS:
            return

        if event_type in _PI_STOP_EVENTS:
            yield from self._owner.flush_accumulators()
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        handler = self._top_level_handlers.get(event_type)
        if handler is not None:
            yield from handler(obj, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _handle_session(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        metadata: dict[str, object] = dict(obj)
        yield AgentOutputLine(type="session", raw=stripped, metadata=metadata)

    def _handle_message_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._emit_message_content(obj, stripped)

    def _handle_message_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        assistant_event = obj.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return
        sub = cast("dict[str, object]", assistant_event)
        sub_type = str(sub.get("type", "unknown"))
        if sub_type in _PI_SILENT_SUB_EVENTS:
            return
        handler = self._sub_event_handlers.get(sub_type)
        if handler is not None:
            yield from handler(sub, stripped)
            return
        yield AgentOutputLine(type=sub_type, raw=stripped, metadata=sub)

    def _handle_tool_execution_start(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(obj.get("toolName", "unknown"))
        args = obj.get("args", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args},
        )

    def _handle_tool_execution_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        partial = obj.get("partialResult", "")
        yield AgentOutputLine(
            type="tool_result",
            content=partial if isinstance(partial, str) else str(partial),
            raw=stripped,
            metadata=obj,
        )

    def _handle_tool_execution_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = obj.get("isError", False)
        if is_error:
            result = obj.get("result", "")
            yield AgentOutputLine(
                type="error",
                content=str(result) if result else "tool execution failed",
                raw=stripped,
                metadata=obj,
            )
            return
        result = obj.get("result", "")
        yield AgentOutputLine(
            type="tool_result",
            content=str(result) if result else "",
            raw=stripped,
            metadata=obj,
        )

    def _handle_extension_error(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = str(obj.get("error", "extension error"))
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _handle_text_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        acc = self._get_text_accumulator()
        yield from acc.accumulate(
            delta, stripped, kind="text", keep_current_when_empty=True
        )

    def _handle_text_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        if not content:
            yield from self._flush_text_accumulator()
            return
        yield from self._flush_text_accumulator()
        yield AgentOutputLine(type="text", content=content, raw=stripped, metadata=sub)

    def _handle_thinking_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        acc = self._get_thinking_accumulator()
        yield from acc.accumulate(
            delta,
            stripped,
            kind="thinking",
            keep_current_when_empty=True,
        )

    def _handle_thinking_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        yield from self._flush_thinking_accumulator()
        if content.strip():
            yield AgentOutputLine(
                type="thinking",
                content=content,
                raw=stripped,
                metadata=sub,
            )

    def _handle_toolcall_event(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_call = sub.get("toolCall")
        tool_name = "unknown"
        if isinstance(tool_call, dict):
            tool_name = str(cast("dict[str, object]", tool_call).get("name", "unknown"))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata=sub,
        )

    def _handle_done(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._owner.flush_accumulators()
        yield AgentOutputLine(type="stop", raw=stripped, metadata=sub)

    def _handle_message_error(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        reason = str(sub.get("reason", "error"))
        yield AgentOutputLine(type="error", content=reason, raw=stripped, metadata=sub)

    def _emit_message_content(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        message_dict = cast("dict[str, object]", message)
        content = message_dict.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
            block_type = str(block_dict.get("type", ""))
            if block_type == "text":
                yield from self._handle_text_block(block_dict, stripped)
            elif block_type == "thinking":
                yield from self._handle_thinking_block(block_dict, stripped)
            elif block_type == "toolCall":
                yield from self._handle_toolcall_block(block_dict, stripped)

    def _handle_text_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(block_dict.get("text", ""))
        if text:
            yield AgentOutputLine(
                type="text", content=text, raw=stripped, metadata=block_dict
            )

    def _handle_thinking_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        thinking = str(block_dict.get("thinking", ""))
        if thinking:
            yield AgentOutputLine(
                type="thinking",
                content=thinking,
                raw=stripped,
                metadata=block_dict,
            )

    def _handle_toolcall_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(block_dict.get("name", "unknown"))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata=block_dict,
        )

    def _get_text_accumulator(self) -> TextAccumulator:
        accumulators = self._owner._accumulators
        if _TEXT_ACCUMULATOR_KEY not in accumulators:
            accumulators[_TEXT_ACCUMULATOR_KEY] = TextAccumulator()
        return accumulators[_TEXT_ACCUMULATOR_KEY]

    def _get_thinking_accumulator(self) -> TextAccumulator:
        accumulators = self._owner._accumulators
        if _THINKING_ACCUMULATOR_KEY not in accumulators:
            accumulators[_THINKING_ACCUMULATOR_KEY] = TextAccumulator()
        return accumulators[_THINKING_ACCUMULATOR_KEY]

    def _flush_text_accumulator(self) -> Iterator[AgentOutputLine]:
        acc = self._owner._accumulators.pop(_TEXT_ACCUMULATOR_KEY, None)
        if acc is None:
            return
        yield from acc.flush(kind="text")

    def _flush_thinking_accumulator(self) -> Iterator[AgentOutputLine]:
        acc = self._owner._accumulators.pop(_THINKING_ACCUMULATOR_KEY, None)
        if acc is None:
            return
        yield from acc.flush(kind="thinking", require_strip=True)


class PiParser(NdjsonParserBase):
    """Parser for pi.dev's AgentSessionEvent NDJSON streaming format.

    Text deltas are accumulated into coherent blocks before emission,
    flushing on:
      - ``message_update`` with ``assistantMessageEvent.type == 'text_end'``
        (or ``'done'``)
      - ``message_end`` (fall back to the buffered blocks)
      - ``agent_end`` / ``turn_end`` (final flush)
      - Iterator exhaustion (final flush via ``flush_accumulators()``)

    Thinking deltas are accumulated in a SEPARATE accumulator so the
    text and reasoning streams do not interleave.

    The single consistent isError rule: ``tool_execution_end.isError=true``
    maps to ``type='error'``; ``isError=false`` (or absent) maps to
    ``type='tool_result'``.

    Inherits from :class:`NdjsonParserBase` which owns the 6 shared NDJSON
    behaviors.  The subclass ``_dispatch_json_object`` delegates to
    :class:`_PiDispatch` for the per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        super().__init__()
        self._accumulators: dict[str, TextAccumulator] = {}
        self._dispatcher = _PiDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        keys = list(self._accumulators.keys())
        for key in keys:
            acc = self._accumulators.pop(key, None)
            if acc is None:
                continue
            if key == _THINKING_ACCUMULATOR_KEY:
                yield from acc.flush(kind="thinking", require_strip=True)
            else:
                yield from acc.flush(kind="text")
