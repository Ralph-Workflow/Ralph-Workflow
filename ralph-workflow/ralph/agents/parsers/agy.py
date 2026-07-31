"""Parser for the AGY v1.1.9 ``--output-format stream-json`` wire format.

Source of truth: ``ralph-workflow/tmp/agy-source-of-truth.txt``.

AGY --print mode emits plain-text model responses on stdout, one line at a
time. The parser classifies every plain-text line as
``AgentOutputLine(type='text')`` (NOT ``type='raw'``) so the smoke report's
"Observed output:" section renders model content via ``_render_text_line``
(in ``ralph.pipeline.activity_stream``) instead of the literal ``raw`` type
label via ``_render_metadata_event_line``.

The parser inherits from :class:`NdjsonParserBase`, which owns the 6 shared
NDJSON behaviours: ``data:`` SSE prefix strip, ``[DONE]`` short-circuit,
non-dict-JSON-to-raw fallback, lifecycle-event suppression, error
extraction, and JSON-dict dispatch. AGY v1.0.8 --print mode does NOT emit
JSON lifecycle or error events; the inherited behaviour is preserved as a
safe default for any future AGY --json flag.

The ``[plain] tool: NAME`` convention from :class:`GenericParser` is
intentionally NOT classified as ``tool_use`` here. That convention is a
GenericParser convention, not an AGY wire-format fact documented in the
source of truth. The smoke harness instead treats the expected workspace file
write as authoritative AGY tool activity; model-authored artifact claims are
never trusted for that check.

Behaviour specifics:

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


def _tool_update(step: dict[str, object]) -> tuple[str, dict[str, object], object] | None:
    """Return normalized AGY tool or subagent update fields."""
    step_type = step.get("step_type")
    info_key = "subagent_info" if step_type == "subagent" else "tool_info"
    raw_info = step.get(info_key)
    if not isinstance(raw_info, dict):
        return None
    info = cast("dict[str, object]", raw_info)
    if step_type == "subagent":
        subagents = info.get("subagents")
        if isinstance(subagents, list) and len(subagents) == 1 and isinstance(subagents[0], dict):
            details = cast("dict[str, object]", subagents[0])
            return "subagent", info, step.get("step_index") or details.get("conversation_id")
        return None
    tool_name = info.get("name")
    if step_type == "tool" and isinstance(tool_name, str) and tool_name:
        return tool_name, info, info.get("call_id") or info.get("id") or step.get("step_index")
    return None


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
        """Map AGY v1.1.9 stream-json events to normalized output events."""
        event = obj.get("event")
        if event == "result":
            yield from self._flush_text()
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
            yield AgentOutputLine(type=str(obj.get("type", "unknown")), raw=raw, metadata=obj)

    def _dispatch_step_update(self, step: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map one AGY incremental update to semantic parser events."""
        text_delta = step.get("text_delta")
        if isinstance(text_delta, str) and text_delta:
            if self._text_accumulator is None:
                self._text_accumulator = TextAccumulator()
            yield from self._text_accumulator.accumulate(
                text_delta, raw, kind="text", keep_current_when_empty=False
            )
            return
        tool_update = _tool_update(step)
        if tool_update is None:
            return
        tool_name, info, call_id = tool_update
        yield from self._flush_text()
        normalized_name = (
            "subagent" if tool_name in {"invoke_subagent", "define_subagent"} else tool_name
        )
        metadata: dict[str, object] = {"tool": normalized_name, "tool_info": info}
        if isinstance(call_id, str | int) and str(call_id):
            metadata["tool_use_id"] = str(call_id)
        if step.get("state") == "DONE":
            output = info.get("output", "")
            yield AgentOutputLine(
                type="tool_result", content=str(output), raw=raw, metadata=metadata
            )
            return
        yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=metadata)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Drain the text accumulator and yield the buffered text event."""
        yield from self._flush_text()
