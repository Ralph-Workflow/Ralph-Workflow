"""Parser for OpenCode's NDJSON streaming format."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message, stringify_text_blocks
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


def _part_id_of(obj: dict[str, object]) -> str:
    """Return ``part.id`` for an OpenCode event, or ``""`` when absent."""
    part = obj.get("part")
    if not isinstance(part, dict):
        return ""
    part_id = cast("dict[str, object]", part).get("id")
    return part_id.strip() if isinstance(part_id, str) else ""


class _OpenCodeDispatch:
    """Per-event-type dispatch for OpenCodeParser.

    Encapsulates the historical event-routing logic that used to live in
    ``_parse_object``.  The subclass ``_dispatch_json_object`` delegates
    here for all non-lifecycle, non-error events.  Holds a reference to
    the owning parser so accumulator state stays in one place.
    """

    def __init__(self, owner: OpenCodeParser) -> None:
        self._owner = owner

    def dispatch(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type == "step_start":
            # OpenCode carries the part id at ``part.id``; there is no
            # top-level ``id`` on any event it emits. Reading only the top
            # level left ``_current_part_id`` permanently ``None``, which
            # silently disabled the whole delta-accumulation path below.
            step_id = str(obj.get("id", "")) or str(_part_id_of(obj))
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
        tool_name = str(part.get("tool", obj.get("tool", "unknown")))
        state_obj = part.get("state")
        metadata = self._tool_metadata(obj, part)

        if not isinstance(state_obj, dict):
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)
            return

        status = str(state_obj.get("status", ""))
        if status == "completed":
            # OpenCode collapses the call and its result into ONE terminal
            # event, so a completed tool carries BOTH. Emitting only the
            # ``tool_result`` erased the dispatch: consumers that count
            # dispatches by ``type == "tool_use"`` (e.g.
            # ``_subagent_smoke_evidence``) saw zero, and a real ``task``
            # subagent run was reported as "subagent dispatch was not
            # observed". Surface the dispatch first, then the result, so the
            # ordered dispatch -> result -> post-activity lifecycle holds.
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)
            output = state_obj.get("output", "")
            yield AgentOutputLine(
                type="tool_result",
                content=stringify_text_blocks(output),
                raw=raw,
                metadata=metadata,
            )
            return

        if status == "error":
            # Same reasoning as the ``completed`` branch above: the dispatch
            # itself is real and MUST stay visible. Emitting only the error
            # erased the call from the tool timeline, so an errored ``task``
            # dispatch reported "subagent dispatch was not observed" even
            # though the subagent was genuinely dispatched.
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)
            err = str(state_obj.get("error", "tool error"))
            yield AgentOutputLine(type="error", content=err, raw=raw, metadata=metadata)
            return

        yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)

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
        tool_name = part.get("tool", obj.get("tool"))
        if isinstance(tool_name, str) and tool_name:
            metadata["tool"] = tool_name

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
        self._current_part_id: str | None = None
        self._stream_counter = 0
        self._dispatcher = _OpenCodeDispatch(self)

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

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            if key not in self._accumulators:
                continue
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")
