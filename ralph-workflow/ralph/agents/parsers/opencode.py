"""Parser for OpenCode's NDJSON streaming format."""

from __future__ import annotations

import contextlib
from collections import deque
from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message, stringify_text_blocks
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


class _OpenCodeDispatch:
    """Per-event-type dispatch for OpenCodeParser.

    Encapsulates the historical event-routing logic that used to live in
    ``_parse_object``.  The subclass ``_dispatch_json_object`` delegates
    here for all non-lifecycle, non-error events.  Holds a reference to
    the owning parser so accumulator state stays in one place.
    """

    def __init__(self, owner: OpenCodeParser) -> None:
        self._owner = owner

    @staticmethod
    def _canonical_tool_name(raw_tool_name: str) -> str:
        """Normalize OpenCode's ``ralph_*`` tool names to the canonical form.

        The live 1.18.14 wire format emits tool names like
        ``ralph_read_file`` / ``ralph_write_file`` / ``ralph_edit_file``;
        the transport-neutral preview payload builder keys off
        ``read_file`` / ``write_file`` / ``edit_file``. This is the
        single normalization point at the transport boundary so the
        display and the preview builder see one canonical name.
        A name that does not start with ``ralph_`` is returned unchanged
        (e.g. Claude-shaped ``Read`` / ``Write`` / ``Edit`` input is
        preserved as-is).
        """
        if raw_tool_name.startswith("ralph_"):
            return raw_tool_name[len("ralph_") :]
        return raw_tool_name

    def dispatch(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type == "step_start":
            # NOTE: OpenCode 1.17.15 emits exactly five event types --
            # step_start, step_finish, text, tool_use, error -- and carries the
            # part id at ``part.id``, not at the top level. So this lookup
            # always misses on the live runtime and the accumulator machinery
            # below never engages. That is currently harmless because the
            # ``stream`` deltas it accumulates do not exist either; text
            # arrives whole in one ``text`` event. Do NOT "fix" this by
            # reading ``part.id``: the step-start part id is a DIFFERENT part
            # from the text part the deltas would belong to, so it would key
            # the accumulator wrongly. Both halves must be reworked together
            # against a runtime that actually streams.
            step_id = str(obj.get("id", ""))
            if step_id:
                self._owner._current_part_id = step_id
            return
        if event_type == "step_finish":
            current = self._owner._current_part_id
            accumulators = self._owner._accumulators
            if current and current in accumulators:
                yield from self._flush_accumulator(current)
            self._owner._current_part_id = None
            return
        if event_type == "done":
            yield from self._owner.flush_accumulators()
            self._owner._current_part_id = None
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        part_obj = obj.get("part")
        part: dict[str, object] = {}
        if isinstance(part_obj, dict):
            part = cast("dict[str, object]", part_obj)

        handler_map = {
            "stream": self._parse_stream,
            "text": self._parse_text,
            "error": self._parse_error,
            "tool_use": self._parse_tool_use,
            "tool_result": self._parse_tool_result,
        }

        handler = handler_map.get(event_type)
        if handler:
            yield from handler(obj, part, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        accumulators = self._owner._accumulators
        if key not in accumulators:
            return
        acc = accumulators.pop(key)
        yield from acc.flush(kind="text")

    def _parse_stream(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content = obj.get("content", "")
        if not isinstance(content, str) or not content:
            return

        part_id = self._owner._current_part_id
        if part_id is None:
            yield AgentOutputLine(type="text", content=content, raw=raw)
            return

        key = part_id
        accumulators = self._owner._accumulators
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        yield from accumulators[key].accumulate(
            content, raw, kind="text", keep_current_when_empty=True
        )

    def _parse_text(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        part_text = part.get("text")
        if isinstance(part_text, str) and part_text:
            yield AgentOutputLine(type="text", content=part_text, raw=raw, metadata=obj)
            return

        content = obj.get("content", "")
        if isinstance(content, str) and content:
            yield AgentOutputLine(type="text", content=content, raw=raw, metadata=obj)
            return

        yield AgentOutputLine(type="text", raw=raw, metadata=obj)

    def _parse_error(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_tool_use(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        raw_tool_name = str(part.get("tool", obj.get("tool", "unknown")))
        canonical_tool_name = self._canonical_tool_name(raw_tool_name)
        state_obj = part.get("state")
        metadata = self._tool_metadata(obj, part)

        if not isinstance(state_obj, dict):
            yield AgentOutputLine(
                type="tool_use", content=canonical_tool_name, raw=raw, metadata=metadata
            )
            return

        status = str(state_obj.get("status", ""))
        if status == "completed":
            # OpenCode collapses the call and its result into ONE terminal
            # event, so a completed tool carries BOTH. A preceding ``running``
            # event already exposed the dispatch, however; emit it only once
            # per call ID while still retaining the terminal result.
            if not self._owner._tool_call_was_dispatched(part):
                yield AgentOutputLine(
                    type="tool_use",
                    content=canonical_tool_name,
                    raw=raw,
                    metadata=metadata,
                )
            self._owner._finish_tool_call(part)
            output = state_obj.get("output", "")
            yield AgentOutputLine(
                type="tool_result",
                content=stringify_text_blocks(output),
                raw=raw,
                metadata=metadata,
            )
            return

        if status == "error":
            # Keep an errored dispatch visible, but do not duplicate a prior
            # running event for the same call.
            if not self._owner._tool_call_was_dispatched(part):
                yield AgentOutputLine(
                    type="tool_use",
                    content=canonical_tool_name,
                    raw=raw,
                    metadata=metadata,
                )
            self._owner._finish_tool_call(part)
            err = str(state_obj.get("error", "tool error"))
            yield AgentOutputLine(type="error", content=err, raw=raw, metadata=metadata)
            return

        self._owner._record_tool_dispatch(part)
        yield AgentOutputLine(
            type="tool_use", content=canonical_tool_name, raw=raw, metadata=metadata
        )

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        metadata = self._tool_metadata(obj, part)
        if "result" in obj:
            result = stringify_text_blocks(obj.get("result", ""))
        else:
            state_obj = part.get("state")
            result = (
                stringify_text_blocks(state_obj.get("output", ""))
                if isinstance(state_obj, dict)
                else ""
            )
        yield AgentOutputLine(type="tool_result", content=result, raw=raw, metadata=metadata)

    def _tool_metadata(
        self,
        obj: dict[str, object],
        part: dict[str, object],
    ) -> dict[str, object]:
        metadata = dict(obj)
        raw_tool_name = part.get("tool", obj.get("tool"))
        if isinstance(raw_tool_name, str) and raw_tool_name:
            # Normalize OpenCode's ``ralph_*`` tool names at the
            # transport boundary so the canonical preview payload
            # builder recognizes them. The live 1.18.14 wire format
            # emits tool names like ``ralph_read_file`` /
            # ``ralph_write_file`` / ``ralph_edit_file``; the
            # transport-neutral preview payload builder keys off
            # ``read_file`` / ``write_file`` / ``edit_file``. Preserve
            # the raw name in ``tool_raw`` for diagnostics so an
            # operator can still trace the originating wire bytes.
            tool_name = self._canonical_tool_name(raw_tool_name)
            metadata["tool"] = tool_name
            metadata["tool_raw"] = raw_tool_name

        call_id = self._owner._tool_call_id(part)
        if call_id is not None:
            # Downstream consumers use one transport-neutral call identity for
            # dispatch/result correlation. Preserve the native ``part`` for
            # diagnostics, but normalize both OpenCode spellings here at the
            # transport boundary so smoke evidence and display records do not
            # each need to understand ``callID`` versus ``callId``.
            metadata["call_id"] = call_id

        input_obj = part.get("input")
        if isinstance(input_obj, dict):
            metadata["input"] = input_obj
            return metadata

        state_obj = part.get("state")
        if isinstance(state_obj, dict):
            nested_input = state_obj.get("input")
            if isinstance(nested_input, dict):
                metadata["input"] = nested_input

        return metadata


class OpenCodeParser(NdjsonParserBase):
    """Parser for OpenCode's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``step_finish`` / ``done`` (end of step/message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``flush_accumulators()``)

    Inherits from :class:`NdjsonParserBase` which owns the
    ``data:`` strip, ``[DONE]`` short-circuit, JSON parse dispatch,
    lifecycle suppression, and error extraction.  The subclass
    ``_dispatch_json_object`` delegates to ``_OpenCodeDispatch`` for the
    per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"step_start", "step_finish", "done"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        # R5: bind the per-invocation shared SubagentPidRegistry + per-transport
        # source label. The OpenCode strategy ingests structured
        # child-lifecycle events (child_started/child_progress/
        # child_heartbeat/child_complete) into a ChildLivenessRegistry, and the
        # parser-side registry hook is forward-compat for events that carry an
        # embedded PID. Verified against OpenCode 1.17.15: the live runtime
        # emits NONE of those types and no event carries a PID, so both hooks
        # are inert today -- real subagent dispatch arrives as a ``task`` tool
        # call and is classified by ``_opencode_tool_signal`` instead.
        self._subagent_pid_registry = subagent_pid_registry
        self._subagent_source_label = subagent_source_label
        self._accumulators: dict[str, TextAccumulator] = {}  # bounded-accumulator-ok: drained
        self._dispatched_tool_call_ids: deque[str] = deque(maxlen=256)
        self._current_part_id: str | None = None
        self._stream_counter = 0
        self._dispatcher = _OpenCodeDispatch(self)

    def _flush_before_error(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Drain streamed text before the base emits a terminal error."""
        yield from self.flush_accumulators()
        self._current_part_id = None

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        """Handle OpenCode framing while keeping it out of visible output."""
        if event_type == "step_start":
            step_id = obj.get("id")
            if not isinstance(step_id, str):
                part = obj.get("part")
                step_id = part.get("id") if isinstance(part, dict) else None
            if isinstance(step_id, str) and step_id:
                self._current_part_id = step_id
        elif event_type in {"step_finish", "done"}:
            return self._flush_lifecycle(event_type)
        return iter(())

    def _flush_lifecycle(self, event_type: str) -> Iterator[AgentOutputLine]:
        """Flush pending text and emit stop only for OpenCode's terminal event."""
        if event_type == "step_finish":
            current = self._current_part_id
            if current and current in self._accumulators:
                yield from self._dispatcher._flush_accumulator(current)
            self._current_part_id = None
            return
        yield from self.flush_accumulators()
        self._current_part_id = None
        yield AgentOutputLine(type="stop", raw=event_type, metadata={"type": event_type})

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        # DA-002 (wt-028-display S-2 / AC-01): the base class
        # post-processes the iterator to attach ``source_timestamp``
        # to any AgentOutputLine that lacks one, so the per-event
        # dispatcher itself does not need to thread the parameter.
        del source_timestamp  # accepted for override compatibility; ignored
        yield from self._dispatcher.dispatch(obj, raw)

    @staticmethod
    def _tool_call_id(part: dict[str, object]) -> str | None:
        """Return OpenCode's native tool-call identity when present."""
        for key in ("callID", "callId"):
            value = part.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _record_tool_dispatch(self, part: dict[str, object]) -> None:
        """Remember an in-flight tool dispatch so its terminal frame is not duplicated."""
        call_id = self._tool_call_id(part)
        if call_id is not None and call_id not in self._dispatched_tool_call_ids:
            self._dispatched_tool_call_ids.append(call_id)

    def _tool_call_was_dispatched(self, part: dict[str, object]) -> bool:
        """Return whether this terminal frame follows a streamed dispatch."""
        call_id = self._tool_call_id(part)
        return call_id is not None and call_id in self._dispatched_tool_call_ids

    def _finish_tool_call(self, part: dict[str, object]) -> None:
        """Forget a terminal call ID so a future call cannot be suppressed."""
        call_id = self._tool_call_id(part)
        if call_id is not None:
            with contextlib.suppress(ValueError):
                self._dispatched_tool_call_ids.remove(call_id)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            if key not in self._accumulators:
                continue
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")
