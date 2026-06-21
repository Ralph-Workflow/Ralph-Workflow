"""Parser for Pi's AgentSessionEvent NDJSON streaming format.

Pi (https://pi.dev) is a terminal coding agent that, in
``--mode json`` headless mode, emits one JSON line per
``AgentSessionEvent``.  The event vocabulary is the documented
TypeScript union at https://pi.dev/docs/latest/json:

  - session header line: ``{type:'session', version, id, timestamp, cwd}``
  - agent lifecycle: ``agent_start``, ``agent_end``
  - turn lifecycle: ``turn_start``, ``turn_end``
  - message lifecycle: ``message_start`` (in LIFECYCLE_EVENT_TYPES so the
    base would suppress it; PiParser overrides
    :meth:`_handle_lifecycle_event` to fall through to
    ``_dispatch_json_object`` for every event type, and the dispatcher
    marks ``message_start`` silent), ``message_update`` (carries
    ``assistantMessageEvent``), ``message_end``
  - tool execution: ``tool_execution_start``, ``tool_execution_update``,
    ``tool_execution_end`` (with ``isError`` boolean)
  - queue: ``queue_update``
  - compaction: ``compaction_start``, ``compaction_end``
  - auto-retry: ``auto_retry_start``, ``auto_retry_end``

The current published AgentSessionEvent union enumerates EXACTLY these
16 top-level event types (re-fetched 2026-06-20 from
https://pi.dev/docs/latest/json; mirrored in
``tmp/pi-dev-docs/inventory.md``).  Anything outside this list is
forward-compat, not part of the documented contract.

Forward-compat only (NOT in the current published union):

  - ``extension_error`` — defensively accepted so a legacy or future
    pi.dev build that emits it does not break the parser; routed to
    ``type='error'``.  Deliberately excluded from the committed
    fixture and from the wire-format spec assertion so the live-doc
    contract stays aligned with the published pi.dev docs.

The ``message_update`` events carry an ``assistantMessageEvent`` with
its own sub-type union:

  - ``text_start`` / ``text_delta`` / ``text_end``
  - ``thinking_start`` / ``thinking_delta`` / ``thinking_end``
  - ``toolcall_start`` / ``toolcall_delta`` / ``toolcall_end``
  - ``done`` (``stopReason: 'stop' | 'length' | 'toolUse'``)
  - ``error`` (``reason: 'aborted' | 'error'``)

Pi's ``message_start`` event is the only pi event that overlaps with
the shared :data:`LIFECYCLE_EVENT_TYPES` frozenset, so PiParser
overrides :meth:`_handle_lifecycle_event` to return ``None`` for
every event type, which causes the base layer to fall through to
``_dispatch_json_object`` for the entire pi vocabulary.  The
dispatcher then routes each event through the per-type handler map
(silent for ``message_start`` / ``agent_start`` / ``turn_start``,
typed output for the rest).  This keeps the AC-04 invariant: every
documented pi event reaches ``_dispatch_json_object`` for routing
decisions; the dispatch table is the single owner of what each event
type emits.

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


_TEXT_KIND = "text"
_THINKING_KIND = "thinking"

_TEXT_ACCUMULATOR_PREFIX = "text:"
_THINKING_ACCUMULATOR_PREFIX = "thinking:"

# Sentinel used when a text_end / thinking_end event arrives without a
# numeric ``contentIndex`` field.  In that case we cannot track the
# terminal snapshot per-block (the streaming event did not tell us
# which block it closed), so the per-block accumulator is keyed by
# this sentinel and a separate "saw terminal" guard applies per block
# just like for any other contentIndex.  Real pi.dev output always
# carries an integer ``contentIndex`` (per the fixture and the live
# docs), so this branch is a defensive fallback only.
_LEGACY_UNINDEXED_CONTENT_INDEX = -1


def _content_index_of(sub: dict[str, object]) -> int:
    """Return the integer ``contentIndex`` of a streaming sub-event.

    The pi.dev ``AssistantMessageEvent`` events (``text_start`` /
    ``text_delta`` / ``text_end`` / ``thinking_start`` /
    ``thinking_delta`` / ``thinking_end`` / ``toolcall_start`` /
    ``toolcall_delta`` / ``toolcall_end``) all carry an integer
    ``contentIndex`` field per the documented wire format.  Real
    pi.dev output always carries an integer; if a malformed or
    forward-compat event omits it, we fall back to the
    :data:`_LEGACY_UNINDEXED_CONTENT_INDEX` sentinel so the
    per-block tracking still treats it as its own block.
    """
    raw = sub.get("contentIndex")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return _LEGACY_UNINDEXED_CONTENT_INDEX


def _accumulator_key(kind: str, content_index: int) -> str:
    """Compose the per-block accumulator key for a streaming sub-event.

    The key is ``{kind}:{contentIndex}`` so each active text or
    thinking content block accumulates independently, even when
    multiple blocks stream interleaved ``text_delta`` events for the
    same message.  Returns the literal ``"text"`` or ``"thinking"``
    kind string; callers may pass ``_TEXT_KIND`` or ``_THINKING_KIND``.
    """
    return f"{kind}:{content_index}"


def _extract_tool_result_text(result: object) -> str:
    """Normalize a Pi tool result payload into user-visible text.

    Pi's documented ``tool_execution_end.result`` and
    ``message_end.content[].toolResult.result`` payloads carry a
    typed content-array shape::

        {"content": [{"type": "text", "text": "..."}, ...]}

    The parser MUST emit the user-visible ``text`` rather than the
    raw ``str(result)`` (which would produce ``"{'content': [...]}"``
    and leak the dict literal to downstream consumers).  The single
    extraction rule is shared by the success path
    (``tool_execution_end`` with ``isError=false``, and the
    ``toolResult`` block from ``message_end``) and the error path
    (``tool_execution_end`` with ``isError=true``, and the
    ``toolResult`` block with ``isError=true``).

    Returns the joined ``text`` of every content block of type
    ``text`` (in order), or an empty string when the payload has no
    user-visible text.  For backwards compatibility with callers
    that already pass a plain string (or empty) ``result``, the
    input is returned unchanged.
    """
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result) if result else ""
    content = result.get("content")
    if not isinstance(content, list):
        return str(result)
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_dict = cast("dict[str, object]", block)
            if block_dict.get("type") == "text":
                text = block_dict.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


_PI_PASSTHROUGH_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {
        "queue_update",
        "compaction_start",
        "compaction_end",
        "auto_retry_start",
        "auto_retry_end",
    }
)


_PI_STOP_EVENTS: frozenset[str] = frozenset({"agent_end", "turn_end"})


_PI_SILENT_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {"agent_start", "turn_start", "message_start"}
)


_PI_SILENT_SUB_EVENTS: frozenset[str] = frozenset(
    {"text_start", "thinking_start"}
)


def _make_passthrough(
    event_type: str,
) -> Callable[[dict[str, object], str], Iterator[AgentOutputLine]]:
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
            "toolcall_start": self._handle_toolcall_start,
            "toolcall_delta": self._handle_toolcall_delta,
            "toolcall_end": self._handle_toolcall_end,
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
            self._owner.reset_emission_flags()
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
        # ``message_end`` carries the authoritative terminal snapshot for
        # any in-flight streaming content.  Discard any pending
        # streaming accumulators WITHOUT flushing them, because the
        # blocks in ``message_end.message.content`` are the canonical
        # final text/thinking; flushing on iterator exhaustion would
        # double-emit (once from the ``message_end`` block, once from
        # the leftover streaming buffer).  The per-index saw_*_by_index
        # sets already suppress any double-emission from the streaming
        # ``*_end`` events.
        for key in list(self._owner._accumulators.keys()):
            self._owner._accumulators.pop(key, None)
        yield from self._emit_message_content(obj, stripped)
        self._owner.reset_emission_flags()

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
        content = partial if isinstance(partial, str) else _extract_tool_result_text(partial)
        yield AgentOutputLine(
            type="tool_result",
            content=content,
            raw=stripped,
            metadata=obj,
        )

    def _handle_tool_execution_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = obj.get("isError", False)
        result = obj.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool execution failed",
                raw=stripped,
                metadata=obj,
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata=obj,
        )

    def _handle_extension_error(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = str(obj.get("error", "extension error"))
        yield AgentOutputLine(
            type="error", content=error_msg, raw=stripped, metadata=obj
        )

    def _handle_text_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_text_end_by_index:
            return
        acc = self._get_text_accumulator(content_index)
        yield from acc.accumulate(
            delta, stripped, kind=_TEXT_KIND, keep_current_when_empty=True
        )

    def _handle_text_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_text_end_by_index.add(content_index)
        acc_key = _accumulator_key(_TEXT_KIND, content_index)
        if content:
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(
                type=_TEXT_KIND, content=content, raw=stripped, metadata=sub
            )
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_TEXT_KIND)

    def _handle_thinking_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_thinking_end_by_index:
            return
        acc = self._get_thinking_accumulator(content_index)
        yield from acc.accumulate(
            delta,
            stripped,
            kind=_THINKING_KIND,
            keep_current_when_empty=True,
        )

    def _handle_thinking_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_thinking_end_by_index.add(content_index)
        acc_key = _accumulator_key(_THINKING_KIND, content_index)
        if content.strip():
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(
                type=_THINKING_KIND,
                content=content,
                raw=stripped,
                metadata=sub,
            )
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_THINKING_KIND, require_strip=True)

    def _handle_toolcall_start(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return
        yield  # pragma: no cover - explicit generator for the type checker

    def _handle_toolcall_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return
        yield  # pragma: no cover - explicit generator for the type checker

    def _handle_toolcall_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_call = sub.get("toolCall")
        tool_name = "unknown"
        if isinstance(tool_call, dict):
            tool_name = str(
                cast("dict[str, object]", tool_call).get("name", "unknown")
            )
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
        self._owner.reset_emission_flags()
        yield AgentOutputLine(type="stop", raw=stripped, metadata=sub)

    def _handle_message_error(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        reason = str(sub.get("reason", "error"))
        yield AgentOutputLine(
            type="error", content=reason, raw=stripped, metadata=sub
        )

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
        content_list = cast("list[object]", content)
        for block_index, block in enumerate(content_list):
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
            block_type = str(block_dict.get("type", ""))
            if (
                block_type == _TEXT_KIND
                and block_index not in self._owner.saw_text_end_by_index
            ):
                acc_key = _accumulator_key(_TEXT_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_text_end_by_index.add(block_index)
                yield from self._handle_text_block(block_dict, stripped)
            elif (
                block_type == _THINKING_KIND
                and block_index
                not in self._owner.saw_thinking_end_by_index
            ):
                acc_key = _accumulator_key(_THINKING_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_thinking_end_by_index.add(block_index)
                yield from self._handle_thinking_block(block_dict, stripped)
            elif block_type == "toolCall":
                yield from self._handle_toolcall_block(block_dict, stripped)
            elif block_type == "toolResult":
                yield from self._handle_toolresult_block(block_dict, stripped)

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

    def _handle_toolresult_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = block_dict.get("isError", False)
        result = block_dict.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool result error",
                raw=stripped,
                metadata=block_dict,
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata=block_dict,
        )

    def _get_text_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_TEXT_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]

    def _get_thinking_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_THINKING_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]


class PiParser(NdjsonParserBase):
    """Parser for pi.dev's AgentSessionEvent NDJSON streaming format.

    Text deltas are accumulated into coherent blocks before emission.
    The terminal snapshot (``text_end`` content or the ``message_end``
    message.content text block) is the authoritative final text; the
    parser tracks whether the terminal snapshot has already been
    emitted for a given content block (keyed by ``contentIndex``)
    to avoid duplicate emissions when streaming deltas, ``text_end``,
    and the ``message_end`` snapshot all reference the same content.

    Flushing happens on:

    - ``message_update`` with ``assistantMessageEvent.type == 'text_end'``
    - ``message_end`` (when no ``text_end`` was seen, the snapshot wins)
    - ``agent_end`` / ``turn_end`` (final flush via stop events)
    - Iterator exhaustion (final flush via ``flush_accumulators()``)

    Thinking deltas are accumulated in a SEPARATE accumulator with the
    same terminal-snapshot rules (``thinking_end`` content or
    ``message_end`` message.content thinking block).

    The single consistent isError rule:
    ``tool_execution_end.isError=true`` maps to ``type='error'``;
    ``isError=false`` (or absent) maps to ``type='tool_result'``.

    The ``message_end`` content array is walked for text, thinking,
    toolCall, and toolResult blocks.  Text and thinking blocks honor
    the per-block terminal-snapshot rule: if the corresponding
    ``*_end`` snapshot was already emitted for a given
    ``contentIndex``, the ``message_end`` block at that block index
    is skipped.  Other text/thinking blocks (whose ``contentIndex``
    has NOT had a terminal snapshot yet) are emitted.  The toolCall
    block is ALWAYS emitted (per the plan) so downstream consumers
    see the same logical tool call in the same place they see text
    and thinking content from ``message_end``.

    Inherits from :class:`NdjsonParserBase` which owns the 6 shared
    NDJSON behaviors.  The subclass ``_dispatch_json_object`` delegates
    to :class:`_PiDispatch` for the per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        super().__init__()
        self._accumulators: dict[str, TextAccumulator] = {}
        self.saw_text_end_by_index: set[int] = set()
        self.saw_thinking_end_by_index: set[int] = set()
        self._dispatcher = _PiDispatch(self)

    def reset_emission_flags(self) -> None:
        """Clear per-message terminal-snapshot tracking.

        Called after a stop event (``agent_end`` / ``turn_end`` /
        ``done``) or after ``message_end`` so the next message can
        emit its own terminal snapshots.  The set is keyed by the
        ``contentIndex`` of the streaming ``*_end`` event so the
        next ``message_end`` can distinguish blocks that were
        already terminalised (skip) from blocks that still need
        to be emitted (emit).
        """
        self.saw_text_end_by_index = set()
        self.saw_thinking_end_by_index = set()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        """Override the base lifecycle hook to fall through to dispatch.

        Pi's documented event vocabulary (per
        https://pi.dev/docs/latest/json) includes ``message_start``,
        which is in the shared :data:`LIFECYCLE_EVENT_TYPES` frozenset.
        To honor the AC-04 invariant that EVERY pi event reaches
        :meth:`_dispatch_json_object`, this hook returns ``None`` so
        the base layer falls through to the dispatch table; the
        dispatcher's :data:`_PI_SILENT_TOP_LEVEL_EVENTS` membership
        then decides whether the event is silent (``message_start``,
        ``agent_start``, ``turn_start``) or yields typed output.
        """
        return None

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
            if key.startswith(_THINKING_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_THINKING_KIND, require_strip=True)
            elif key.startswith(_TEXT_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_TEXT_KIND)
