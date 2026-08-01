"""Template-method base for wire-format NDJSON agent output parsers.

Sits on top of :class:`ParserTemplateBase` and owns the 6 shared NDJSON
behaviors previously duplicated across the 5 wire-format parsers
(:class:`ralph.agents.parsers.claude.ClaudeParser`,
:class:`ralph.agents.parsers.opencode.OpenCodeParser`,
:class:`ralph.agents.parsers.codex.CodexParser`,
:class:`ralph.agents.parsers.gemini.GeminiParser`,
:class:`ralph.agents.parsers.generic.GenericParser`):

  (a) strip ``data:`` SSE prefix
  (b) short-circuit on ``[DONE]`` (yields ``AgentOutputLine(type='stop')``)
  (c) non-JSON lines -> ``AgentOutputLine(type='raw', content=stripped)``
  (d) non-dict JSON  -> ``AgentOutputLine(type='raw', content=stripped)``
  (e) lifecycle event types are suppressed via the canonical
      :func:`is_lifecycle_event` (no lines yielded, subclass never called)
  (f) ``{"error": ...}`` shapes produce ``AgentOutputLine(type='error', ...)``
      via the canonical :func:`extract_error_message`

Subclasses override a single ``_dispatch_json_object(obj, raw)`` hook to
handle per-agent event types.  Lifecycle suppression and error extraction
are applied BEFORE the subclass hook so the subclass only sees meaningful
per-agent events.

``flush_accumulators`` defaults to a no-op iterator; subclasses with
text/thinking accumulator state override it to drain pending buffers.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ._event_classification import is_lifecycle_event
from ._template import ParserTemplateBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Literal

    from ralph.agents.idle_watchdog import SubagentPidRegistry

    # Per-transport source tokens accepted by
    # :class:`SubagentPidRegistry.register`. The registry enforces this
    # Literal at runtime; the parser narrows the same set here so a
    # misconfigured parser is caught by the type checker. ``gemini``
    # is the parser-bound source for the Gemini transport (its
    # :class:`ralph.config.enums.AgentTransport` is ``GENERIC``; the
    # parser-bound source label is the discriminator for the
    # per-parser PID registration seam).
    _SubagentSourceLabel = Literal[
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
        "gemini",
        "cursor",
    ]


def _extract_pid_from_obj(obj: dict[str, object]) -> int | None:
    """Return the integer PID carried in obj or any nested metadata/part/state dict.

    Inspects, in order:

      * top-level ``pid``
      * top-level ``child_pid`` / ``subagent_pid``
      * nested ``metadata.pid`` (when ``metadata`` is a dict)
      * nested ``part.state.pid`` (when both ``part`` and ``state`` are dicts)

    Returns ``None`` when no integer-ish PID is present so the caller
    falls through to no-op registration. Used by the
    :meth:`NdjsonParserBase._try_register_subagent_pid_from_obj` hook
    to register any PID emitted by an agent's structured child-lifecycle
    event into the shared :class:`SubagentPidRegistry`.
    """
    for key in ("pid", "child_pid", "subagent_pid"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
    nested_meta = obj.get("metadata")
    if isinstance(nested_meta, dict):
        nested_pid = _extract_pid_from_obj(cast("dict[str, object]", nested_meta))
        if nested_pid is not None:
            return nested_pid
    part = obj.get("part")
    if isinstance(part, dict):
        state = cast("dict[str, object]", part).get("state")
        if isinstance(state, dict):
            state_pid = _extract_pid_from_obj(cast("dict[str, object]", state))
            if state_pid is not None:
                return state_pid
    return None


__all__ = ["NdjsonParserBase"]


def _extract_source_timestamp(obj: dict[str, object]) -> str | None:
    """Return the validated ISO-8601 source timestamp from ``obj`` or ``None``.

    DA-002 (wt-028-display S-2 / AC-01): the parser-to-display handoff
    preserves the source event's real timestamp when one is present so
    the rendered record carries ``[hh:mm:ss]`` from the agent's own
    clock rather than the display clock. Inspects the conventional
    keys (``timestamp``, ``time``, ``ts``) at the top level so a
    fixture can drive the assertion with ``{"timestamp": "..."}``
    without re-shaping the wire format. Numeric epoch values (seconds or
    milliseconds) are accepted too, because OpenCode stamps every event
    with an integer epoch-ms. Returns ``None`` for any unparsable string,
    any implausible epoch, and any key whose value is empty -- the caller
    falls back to the display clock on ``None`` so a buggy parser cannot
    silently inject ``[??:??:??]``.
    """
    for key in ("timestamp", "time", "ts"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int | float):
            epoch = _epoch_to_iso(float(raw))
            if epoch is not None:
                return epoch
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = raw.strip()
        try:
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        return candidate
    return None


#: Epoch values at or above this bound are milliseconds, not seconds. The
#: boundary is ~1973 in ms and ~year 5138 in seconds, so no realistic agent
#: event is misread in either direction.
_EPOCH_MILLISECONDS_BOUND = 1e11

#: Reject anything that resolves to before 2000-01-01. A transport that puts a
#: sequence counter (or a duration) under ``time``/``ts`` must keep the
#: display-clock fallback rather than render a bogus 1970 stamp.
_EPOCH_SECONDS_FLOOR = 946_684_800.0


def _epoch_to_iso(value: float) -> str | None:
    """Convert a numeric epoch (seconds or milliseconds) to an ISO-8601 string.

    OpenCode stamps every event with ``"timestamp": 1785133508187`` -- an
    integer epoch in milliseconds. The string-only branch above skipped it, so
    the DA-002 "preserve the agent's own clock" contract was silently unmet for
    the whole OpenCode transport and every record fell back to the display
    clock. Returns ``None`` for values outside the representable range so a
    garbage number keeps the display-clock fallback rather than raising.
    """
    seconds = value / 1000.0 if abs(value) >= _EPOCH_MILLISECONDS_BOUND else value
    if seconds < _EPOCH_SECONDS_FLOOR:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class NdjsonParserBase(ParserTemplateBase):
    """Template-method base for the 5 wire-format NDJSON parsers.

    The base owns the 6 shared NDJSON behaviors; subclasses implement
    :meth:`_dispatch_json_object` to handle the per-agent event vocabulary
    and optionally :meth:`_classify_non_json_line` to reclassify lines
    that are not valid JSON (e.g. plain tool prefixes).

    Lifecycle events and error shapes are intercepted by the base BEFORE
    the subclass hook runs, so subclass code never has to re-implement
    :func:`is_lifecycle_event` filtering or :func:`extract_error_message`.
    """

    def __init__(self) -> None:
        super().__init__()
        # R5 (Trustworthy Idle Watchdog spec): the per-invocation
        # ``SubagentPidRegistry`` is the FILTERED source of truth
        # (``spawned_subagent_count`` over ``descendant_snapshot``).
        # Subclasses that observe structured child-lifecycle events
        # with embedded PIDs MUST register them via
        # :meth:`_try_register_subagent_pid_from_obj` so the registry
        # reaches the watchdog's deferral decision. ``None`` keeps the
        # legacy zero-arg ``parser_factory()`` call working.
        self._subagent_pid_registry: SubagentPidRegistry | None = None
        # Per-transport source label registered alongside each PID so
        # the watchdog's per-transport ``SubagentPidSource`` filter
        # (see ``ralph.process.monitor._subagent_pid_source_providers``)
        # only observes PIDs emitted by the matching transport.
        self._subagent_source_label: str | None = None

    def _try_register_subagent_pid_from_obj(self, obj: dict[str, object]) -> None:
        """Register any embedded PID in ``obj`` into the shared registry.

        No-op when the parser was constructed without a registry, when
        no source label was bound, or when the event carries no PID
        field. The registry is the FILTER (R1); the broader psutil
        descendant count is NEVER consulted here. This is the parser
        side of the per-transport SubagentPidSource seam from
        :mod:`ralph.process.monitor._subagent_pid_source_providers`.
        """
        registry = self._subagent_pid_registry
        if registry is None:
            return
        if self._subagent_source_label is None:
            return
        pid = _extract_pid_from_obj(obj)
        if pid is None or pid <= 0:
            return
        try:
            # The registry's ``source`` parameter is typed as a narrow
            # Literal in ``SubagentPidRegistry.register``. The parser
            # stores the label as ``str`` (the runtime carrier) and
            # trusts the SubagentPidRegistry source validation in
            # ``register`` to reject unknown source labels at the
            # registry seam. ``cast`` is safe because the registry's
            # constructor already validates the source against the
            # canonical Literal set on every ``register`` call.
            registry.register(
                pid,
                source=cast(
                    "_SubagentSourceLabel", self._subagent_source_label
                ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            )
        except ValueError:
            # Fail-closed: a known validation rejection (unknown
            # source label, invalid PID) MUST NOT propagate into the
            # parser's primary event-emission path because the parser
            # is on the hot path of every per-line classification
            # cycle. ONLY ``ValueError`` is caught -- other exception
            # types (``TypeError``, ``AttributeError``,
            # ``RuntimeError``) indicate a programmer error and MUST
            # surface so the bug is caught at test time rather than
            # silently no-op'ing the PID registration. The bare
            # ``except Exception`` pattern previously in place
            # silently dropped registration calls for source labels
            # that the registry rejected (e.g. an earlier iteration of
            # the canonical source set that did not include
            # ``"gemini"``); the regression test at
            # ``tests/agents/idle_watchdog/test_production_subagent_registry_wiring.py``
            # pins the explicit behavior so a future PR cannot
            # regress to the silent no-op.
            return

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        """Classify a single raw NDJSON line.

        Order of operations (matches the historical per-parser implementation
        that the base consolidates):

          1. Strip the line and short-circuit on empty.
          2. Strip an optional ``data:`` SSE prefix.
          3. Short-circuit on ``[DONE]`` -> ``type='stop'``.
          4. If the line is not valid JSON or is not a dict, give the
             subclass a chance to reclassify via
             :meth:`_classify_non_json_line`; otherwise fall through to
             ``type='raw'``.
          5. If the JSON object has an error field, yield a single
             ``type='error'`` line and stop.
          6. If the event type is a lifecycle event, suppress (return empty).
          7. Otherwise, hand the JSON object to ``_dispatch_json_object``.
        """
        stripped = self._strip_data_prefix(line)
        if not stripped:
            return

        yield from self._classify_after_strip(stripped)

    def _strip_data_prefix(self, line: str) -> str:
        """Strip whitespace and an optional ``data:`` SSE prefix."""
        stripped = line.strip()
        if not stripped:
            return ""
        if stripped.startswith("data:"):
            return stripped[5:].strip()
        return stripped

    def _classify_after_strip(self, stripped: str) -> Iterator[AgentOutputLine]:
        """Dispatch the already-stripped line through the NDJSON state machine."""
        if stripped == "[DONE]":
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        try:
            parsed: object = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            yield from self._classify_non_json_line(stripped)
            return

        if not isinstance(parsed, dict):
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return

        obj = cast("dict[str, object]", parsed)

        # DA-002 (wt-028-display S-2 / AC-01): extract the source
        # timestamp ONCE so every yielded ``AgentOutputLine`` carries
        # the same validated value when the wire-format event has one.
        # ``None`` keeps the pre-fix display-clock fallback.
        source_timestamp = _extract_source_timestamp(obj)

        # R5 (Trustworthy Idle Watchdog spec): the shared
        # ``SubagentPidRegistry`` MUST see every observed structured
        # event BEFORE the lifecycle / error short-circuits so a
        # PID-carrying ``child_progress`` / ``subagent_pid`` event is
        # registered even when the event is also flagged as an error
        # or lifecycle (defense-in-depth against a future transport that
        # dual-tags events). The hook is a no-op when no registry is
        # wired or when no PID field is present.
        self._try_register_subagent_pid_from_obj(obj)

        if "error" in obj:
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
                timestamp=source_timestamp,
            )
            return

        event_type = str(obj.get("type", ""))
        lifecycle_result: Iterator[AgentOutputLine] | None = None
        if (
            event_type == "content_block_start"
            and isinstance(obj.get("content_block"), dict)
        ):
            # Claude's stream events use this framing marker for tool calls;
            # it is lifecycle only when it contains no content block.
            pass
        elif event_type and is_lifecycle_event(event_type):
            # A subclass may explicitly handle a lifecycle event (for example
            # OpenCode uses step_finish to flush text). ``None`` deliberately
            # falls through to normal dispatch; an empty iterator suppresses it.
            lifecycle_result = self._handle_lifecycle_event(obj, event_type)

        # DA-002 (wt-028-display S-2 / AC-01): the subclass dispatch hook
        # yields AgentOutputLines whose ``timestamp`` is ``None`` unless the
        # subclass stamps them. Forward the validated source timestamp through
        # the one shared post-processing path.
        yield from self._dispatch_with_timestamp(
            obj, stripped, source_timestamp, lifecycle_result
        )

    def _dispatch_with_timestamp(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None,
        lifecycle_result: Iterator[AgentOutputLine] | None,
    ) -> Iterator[AgentOutputLine]:
        """Yield the dispatch chain with ``source_timestamp`` attached to every line.

        DA-002 (wt-028-display S-2 / AC-01): extracted helper that
        keeps the ``_classify_after_strip`` state machine below the
        PLR0911 return-statement cap. The lifecycle branch is
        routed through here so the post-processing is centralised
        in one place; the non-lifecycle branch also routes through
        here with ``lifecycle_result=None``.
        """
        if lifecycle_result is not None:
            yielded = lifecycle_result
        else:
            yielded = self._dispatch_json_object(obj, raw, source_timestamp)
        if source_timestamp is None:
            yield from yielded
            return
        for line in yielded:
            if isinstance(line, AgentOutputLine) and line.timestamp is None:
                yield replace(line, timestamp=source_timestamp)
            else:
                yield line

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        """Subclass hook for lifecycle events intercepted by the base.

        The base has already identified ``event_type`` as a lifecycle event
        via :func:`is_lifecycle_event` and stripped + parsed ``obj``.  The
        default implementation suppresses the event (returns an empty
        iterator).  Subclasses (e.g. :class:`ClaudeParser`) override this
        to handle lifecycle events with side effects (e.g. message_start
        recording, message_stop flush).  Return ``None`` to fall through to
        :meth:`_dispatch_json_object` for events the subclass wants to
        dispatch (e.g. claude's ``assistant`` / ``user`` / ``thinking``
        events, which the base filters as lifecycle but claude routes
        through its per-event hook).
        """
        return iter(())

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        """Subclass hook: classify a non-lifecycle, non-error JSON object.

        The default implementation yields a single ``AgentOutputLine`` whose
        ``type`` is the JSON ``type`` field (or ``"unknown"`` when absent).
        Subclasses override this to map the per-agent event vocabulary to
        :class:`AgentOutputLine` types and to drive their per-agent
        accumulator state.

        ``source_timestamp`` (DA-002 / wt-028-display S-2 / AC-01):
        the optional validated ISO-8601 timestamp the base extracted
        from the wire-format event. When the subclass hook returns
        multiple yielded lines (e.g. an accumulator flush plus a final
        snapshot), the timestamp is forwarded into every yielded
        :class:`AgentOutputLine` so each line preserves the source
        time end-to-end rather than falling back to the display
        clock.
        """
        event_type = str(obj.get("type", "unknown"))
        yield AgentOutputLine(
            type=event_type,
            raw=raw,
            metadata=obj,
            timestamp=source_timestamp,
        )

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        """Subclass hook: reclassify a line that is not valid JSON.

        The default implementation yields a single ``AgentOutputLine`` of
        type ``"raw"`` with the original stripped text as content.
        Subclasses (e.g. :class:`ralph.agents.parsers.generic.GenericParser`)
        override this to detect plain-text agent conventions like
        ``[plain] tool: NAME`` and emit ``type='tool_use'`` instead.
        """
        yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Default no-op flush.  Subclasses with accumulator state override."""
        # Intentionally empty generator; the no-op default is required so
        # that ParserTemplateBase.parse() can call flush_accumulators()
        # without checking whether the subclass has accumulator state.
        return iter(())
