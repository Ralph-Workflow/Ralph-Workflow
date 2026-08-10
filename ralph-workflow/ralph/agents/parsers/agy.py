"""Parser for the AGY v1.1.10 ``--output-format stream-json`` wire format.

Source of truth: ``tests/display/_fixtures/agy_wire_provenance.md``. That file
records the measured live-binary observations this parser is built against:
the PTY capture method, the exact probe prompts, the observed
``init`` / ``step_update`` / ``result`` / ``error`` frame vocabulary, the PTY
requirement (no controlling terminal -> empty stdout), and the real
empty-output stderr message.

The parser maps AGY v1.1.10 stream-json events (``init``, ``step_update``,
``result``, ``error``) to normalized parser events (``text``, ``tool_use``,
``tool_result``, ``error``, ``lifecycle``, ``stop``). For plain-text fallback
streams, the parser classifies plain-text lines as ``AgentOutputLine(type='text')``
(NOT ``type='raw'``) so the smoke report and activity stream render model
content clearly.

The parser inherits from :class:`NdjsonParserBase`, which owns the 6 shared
NDJSON behaviours: ``data:`` SSE prefix strip, ``[DONE]`` short-circuit,
non-dict-JSON-to-raw fallback, lifecycle-event suppression, error
extraction, and JSON-dict dispatch.

Behaviour specifics (measured against the live v1.1.10 binary):

  * ``init`` frames carry ``model``, ``cwd``, ``tools``, and
    ``permission_mode`` under an ``init`` object, plus a top-level
    ``conversation_id``. These are surfaced as a ``type='lifecycle'`` event
    (routed by ``activity_router.map_parser_type_to_kind`` as lifecycle, so it
    stays out of the operator-visible text stream) whose metadata carries all
    four fields plus ``conversation_id``. ``ralph.agents.invoke._session``
    reads the raw JSON line directly for session-id extraction and is
    unaffected by this parser's classification.
  * ``step_update`` frames yield structured ``tool_use`` and ``tool_result``
    events. Ordinary tools (``step_type == "tool"``) correlate ACTIVE/DONE by
    ``tool_info.call_id`` / ``tool_info.id`` when present, else by
    ``step_index`` (guarded against the falsy ``0`` step index). A genuine
    ``tool_info.call_id`` / ``.id`` is surfaced as ``metadata["tool_use_id"]``;
    a ``step_index`` fallback is NOT a genuine upstream call id, so it is
    surfaced as ``metadata["step_ordinal"]`` instead — downstream renderers
    read ``tool_use_id`` as ``call_id=N``, which would misleadingly claim a
    synthesized ordinal as an upstream identifier. Subagent entries
    (``step_type == "subagent"``) correlate by a ``f"{step_index}:{position}"``
    composite id that is stable across ACTIVE and DONE regardless of how many
    subagent entries share one frame — real AGY adds each entry's own
    ``conversation_id`` only on the DONE update, so an id scheme keyed on that
    field would not correlate the ACTIVE dispatch. That composite is a
    purpose-built correlation key rather than a raw field standing in for
    one, so it keeps using ``metadata["tool_use_id"]``.
  * ``step_update`` frames whose ``step_type`` is ``user_input`` / ``unknown``
    / ``checkpoint`` carry no ``text_delta`` and no ``tool_info`` /
    ``subagent_info`` of their own. Rather than being silently dropped, each
    yields a non-empty ``type='lifecycle'`` event summarizing the step_type.
    A bodiless ``agent_response`` DONE frame's ``usage`` (no ``text_delta``,
    e.g. the measured one-shot ``OK`` capture) is likewise never discarded:
    it is carried forward to the next flushed ``text`` event when text is
    already buffered, or, if no further flush is coming, surfaces directly as
    its own ``lifecycle`` event.
  * Duration values (``duration_seconds`` on a tool/step completion or a
    ``result`` frame) are formatted to 2 decimal places (e.g. ``"0.08s"``)
    rather than interpolated raw, since AGY's measured ``duration_seconds``
    can carry up to 9 decimal digits of floating-point noise
    (e.g. ``0.076075017``).
  * A tool's ``tool_use`` content includes a short summary of its most
    identifying parameter (e.g. ``write_to_file hello.txt``); a subagent's
    ``tool_use`` content is its ``role`` (e.g. ``Write File A``) so two
    concurrent subagents remain distinguishable. When a DONE frame carries no
    ``tool_info.output`` (common for AGY: many tools, and every subagent DONE,
    never carry one), the emitted ``tool_result`` content is a non-empty
    completion summary derived from the tool name, its identifying parameter,
    and ``duration_seconds`` — never an empty string.
  * ``result`` frames keep the ``stop`` event and additionally lift
    ``status``, ``num_turns``, ``duration_seconds``, and ``usage`` out of the
    nested ``result`` dict onto the top level of the event metadata (the
    nested copy under ``metadata["result"]`` is preserved for back-compat).
    The ``stop`` event's content is a non-empty summary built from those
    fields (e.g. ``"agy result SUCCESS (3.58s, 1 turn)"``), falling back to
    ``"agy result"`` when the nested ``result`` dict is absent, so it never
    renders as a bodiless lifecycle line. ``response`` is intentionally not
    re-emitted as text: its content already arrived incrementally via
    ``text_delta``.
  * ``step_update`` frames of ``step_type == "agent_response"`` carry
    incremental ``text_delta`` chunks; a DONE frame's ``usage`` (token counts)
    is carried onto the metadata of the eventual flushed ``text`` event
    (the accumulator only flushes at a later structural boundary, so the
    usage is held pending until then).
  * A single plain-text line is buffered, then emitted at iterator
    exhaustion (or at the next paragraph-boundary flush) as a single
    ``text`` event. This coalesces consecutive short lines into one
    coherent text block matching the GenericParser coalescing semantics.
    Every flushed ``text`` event has surrounding whitespace stripped so no
    trailing newline from a ``text_delta`` chunk reaches the display and no
    whitespace-only buffer is ever emitted as an empty ``text`` event.
  * An ``event: "error"`` frame with **no** ``error`` key (the exact live
    payload shape for AGY's ``EmitError`` was not captured) is classified as
    ``type='error'`` by this parser. Frames that DO carry an ``error`` key are
    already intercepted by ``NdjsonParserBase._classify_parsed_json_object``
    before ``_dispatch_json_object`` runs, so no duplicate handling is added
    here for that shape.
  * The ``Task declared complete:`` marker is treated as plain text (not
    a structured completion signal). The smoke detector requires the durable
    run-scoped sentinel written by ``declare_complete``, so transcript text
    cannot spoof completion.
  * Empty input (the documented quota-exhausted failure mode, and the
    documented permission-auto-deny / PTY-less failure modes) yields zero
    events, allowing the smoke plumbing to surface the empty-stdout
    diagnostic for the live case.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from ralph.display.vt_normalizer import normalize_vt_text

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


__all__ = ["AgyParser"]

# The single most identifying parameter is picked in this preference order;
# the first matching string-valued key wins, falling back to the first
# string-valued parameter present for tools that use a different key name.
_IDENTIFYING_PARAM_KEYS: tuple[str, ...] = ("TargetFile", "path", "file_path", "command", "query")
_PARAM_SUMMARY_MAX_LEN = 60

# B3/B4: the documented ``step_type`` vocabulary (see the module docstring
# and tests/display/_fixtures/agy_wire_provenance.md) that carries no body
# of its own -- no text_delta, no tool_info, no subagent_info. These are no
# longer silently dropped by ``_dispatch_step_update``; each yields an
# explicit non-empty ``lifecycle`` event instead.
_LIFECYCLE_STEP_TYPES: frozenset[str] = frozenset({"user_input", "unknown", "checkpoint"})


def _truncate(value: str) -> str:
    """Truncate ``value`` to the parameter-summary length budget."""
    if len(value) <= _PARAM_SUMMARY_MAX_LEN:
        return value
    return value[: _PARAM_SUMMARY_MAX_LEN - 1] + "…"


def _basename_if_path(value: str) -> str:
    """Return the trailing path segment when ``value`` looks like a file path."""
    if "/" in value or "\\" in value:
        tail = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return tail or value
    return value


def _parameter_summary(info: dict[str, object]) -> str | None:
    """Return a short, truncated summary of the tool's most identifying parameter."""
    params = info.get("parameters")
    if not isinstance(params, dict):
        return None
    params_dict = cast("dict[str, object]", params)
    for key in _IDENTIFYING_PARAM_KEYS:
        value = params_dict.get(key)
        if isinstance(value, str) and value:
            return _truncate(_basename_if_path(value))
    for value in params_dict.values():
        if isinstance(value, str) and value:
            return _truncate(_basename_if_path(value))
    return None


def _format_duration(seconds: float) -> str:
    """Return a human-readable duration string rounded to 2 decimal places (B2).

    AGY's ``duration_seconds`` is a raw float that can carry up to 9
    decimal digits of floating-point noise (e.g. ``0.076075017``);
    operators need a stable, readable duration, not a float-precision
    artifact.
    """
    return f"{seconds:.2f}s"


def _completion_summary(label: str, step: dict[str, object], info: dict[str, object]) -> str:
    """Return a non-empty completion summary for a DONE frame with no ``output``."""
    parts = [label]
    param_summary = _parameter_summary(info)
    if param_summary and param_summary not in label:
        parts.append(param_summary)
    duration = step.get("duration_seconds")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        parts.append(f"({_format_duration(duration)})")
    return " ".join(parts)


def _result_summary(res: dict[str, object]) -> str:
    """Return a non-empty human-readable summary of a ``result`` frame (DA-002).

    A bodiless ``stop`` event renders in the activity stream as a
    content-free ``INFO <agent>`` line, the same defect D6/DA-001 already
    fixed for text and lifecycle events. Built from the ``status`` /
    ``duration_seconds`` / ``num_turns`` values already lifted onto the
    event metadata, e.g. ``"agy result SUCCESS (3.58s, 1 turn)"``.
    """
    parts = ["agy result"]
    status = res.get("status")
    if isinstance(status, str) and status:
        parts.append(status)
    detail_parts: list[str] = []
    duration = res.get("duration_seconds")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        detail_parts.append(_format_duration(duration))
    num_turns = res.get("num_turns")
    if isinstance(num_turns, int) and not isinstance(num_turns, bool):
        detail_parts.append(f"{num_turns} turn" + ("" if num_turns == 1 else "s"))
    if detail_parts:
        parts.append(f"({', '.join(detail_parts)})")
    return " ".join(parts)


def _merge_usage(older: dict[str, object], newer: dict[str, object]) -> dict[str, object]:
    """Merge two AGY ``usage`` dicts from back-to-back bodiless steps (B4 gap).

    Both usage dicts describe tokens consumed by the same in-flight turn, so
    a numeric field present in both sums rather than the newer frame
    silently discarding the older one's count. A key present in only one
    dict, or holding a non-numeric value in either dict, keeps the newer
    dict's value -- AGY has not been observed to duplicate non-token fields
    this way, so there is no summation contract to honor for them.
    """
    merged: dict[str, object] = dict(older)
    for key, new_value in newer.items():
        old_value = older.get(key)
        if (
            isinstance(new_value, int | float)
            and not isinstance(new_value, bool)
            and isinstance(old_value, int | float)
            and not isinstance(old_value, bool)
        ):
            merged[key] = old_value + new_value
        else:
            merged[key] = new_value
    return merged


def _tool_updates(
    step: dict[str, object],
) -> list[tuple[str, dict[str, object], str | None, bool]]:
    """Return normalized AGY tool or subagent update tuples.

    Each tuple is ``(tool_name, info, correlation_id, id_is_synthesized)``.
    ``info`` is ``tool_info`` for ordinary tools and one subagent entry dict
    for subagent updates.

    B5: ``id_is_synthesized`` is True only for the ordinary-tool
    ``step_index`` fallback -- a raw step index is not a genuine
    upstream-issued call identifier, so the caller must not label it
    ``tool_use_id`` (downstream renderers read that key as ``call_id=N``,
    which would misleadingly claim a synthesized ordinal as an upstream
    id). The subagent composite id is a purpose-built, stable correlation
    key (not a raw field standing in for one), so it is never flagged as
    synthesized.
    """
    step_type = step.get("step_type")
    step_index = step.get("step_index")
    if step_type == "subagent":
        raw_info = step.get("subagent_info")
        if not isinstance(raw_info, dict):
            return []
        info = cast("dict[str, object]", raw_info)
        raw_subagents = info.get("subagents")
        if not isinstance(raw_subagents, list):
            return []
        subagents = cast("list[object]", raw_subagents)
        results: list[tuple[str, dict[str, object], str | None, bool]] = []
        for position, sub in enumerate(subagents):
            if not isinstance(sub, dict):
                continue
            details = cast("dict[str, object]", sub)
            # AGY assigns each subagent entry's conversation_id only on its
            # DONE update; ACTIVE carries type_name/role/initial_prompt only.
            # A (step_index, position) composite is stable across both states
            # for every entry sharing one frame, so dispatch and result
            # correlate even though the identity-bearing field arrives later.
            cid = f"{step_index}:{position}" if step_index is not None else str(position)
            results.append(("subagent", details, cid, False))
        return results

    raw_info = step.get("tool_info")
    if not isinstance(raw_info, dict):
        return []
    info = cast("dict[str, object]", raw_info)
    tool_name_obj = info.get("name")
    tool_name = tool_name_obj if isinstance(tool_name_obj, str) and tool_name_obj else None
    if tool_name is None:
        raw_tool_name = step.get("tool_name")
        tool_name = raw_tool_name if isinstance(raw_tool_name, str) and raw_tool_name else None
    if step_type != "tool" or not tool_name:
        return []
    cid_obj = info.get("call_id") or info.get("id")
    id_is_synthesized = False
    if cid_obj is None and step_index is not None:
        cid_obj = step_index
        id_is_synthesized = True
    tool_cid: str | None = str(cid_obj) if cid_obj is not None else None
    return [(tool_name, info, tool_cid, id_is_synthesized)]


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
        # Pending usage (token counts) from the most recent agent_response
        # step_update, carried onto the next flushed text event's metadata.
        # bounded-accumulator-ok: single scalar slot, overwritten per update, not a collection.
        self._pending_text_usage: dict[str, object] | None = None

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
        """Drain pending text before a structured boundary.

        Strips surrounding whitespace off the flushed content so no
        trailing newline from a ``text_delta`` chunk reaches the display
        and no whitespace-only buffer is ever emitted as an empty ``text``
        event (D6). Any usage token counts pending from the triggering
        ``agent_response`` DONE frame are attached to the flushed line's
        metadata (D8).
        """
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        self._has_prior_text_line = False
        pending_usage = self._pending_text_usage
        self._pending_text_usage = None
        for line in acc.flush(kind="text"):
            stripped_content = line.content.strip()
            if not stripped_content:
                continue
            if pending_usage is not None:
                metadata = dict(line.metadata) if line.metadata else {}
                metadata["usage"] = pending_usage
                yield replace(line, content=stripped_content, metadata=metadata)
            else:
                yield replace(line, content=stripped_content)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        """Map AGY v1.1.10 stream-json events to normalized output events."""
        event = obj.get("event")
        if event == "result":
            yield from self._dispatch_result_event(obj, raw)
        elif event == "step_update":
            update = obj.get("step_update")
            if isinstance(update, dict):
                yield from self._dispatch_step_update(cast("dict[str, object]", update), raw)
        elif event == "init":
            yield from self._dispatch_init_event(obj, raw)
        elif event == "error":
            # NdjsonParserBase._classify_parsed_json_object already intercepts
            # every frame that carries an ``error`` key before this hook runs,
            # so this branch only ever sees an ``event: "error"`` frame with
            # no ``error`` key (the exact live payload shape was not captured;
            # synthetic-only today).
            payload = _extract_fallback_payload(obj)
            yield AgentOutputLine(type="error", content=payload or "", raw=raw, metadata=obj)
        elif obj.get("type") == "tool_use":
            yield from self._dispatch_legacy_tool_use(obj, raw)
        else:
            payload = _extract_fallback_payload(obj)
            if payload:
                yield AgentOutputLine(type="text", content=payload, raw=raw, metadata=obj)

    def _dispatch_result_event(self, obj: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map a ``result`` frame to ``stop`` (and ``error`` on failure).

        D8: ``status`` / ``num_turns`` / ``duration_seconds`` / ``usage`` are
        lifted out of the nested ``result`` dict onto the top level of the
        event metadata; the nested copy under ``metadata["result"]`` (from
        ``dict(obj)``) is preserved for back-compat.

        DA-002: the emitted ``stop`` event always carries non-empty content
        (mirrors DA-001's fix for the ``init`` lifecycle event), built from
        the ``result`` frame's ``status`` / ``duration_seconds`` /
        ``num_turns`` fields, falling back to a bare ``"agy result"`` when
        the nested ``result`` dict is absent.

        S-5/C3: the emitted ``stop`` event is marked
        ``metadata["_transcript_claimed_outcome"] = True`` so the display
        layer can qualify the rendered line as transcript-sourced and the
        report path can cross-check it against the graded verdict from
        ``ralph.agents.completion_signals.graded_verdict``. The model is
        free to emit ``"agy result SUCCESS (...)"`` even when the phase
        produced no artifact and never called ``declare_complete``; the
        rendered line carries that transcript claim explicitly so the
        operator does not mistake the agent's own success assertion for
        the harness's graded verdict.
        """
        yield from self._flush_text()
        metadata: dict[str, object] = dict(obj)
        res = obj.get("result")
        if not isinstance(res, dict):
            yield AgentOutputLine(type="stop", content="agy result", raw=raw, metadata=metadata)
            return
        res_dict = cast("dict[str, object]", res)
        for key in ("status", "num_turns", "duration_seconds", "usage"):
            if key in res_dict:
                metadata[key] = res_dict[key]
        status = res_dict.get("status")
        # Mark the ``stop`` event as carrying a transcript-claimed
        # outcome so downstream renderers and reports know the
        # ``SUCCESS``/``FAILURE`` text is the agent's own stream, not a
        # graded verdict from Ralph's tooling (see C3 / DoD 14).
        metadata["_transcript_claimed_outcome"] = True
        if isinstance(status, str) and status != "SUCCESS":
            err_msg = str(res_dict.get("error") or status)
            yield AgentOutputLine(type="error", content=err_msg, raw=raw, metadata=metadata)
        yield AgentOutputLine(
            type="stop", content=_result_summary(res_dict), raw=raw, metadata=metadata
        )

    def _dispatch_init_event(self, obj: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map an ``init`` frame to a ``lifecycle`` event (D7, DA-001).

        Lifecycle, not operator-visible text: activity_router routes
        ``type='lifecycle'`` as ``ActivityEventKind.LIFECYCLE``. The session
        extractor (``ralph.agents.invoke._session``) reads the raw JSON line
        directly, so this classification does not affect it.

        DA-001: the emitted event always carries non-empty content — a
        bodiless lifecycle event renders in the activity stream as a
        content-free ``INFO <agent>`` line, which reads as a display
        glitch rather than a real lifecycle signal. The content summarizes
        the init frame using the model name when present.
        """
        init_info = obj.get("init")
        metadata: dict[str, object] = dict(obj)
        model: object = None
        if isinstance(init_info, dict):
            init_dict = cast("dict[str, object]", init_info)
            for key in ("model", "cwd", "tools", "permission_mode"):
                if key in init_dict:
                    metadata[key] = init_dict[key]
            model = init_dict.get("model")
        content = f"agy init {model}" if isinstance(model, str) and model else "agy init"
        yield AgentOutputLine(type="lifecycle", content=content, raw=raw, metadata=metadata)

    def _dispatch_legacy_tool_use(self, obj: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map a bare ``{"type": "tool_use", ...}`` frame (non-AGY-native shape)."""
        tool_name = obj.get("name")
        if isinstance(tool_name, str) and tool_name:
            yield AgentOutputLine(
                type="tool_use", content=tool_name, raw=raw, metadata={"tool": tool_name, **obj}
            )

    def _dispatch_step_update(self, step: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        """Map one AGY incremental update to semantic parser events.

        B3/B4: a step with no ``text_delta`` and no tool/subagent updates is
        never silently dropped. ``user_input`` / ``unknown`` / ``checkpoint``
        step_types (the documented bodiless vocabulary) and a bodiless
        ``agent_response`` DONE frame's ``usage`` are handled explicitly by
        :meth:`_dispatch_bodiless_step` instead of falling through a bare
        ``return``.
        """
        text_delta = step.get("text_delta")
        if isinstance(text_delta, str) and text_delta:
            yield from self._accumulate_text_delta(text_delta, step, raw)
            return
        tool_updates = _tool_updates(step)
        if tool_updates:
            yield from self._flush_text()
            is_subagent = step.get("step_type") == "subagent"
            for tool_name, info, call_id, id_is_synthesized in tool_updates:
                yield from self._dispatch_tool_update(
                    step,
                    tool_name,
                    info,
                    call_id,
                    is_subagent=is_subagent,
                    id_is_synthesized=id_is_synthesized,
                    raw=raw,
                )
            return
        yield from self._dispatch_bodiless_step(step, raw)

    def _dispatch_bodiless_step(
        self, step: dict[str, object], raw: str
    ) -> Iterator[AgentOutputLine]:
        """Handle a step_update with no ``text_delta`` and no tool/subagent update (B3/B4).

        ``user_input`` / ``unknown`` / ``checkpoint`` are the documented
        bodiless step_types (measured on the live capture -- see
        ``tests/display/_fixtures/agy_wire_provenance.md``); each yields a
        non-empty ``lifecycle`` event (mirroring :meth:`_dispatch_init_event`'s
        pattern) so the frame is accounted for instead of silently discarded.

        A bodiless ``agent_response`` DONE frame's ``usage`` is never thrown
        away either: when text is already buffered, a later flush is
        guaranteed (the next tool update or the closing ``result`` frame), so
        the usage is carried forward via ``_pending_text_usage``; otherwise no
        flush is coming, so the usage surfaces directly as its own
        ``lifecycle`` event.

        The "never thrown away" guarantee also covers two bodiless
        ``agent_response`` DONE frames arriving back-to-back before the next
        flush: ``_pending_text_usage`` is a single scalar slot, so a naive
        overwrite would silently drop the first frame's usage the moment a
        second one lands while text is still buffered. When a value is
        already pending, the incoming usage is merged into it (numeric
        fields sum, since both describe tokens consumed by the same
        in-flight turn) rather than replacing it.
        """
        step_type = step.get("step_type")
        usage = step.get("usage")
        has_usage = isinstance(usage, dict) and bool(usage)
        if isinstance(step_type, str) and step_type in _LIFECYCLE_STEP_TYPES:
            metadata: dict[str, object] = dict(step)
            if has_usage:
                metadata["usage"] = usage
            yield AgentOutputLine(
                type="lifecycle", content=f"agy step {step_type}", raw=raw, metadata=metadata
            )
            return
        if not has_usage:
            return
        if self._text_accumulator is not None:
            usage_dict = cast("dict[str, object]", usage)
            if self._pending_text_usage is not None:
                self._pending_text_usage = _merge_usage(self._pending_text_usage, usage_dict)
            else:
                self._pending_text_usage = usage_dict
            return
        label = step_type if isinstance(step_type, str) and step_type else "update"
        metadata = dict(step)
        metadata["usage"] = usage
        yield AgentOutputLine(
            type="lifecycle", content=f"agy step {label}", raw=raw, metadata=metadata
        )

    def _accumulate_text_delta(
        self, text_delta: str, step: dict[str, object], raw: str
    ) -> Iterator[AgentOutputLine]:
        """Accumulate one ``agent_response`` ``text_delta`` chunk (ACTIVE or DONE).

        D8: a DONE frame's ``usage`` (token counts) is held pending and later
        attached to the eventual flushed ``text`` event's metadata, since the
        accumulator only flushes at a later structural boundary.
        """
        normalized_delta = normalize_vt_text(text_delta)
        if not normalized_delta:
            return
        if self._text_accumulator is None:
            self._text_accumulator = TextAccumulator()
        usage = step.get("usage")
        if isinstance(usage, dict):
            self._pending_text_usage = cast("dict[str, object]", usage)
        yield from self._text_accumulator.accumulate(
            normalized_delta, raw, kind="text", keep_current_when_empty=False
        )

    def _tool_update_content_label(
        self,
        info: dict[str, object],
        tool_name: str,
        metadata: dict[str, object],
        *,
        is_subagent: bool,
    ) -> str:
        """Return the display label for one tool/subagent update (D2, D5)."""
        if is_subagent:
            for key in ("type_name", "role", "initial_prompt", "conversation_id", "log_uri"):
                value = info.get(key)
                if value is not None:
                    metadata[key] = value
            role = info.get("role")
            return str(role) if isinstance(role, str) and role else tool_name
        param_summary = _parameter_summary(info)
        return f"{tool_name} {param_summary}" if param_summary else tool_name

    def _dispatch_tool_update(
        self,
        step: dict[str, object],
        tool_name: str,
        info: dict[str, object],
        call_id: str | None,
        *,
        is_subagent: bool,
        raw: str,
        id_is_synthesized: bool = False,
    ) -> Iterator[AgentOutputLine]:
        """Map one normalized tool or subagent update tuple to an output event.

        B5: a ``call_id`` synthesized from a raw ``step_index`` fallback (no
        genuine ``tool_info.call_id`` / ``.id`` present) is written under
        ``metadata["step_ordinal"]``, never ``metadata["tool_use_id"]`` --
        writing a step-index ordinal under the ``tool_use_id`` key made
        downstream renderers (:mod:`ralph.display.presented_entry`,
        :mod:`ralph.display.agent_event_renderer`) print it as
        ``call_id=N``, falsely implying an upstream-issued identifier.
        """
        metadata: dict[str, object] = {"tool": tool_name, "tool_info": info}
        if call_id:
            if id_is_synthesized:
                metadata["step_ordinal"] = call_id
            else:
                metadata["tool_use_id"] = call_id
        content_label = self._tool_update_content_label(
            info, tool_name, metadata, is_subagent=is_subagent
        )

        if step.get("state") == "DONE":
            output = info.get("output")
            result_content = (
                output
                if isinstance(output, str) and output
                else _completion_summary(content_label, step, info)
            )
            yield AgentOutputLine(
                type="tool_result", content=result_content, raw=raw, metadata=metadata
            )
            return
        if call_id:
            if call_id in self._emitted_tool_use_ids:
                return
            self._emitted_tool_use_ids.add(call_id)
        yield AgentOutputLine(type="tool_use", content=content_label, raw=raw, metadata=metadata)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Drain the text accumulator and yield the buffered text event."""
        yield from self._flush_text()
