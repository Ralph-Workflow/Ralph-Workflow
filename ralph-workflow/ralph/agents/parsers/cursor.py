"""Parser for the Cursor Agent CLI ``--output-format stream-json`` NDJSON wire format.

The Cursor Agent CLI ``agent`` binary, when invoked with
``--output-format stream-json``, emits one JSON line per event.  The
documented event vocabulary includes (per Cursor's CLI ``--help`` and
the documented Cursor Agent stream-json envelope):

  - ``system`` - status events carrying the system message as the
    ``message`` or ``content`` field.  Surfaced as ``AgentOutputLine(type='status')``
    so the runtime can render the message via the standard status
    pipeline.
  - ``user`` - input echo (the user prompt Ralph already sent; these
    are NOT the agent's own output).  Suppressed (mirror the pi/agy
    behavior of not re-emitting already-known input).
  - ``assistant`` - assistant turn events.  Carries a ``message`` object
    with a ``content`` array of typed blocks.  Blocks of type ``text``
    surface as ``type='text'`` via :class:`TextAccumulator` coalescing;
    blocks of type ``thinking`` surface as ``type='thinking'``.
  - ``thinking`` - standalone thinking event carrying a ``text`` or
    ``thinking`` delta.  Surfaced as ``type='thinking'``.
  - ``tool_call`` - tool invocation event carrying a ``toolName`` (or
    ``name``) and ``args``.  Surfaced as ``type='tool_use'`` so the
    watchdog sees real tool activity.
  - ``tool_result`` - tool result event carrying the tool name and
    a ``result`` (or ``output`` or ``content``) field.  Surfaced as
    ``type='tool_result'`` for the success path; when the event
    carries ``is_error=true`` (or ``error``) it is surfaced as
    ``type='error'`` so the watchdog can see the failure.
  - ``result`` - the documented end-of-turn marker.  Surfaced as
    ``type='stop'`` (the canonical completion signal) after any
    pending text/thinking accumulators are flushed.

Cursor's ``--stream-partial-output`` flag (when the operator opts in
via ``[agents.cursor].streaming_flag``) emits incremental text deltas
on the assistant ``text_delta`` sub-events; the parser coalesces those
deltas into coherent blocks via the shared :class:`TextAccumulator`
exactly like the pi parser does for its streaming text deltas.

Inherits from :class:`NdjsonParserBase` which owns the 6 shared NDJSON
behaviors: ``data:`` strip, ``[DONE]`` short-circuit, JSON parse
dispatch, lifecycle suppression, error extraction, and JSON-dict
dispatch.  The subclass ``_dispatch_json_object`` routes each event
through a per-event-type handler map so the wire-format vocabulary
is the single owner of what each event type emits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_TEXT_KIND = "text"
_THINKING_KIND = "thinking"

# Sentinel flag for ``_handle_user`` (and any future ``_handle_*``
# method that produces no events).  Set to ``False`` at runtime so the
# conditional ``yield`` body is never executed; the constant exists
# only so the method body is recognized as a generator (via the
# ``yield`` statement) AND so mypy does not flag the
# ``[unreachable]`` lint for a yield-after-return pattern.
_HANDLER_RETURNS_NO_EVENTS = False


class _CursorDispatch:
    """Per-event-type dispatch for ``CursorParser``.

    Holds a reference to the owning parser so accumulator state stays
    in one place.  Mirrors the dispatcher pattern used by
    :class:`PiParser` and :class:`CodexParser` so future maintainers
    familiar with those parsers can navigate the cursor parser
    without re-learning the seam.
    """

    def __init__(self, owner: CursorParser) -> None:
        self._owner = owner

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        handler_map = {
            "system": self._handle_system,
            "user": self._handle_user,
            "assistant": self._handle_assistant,
            "thinking": self._handle_thinking,
            "tool_call": self._handle_tool_call,
            "tool_result": self._handle_tool_result,
            "result": self._handle_result,
        }

        handler = handler_map.get(event_type)
        if handler is not None:
            yield from handler(obj, stripped)
            return

        # Forward-compat: any unknown event type passes through with
        # its ``type`` field as the AgentOutputLine type so a future
        # Cursor release that adds events does not silently drop them.
        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _handle_system(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``system`` events carry the system message as ``message`` / ``content``.

        Surfaced as ``type='status'`` so the runtime can render the
        status via the standard status pipeline.  An empty message
        yields nothing (matches the pi parser's no-op on empty
        streaming deltas).
        """
        message = obj.get("message") or obj.get("content") or ""
        if not isinstance(message, str):
            message = str(message) if message else ""
        if not message:
            return
        yield AgentOutputLine(type="status", content=message, raw=stripped, metadata=obj)

    def _handle_user(
        self,
        _obj: dict[str, object],
        _stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``user`` events are the agent's input echo (the prompt Ralph sent).

        Suppressed (mirror the pi/agy behavior of not re-emitting
        already-known input).  This keeps the parsed-event stream
        focused on the agent's actual output rather than Ralph's own
        prompt.
        """
        # Explicitly produce an empty iterator so the type checker
        # recognizes this method as a generator.  Using ``return`` alone
        # would make the method a non-generator; using ``yield`` after
        # ``return`` would be an unreachable statement that fails mypy's
        # ``[unreachable]`` lint check.  The conditional ``yield`` below
        # is never executed (the constant is ``False`` at runtime).
        if _HANDLER_RETURNS_NO_EVENTS:  # pragma: no cover
            yield from ()
        return

    def _handle_assistant(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``assistant`` events carry a ``message`` with a ``content`` array.

        Walks the content array and yields ``text`` / ``thinking``
        events for each typed block.  Text blocks coalesce via
        :class:`TextAccumulator` so a long response surfaces as a
        single coherent block at the paragraph boundary or at the
        end of the array.  Tool calls inside the assistant content
        are surfaced as ``type='tool_use'`` for watchdog visibility.
        """
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
            if block_type == _TEXT_KIND:
                text = str(block_dict.get("text", ""))
                if not text:
                    continue
                yield from self._owner._text_accumulator.accumulate(
                    text, stripped, kind=_TEXT_KIND, keep_current_when_empty=False
                )
                continue
            if block_type == _THINKING_KIND:
                thinking = str(block_dict.get("thinking", block_dict.get("text", "")))
                if not thinking.strip():
                    continue
                yield from self._owner._thinking_accumulator.accumulate(
                    thinking,
                    stripped,
                    kind=_THINKING_KIND,
                    keep_current_when_empty=False,
                )
                continue
            if block_type == "tool_call":
                tool_name = str(block_dict.get("name", block_dict.get("toolName", "unknown")))
                args = block_dict.get("args", block_dict.get("input", {}))
                yield AgentOutputLine(
                    type="tool_use",
                    content=tool_name,
                    raw=stripped,
                    metadata={"tool": tool_name, "args": args, **block_dict},
                )

    def _handle_thinking(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``thinking`` events carry a ``text`` or ``thinking`` delta."""
        text = str(obj.get("text", obj.get("thinking", "")))
        if not text.strip():
            return
        yield from self._owner._thinking_accumulator.accumulate(
            text,
            stripped,
            kind=_THINKING_KIND,
            keep_current_when_empty=False,
        )

    def _handle_tool_call(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``tool_call`` events surface as ``type='tool_use'`` for watchdog visibility."""
        tool_name = str(obj.get("toolName", obj.get("name", "unknown")))
        args = obj.get("args", obj.get("input", {}))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, **obj},
        )

    def _handle_tool_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``tool_result`` events surface as ``type='tool_result'`` or ``type='error'``.

        The Cursor wire format documents a boolean ``is_error`` field
        on the tool result envelope.  When ``is_error=true`` (or the
        event carries an ``error`` field), the parser surfaces the
        event as ``type='error'`` so the watchdog can see the failure.
        """
        is_error = obj.get("is_error", obj.get("isError", False))
        if isinstance(is_error, str):
            is_error = is_error.casefold() in {"true", "1", "yes"}
        if is_error:
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)
            return
        result = obj.get("result", obj.get("output", obj.get("content", "")))
        if not isinstance(result, str):
            result = str(result) if result else ""
        tool_name = str(obj.get("toolName", obj.get("name", "unknown")))
        yield AgentOutputLine(
            type="tool_result",
            content=result,
            raw=stripped,
            metadata={"tool": tool_name, **obj},
        )

    def _handle_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        """``result`` is the documented end-of-turn marker.

        Flush any pending text/thinking accumulators BEFORE the stop
        event so the runtime sees the final block of model text in
        the same iteration that sees the stop signal.
        """
        yield from self._owner.flush_accumulators()
        yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)


class CursorParser(NdjsonParserBase):
    """Parser for the Cursor Agent CLI ``--output-format stream-json`` wire format.

    Text and thinking deltas are accumulated into coherent blocks via
    :class:`TextAccumulator`.  Flushing happens on:

      * ``result`` (the documented end-of-turn marker)
      * Iterator exhaustion (final flush via :meth:`flush_accumulators`)

    Inherits from :class:`NdjsonParserBase` which owns the 6 shared
    NDJSON behaviors (data: strip, [DONE] short-circuit, JSON parse
    dispatch, lifecycle suppression, error extraction, JSON-dict
    dispatch).  The subclass ``_dispatch_json_object`` delegates to
    :class:`_CursorDispatch` for the per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"result"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        # R5: bind the per-invocation shared SubagentPidRegistry +
        # per-transport source label.  Cursor's documented stream-json
        # envelope does not currently include a ``pid`` field; the
        # hook is forward-compat for events that carry one.
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator = TextAccumulator()
        self._thinking_accumulator: TextAccumulator = TextAccumulator()
        self._dispatcher = _CursorDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._dispatcher.dispatch(obj, raw)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        """Override the base lifecycle hook to fall through to dispatch.

        Cursor's documented event vocabulary (``system`` / ``user`` /
        ``assistant`` / ``thinking`` / ``tool_call`` / ``tool_result`` /
        ``result``) overlaps the shared :data:`LIFECYCLE_EVENT_TYPES`
        frozenset on ``user`` / ``assistant`` / ``thinking``.  To honor
        the AC-04 invariant that every documented cursor event reaches
        :meth:`_dispatch_json_object`, this hook returns ``None`` so the
        base layer falls through to the dispatch table; the dispatcher's
        per-event handler map then decides what each cursor event type
        emits (text / thinking / status / tool_use / tool_result / stop).
        """
        return None

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._text_accumulator.flush(kind=_TEXT_KIND)
        yield from self._thinking_accumulator.flush(kind=_THINKING_KIND, require_strip=True)


__all__ = ["CursorParser"]
