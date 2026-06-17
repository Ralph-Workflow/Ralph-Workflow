"""Parser for the AGY v1.0.8 --print wire format.

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
source of truth. AGY tool activity is reported via the persisted
``smoke_test_result`` artifact (see ``smoke_plumbing._agy_tool_activity_seen``).

Behaviour specifics:

  * A single plain-text line is buffered, then emitted at iterator
    exhaustion (or at the next paragraph-boundary flush) as a single
    ``text`` event. This coalesces consecutive short lines into one
    coherent text block matching the GenericParser coalescing semantics.
  * The ``Task declared complete:`` marker is treated as plain text (not
    a structured completion signal). The smoke detector at
    ``smoke_plumbing._explicit_completion_seen`` scans the raw transcript
    for the substring, so the marker must pass through as a regular
    ``text`` event rather than being filtered as ``raw``.
  * Empty input (the documented quota-exhausted failure mode in
    ``agy-source-of-truth.txt``) yields zero events, allowing the smoke
    plumbing to surface the empty-stdout diagnostic for the live case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.vt_normalizer import normalize_vt_text

from ._ndjson_base import NdjsonParserBase
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .agent_output_line import AgentOutputLine


__all__ = ["AgyParser"]


class AgyParser(NdjsonParserBase):
    """Plain-text parser for AGY v1.0.8 --print output.

    Inherits the NDJSON state machine from :class:`NdjsonParserBase` (SSE
    strip, ``[DONE]`` short-circuit, lifecycle suppression, error
    extraction, JSON-dict dispatch, non-dict-JSON-to-raw). Overrides
    :meth:`_classify_non_json_line` so the AGY --print plain-text stream is
    classified as ``type='text'`` and coalesced via
    :class:`TextAccumulator` into coherent blocks.
    """

    def __init__(self) -> None:
        super().__init__()
        self._text_accumulator: TextAccumulator | None = None

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        """Classify an AGY plain-text line as ``type='text'`` and coalesce.

        VT normalization is applied first so ANSI-decorated lines (e.g.
        from a TUI run piped without a PTY) are classified consistently.
        """
        normalized = normalize_vt_text(stripped).strip()
        if not normalized:
            return
        if self._text_accumulator is None:
            self._text_accumulator = TextAccumulator()
        yield from self._text_accumulator.accumulate(
            normalized,
            stripped,
            kind="text",
            keep_current_when_empty=False,
        )

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Drain the text accumulator and yield the buffered text event."""
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")
