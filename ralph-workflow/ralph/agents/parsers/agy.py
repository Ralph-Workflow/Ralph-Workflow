"""Parser for the AGY v1.1.10 ``--output-format stream-json`` wire format.

Source of truth: ``ralph-workflow/tmp/agy-source-of-truth.txt``.

The parser maps AGY v1.1.10 stream-json events (``init``, ``step_update``,
``result``) to normalized parser events (``text``, ``tool_use``, ``tool_result``,
``error``, ``stop``). For plain-text fallback streams, the parser classifies
plain-text lines as ``AgentOutputLine(type='text')`` (NOT ``type='raw'``) so the
smoke report and activity stream render model content clearly.

The parser inherits from :class:`NdjsonParserBase`, which owns the 6 shared
NDJSON behaviours: ``data:`` SSE prefix strip, ``[DONE]`` short-circuit,
non-dict-JSON-to-raw fallback, lifecycle-event suppression, error
extraction, and JSON-dict dispatch.

Behaviour specifics:

  * AGY ``step_update`` frames yield structured ``tool_use`` and ``tool_result``
    events, correlated by step index or call ID.
  * A single plain-text line is buffered, then emitted at iterator
    exhaustion (or at the next paragraph-boundary flush) as a single
    ``text`` event. This coalesces consecutive short lines into one
    coherent text block matching the GenericParser coalescing semantics.
  * The ``Task declared complete:`` marker is treated as plain text (not
    a structured completion signal). The smoke detector requires the durable
    run-scoped sentinel written by ``declare_complete``, so transcript text
    cannot spoof completion.
  * Empty input (the documented quota-exhausted failure mode in
    ``agy-source-of-truth.txt``) yields zero events, allowing the smoke
    plumbing to surface the empty-stdout diagnostic for the live case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.display.vt_normalizer import normalize_vt_text

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


__all__ = ["AgyParser"]


def _tool_updates(step: dict[str, object]) -> list[tuple[str, dict[str, object], object]]:
    """Return normalized AGY tool or subagent update tuples."""
    step_type = step.get("step_type")
    info_key = "subagent_info" if step_type == "subagent" else "tool_info"
    raw_info = step.get(info_key)
    if not isinstance(raw_info, dict):
        return []
    info = cast("dict[str, object]", raw_info)
    if step_type == "subagent":
        raw_subagents = info.get("subagents")
        if isinstance(raw_subagents, list):
            subagents = cast("list[object]", raw_subagents)
            step_index = step.get("step_index")
            multi = len(subagents) > 1
            results: list[tuple[str, dict[str, object], object]] = []
            for position, sub in enumerate(subagents):
                if isinstance(sub, dict):
                    details = cast("dict[str, object]", sub)
                    entry_id = details.get("conversation_id") or details.get("id")
                    # A shared step_index correlates ACTIVE -> DONE reliably
                    # when there is exactly one subagent per frame (the id may
                    # only appear on the DONE update); with multiple subagents
                    # in one frame, step_index alone collapses every entry
                    # onto the same id, so prefer each entry's own identity
                    # and fall back to a step_index + position composite.
                    if multi:
                        cid = entry_id or f"{step_index}:{position}"
                    else:
                        cid = step_index or entry_id
                    results.append(("subagent", details, cid))
            return results
        return []
    tool_name = info.get("name")
    if step_type == "tool" and isinstance(tool_name, str) and tool_name:
        cid = info.get("call_id") or info.get("id") or step.get("step_index")
        return [(tool_name, info, cid)]
    return []


def _extract_fallback_payload(obj: dict[str, object]) -> str | None:
    """Extract a compact human-readable text payload from an unrecognized JSON frame."""
    if not obj:
        return None
    data = obj.get("data")
    if isinstance(data, dict):
        msg = data.get("message") or data.get("text") or data.get("content")
        payload: object = msg if msg is not None else data
    elif data is not None:
        payload = data
    else:
        payload = next(
            (obj[k] for k in ("message", "text", "content", "error") if k in obj),
            None,
        )
        if payload is None:
            items = {k: v for k, v in obj.items() if k not in {"event", "type", "timestamp", "ts"}}
            payload = items if items else None
    return str(payload) if payload is not None else None


class AgyParser(NdjsonParserBase):
    """Parser for AGY stream-json output with a plain-text compatibility fallback.

    Inherits the NDJSON state machine from :class:`NdjsonParserBase` (SSE
    strip, ``[DONE]`` short-circuit, lifecycle suppression, error
    extraction, JSON-dict dispatch, non-dict-JSON-to-raw). Overrides
    :meth:`_classify_non_json_line` so the AGY --print plain-text stream is
    classified as ``type='text'`` and coalesced via
    :class:`TextAccumulator` into coherent blocks.
    """

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        # R5: bind the per-invocation shared SubagentPidRegistry + per-transport
        # source label. AGY's --print plain-text stream does not currently
        # carry embedded PIDs; this is forward-compat for the
        # per-transport SubagentPidSource seam.
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator | None = None
        self._has_prior_text_line: bool = False
        self._emitted_tool_use_ids: set[str] = set()  # bounded-accumulator-ok: bounded set for deduplicating tool_use events per parse run

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        """Classify an AGY plain-text line as ``type='text'`` and coalesce.

        VT normalization is applied first so ANSI-decorated lines (e.g.
        from a TUI run piped without a PTY) are classified consistently.

        Consecutive non-blank lines are joined with a single ``\\n``
        separator before being fed into the :class:`TextAccumulator` so
        the rendered text output preserves the line boundaries that the
        model emitted (the prior implementation concatenated lines
        without separators, producing merged output like
        ``I will create the todo list implementation.Using module.exports...``
        that glued words from different lines together).
        """
        normalized = normalize_vt_text(stripped).strip()
        if not normalized:
            return
        if self._text_accumulator is None:
            self._text_accumulator = TextAccumulator()
        chunk = f"\n{normalized}" if self._has_prior_text_line else normalized
        self._has_prior_text_line = True
        yield from self._text_accumulator.accumulate(
            chunk,
            stripped,
            kind="text",
            keep_current_when_empty=False,
        )

    def _flush_text(self) -> Iterator[AgentOutputLine]:
        """Drain pending text before a structured boundary."""
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        self._has_prior_text_line = False
        yield from acc.flush(kind="text")

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        """Map AGY v1.1.10 stream-json events to normalized output events."""
        event = obj.get("event")
        if event == "result":
            yield from self._flush_text()
            res = obj.get("result")
            if isinstance(res, dict):
                status = res.get("status")
                if isinstance(status, str) and status != "SUCCESS":
                    err_msg = str(res.get("error") or status)
                    yield AgentOutputLine(type="error", content=err_msg, raw=raw, metadata=obj)
            yield AgentOutputLine(type="stop", raw=raw, metadata=obj)
        elif event == "step_update":
            update = obj.get("step_update")
            if isinstance(update, dict):
                yield from self._dispatch_step_update(cast("dict[str, object]", update), raw)
        elif event == "init":
            return
        elif obj.get("type") == "tool_use":
            tool_name = obj.get("name")
            if isinstance(tool_name, str) and tool_name:
                yield AgentOutputLine(
                    type="tool_use", content=tool_name, raw=raw, metadata={"tool": tool_name, **obj}
                )
        else:
            payload = _extract_fallback_payload(obj)
            if payload:
                yield AgentOutputLine(type="text", content=payload, raw=raw, metadata=obj)

    def _dispatch_step_update(self, step: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map one AGY incremental update to semantic parser events."""
        text_delta = step.get("text_delta")
        if isinstance(text_delta, str) and text_delta:
            normalized_delta = normalize_vt_text(text_delta)
            if normalized_delta:
                if self._text_accumulator is None:
                    self._text_accumulator = TextAccumulator()
                yield from self._text_accumulator.accumulate(
                    normalized_delta, raw, kind="text", keep_current_when_empty=False
                )
            return
        tool_updates = _tool_updates(step)
        if not tool_updates:
            return
        yield from self._flush_text()
        for tool_name, info, call_id in tool_updates:
            normalized_name = (
                "subagent" if tool_name in {"invoke_subagent", "define_subagent"} else tool_name
            )
            metadata: dict[str, object] = {"tool": normalized_name, "tool_info": info}
            use_id: str | None = None
            if isinstance(call_id, str | int) and str(call_id):
                use_id = str(call_id)
                metadata["tool_use_id"] = use_id

            if step.get("state") == "DONE":
                output = info.get("output", "")
                yield AgentOutputLine(
                    type="tool_result", content=str(output), raw=raw, metadata=metadata
                )
            else:
                if use_id:
                    if use_id in self._emitted_tool_use_ids:
                        continue
                    self._emitted_tool_use_ids.add(use_id)
                yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Drain the text accumulator and yield the buffered text event."""
        yield from self._flush_text()
