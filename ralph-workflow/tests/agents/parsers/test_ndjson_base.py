"""Tests for the NdjsonParserBase template-method layer.

NdjsonParserBase is the shared base for the 5 wire-format NDJSON parsers
(claude, opencode, codex, gemini, generic). It owns 6 behaviors:

  (a) strip ``data:`` prefix from SSE-style lines
  (b) short-circuit on ``[DONE]`` with type='stop'
  (c) non-JSON lines yield type='raw'
  (d) non-dict JSON yields type='raw'
  (e) lifecycle events are suppressed via the canonical ``is_lifecycle_event``
  (f) error fields produce type='error' via the canonical ``extract_error_message``

Subclasses override a single ``_dispatch_json_object`` hook to handle
per-agent event types.

Every behavior is pinned by a TABLE-DRIVEN test: the table below holds the
concrete inputs and the test body loops over the table, asserting the
observable output for every row. A length-only check ("16 lifecycle types
produce no lines") would pass for the wrong content, so each row asserts the
exact emitted lines instead.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers._ndjson_base import NdjsonParserBase
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class _RecordingParser(NdjsonParserBase):
    """Minimal parser that records all dispatched JSON dicts.

    Used to verify that the base layer strips, parses, and routes correctly
    before the subclass-specific event-type handler runs.
    """

    def __init__(self) -> None:
        super().__init__()
        self.received: list[tuple[dict[str, object], str]] = []

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        # DA-002 (wt-028-display S-2 / AC-01): the base class
        # post-processes the iterator to attach ``source_timestamp``
        # to any AgentOutputLine that lacks one, so this recording
        # stub accepts the parameter for override compatibility
        # without threading it itself.
        del source_timestamp  # accepted for override compatibility; ignored
        self.received.append((obj, raw))
        event_type = str(obj.get("type", "unknown"))
        if event_type == "text":
            content = str(obj.get("content", ""))
            yield AgentOutputLine(type="text", content=content, raw=raw, metadata=obj)
            return
        if event_type == "tool_use":
            tool_name = str(obj.get("name", "unknown"))
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=obj)
            return
        yield AgentOutputLine(type=event_type, raw=raw, metadata=obj)


class _PlainToolParser(NdjsonParserBase):
    """Parser whose subclass hook swallows a recognised plain-text prefix."""

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        if stripped.startswith("[plain] tool:"):
            return
        yield from super()._classify_non_json_line(stripped)


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


#: (a) SSE-style lines whose ``data:`` prefix must be stripped before the
#: JSON payload is parsed. Each row is ``(raw_line, expected_content)``.
_STRIPPED_PREFIX_CASES: tuple[tuple[str, str], ...] = (
    ('data: {"type": "text", "content": "hi"}', "hi"),
    ('   data:    {"type": "text", "content": "x"}   ', "x"),
)

#: (c) Payloads that are not JSON at all: the base falls back to type='raw'.
_NON_JSON_CASES: tuple[str, ...] = (
    "not json at all",
    "not json",
)

#: (d) Payloads that ARE valid JSON but not a JSON object: also type='raw'.
_NON_DICT_JSON_CASES: tuple[str, ...] = (
    "[1, 2, 3]",
    '"just a string"',
    "42",
)

#: (e) Lifecycle event types the base layer must suppress entirely -- the
#: subclass hook is never reached and no line is emitted.
_LIFECYCLE_TYPES: tuple[str, ...] = (
    "message_start",
    "message_stop",
    "content_block_start",
    "content_block_stop",
    "message_delta",
    "thread.started",
    "turn.started",
    "message_started",
    "heartbeat",
    "ping",
    "ready",
    "start",
    "begin",
    "user",
    "assistant",
    "thinking",
)

#: (f) Error payload shapes the base layer must turn into a single
#: type='error' line carrying the extracted message. Each row is
#: ``(wire_line, expected_content)``.
_ERROR_CASES: tuple[tuple[str, str], ...] = (
    (json.dumps({"error": {"message": "boom"}}), "boom"),
    (json.dumps({"error": "raw-fail"}), "raw-fail"),
)

#: Blank payloads the base layer must skip (never yield a raw line).
_BLANK_CASES: tuple[str, ...] = (
    "",
    "   \t  ",
)


class TestNdjsonParserBase:
    """Verify each of the 6 base-layer behaviors in isolation."""

    def test_strips_data_prefix_before_json_parsing(self) -> None:
        """(a) The ``data:`` SSE prefix is stripped before the JSON is parsed."""
        for raw_line, expected_content in _STRIPPED_PREFIX_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["text"], (
                f"{raw_line!r} must yield exactly one text line; got {results!r}"
            )
            assert results[0].content == expected_content, (
                f"{raw_line!r} must yield content {expected_content!r}; got {results[0].content!r}"
            )
            # The base layer must have stripped the data: prefix before
            # json.loads, and the stripped payload is what reaches the
            # subclass hook.
            assert [obj.get("type") for obj, _ in parser.received] == ["text"], (
                f"{raw_line!r} must dispatch exactly one text object; got {parser.received!r}"
            )
            dispatched_raw = parser.received[0][1]
            # raw is the post-strip string passed to parse_json_line
            assert "data:" not in dispatched_raw, (
                f"the dispatched raw payload must be prefix-free; got {dispatched_raw!r}"
            )

    def test_done_sentinel_yields_stop(self) -> None:
        """(b) The ``[DONE]`` sentinel short-circuits into a stop line."""
        parser = _RecordingParser()
        results = list(parser.parse(_lines("[DONE]")))

        assert [line.type for line in results] == ["stop"], (
            f"[DONE] must yield exactly one stop line; got {results!r}"
        )
        # No JSON object should have been dispatched for the [DONE] sentinel.
        assert parser.received == []

    def test_non_json_line_yields_raw(self) -> None:
        """(c) A non-JSON line falls back to type='raw' with the stripped text."""
        for raw_line in _NON_JSON_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["raw"], (
                f"{raw_line!r} must yield exactly one raw line; got {results!r}"
            )
            assert results[0].content == raw_line, (
                f"{raw_line!r} must keep its text as content; got {results[0].content!r}"
            )
            assert parser.received == []

    def test_non_dict_json_yields_raw(self) -> None:
        """(d) Valid JSON that is not an object also falls back to type='raw'."""
        for raw_line in _NON_DICT_JSON_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["raw"], (
                f"{raw_line!r} must yield exactly one raw line; got {results!r}"
            )
            assert results[0].content == raw_line, (
                f"{raw_line!r} must keep its text as content; got {results[0].content!r}"
            )
            assert parser.received == []

    def test_lifecycle_events_are_suppressed(self) -> None:
        """(e) A lifecycle event yields no line and never reaches the subclass."""
        for lifecycle_type in _LIFECYCLE_TYPES:
            parser = _RecordingParser()
            line = json.dumps({"type": lifecycle_type})
            results = list(parser.parse(_lines(line)))

            assert results == [], (
                f"Lifecycle event type {lifecycle_type!r} must be suppressed; got {results!r}"
            )
            # The lifecycle event must not have been dispatched to the subclass.
            assert parser.received == []

    def test_error_payload_produces_error_line(self) -> None:
        """(f) An ``error`` field becomes a single type='error' line."""
        for raw_line, expected_content in _ERROR_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["error"], (
                f"{raw_line!r} must yield exactly one error line; got {results!r}"
            )
            assert results[0].content == expected_content, (
                f"{raw_line!r} must carry the extracted message "
                f"{expected_content!r}; got {results[0].content!r}"
            )
            # Error lines are produced by the base, not by the subclass hook.
            assert parser.received == []

    def test_dispatches_normal_event_to_subclass(self) -> None:
        """A normal (non-lifecycle, non-error) event reaches the subclass hook."""
        parser = _RecordingParser()
        line = json.dumps({"type": "text", "content": "hello"})
        results = list(parser.parse(_lines(line)))

        assert [line.type for line in results] == ["text"], (
            f"a normal event must yield exactly one text line; got {results!r}"
        )
        assert results[0].content == "hello"
        # Normal events go through the subclass hook.
        assert [obj.get("type") for obj, _ in parser.received] == ["text"], (
            f"the subclass hook must receive the parsed object; got {parser.received!r}"
        )
        dispatched_raw = parser.received[0][1]
        assert dispatched_raw == line

    def test_inherits_parser_template_base(self) -> None:
        """NdjsonParserBase inherits from ParserTemplateBase."""
        assert issubclass(NdjsonParserBase, ParserTemplateBase)

    def test_default_flush_is_noop(self) -> None:
        """The default flush_accumulators is a no-op; subclasses override."""
        parser = _RecordingParser()
        results = list(parser.flush_accumulators())
        assert results == []

    def test_subclass_can_override_non_json_classification(self) -> None:
        """A subclass may reclassify a non-JSON line via ``_classify_non_json_line``."""
        parser = _PlainToolParser()
        results = list(parser.parse(_lines("[plain] tool: bash")))

        # The override swallows the recognised prefix entirely: the base
        # must not add a raw line behind the subclass's back.
        assert results == [], f"the subclass override must own the classification; got {results!r}"

        # Every other non-JSON line still falls through to the default
        # raw classification.
        fallback = list(parser.parse(_lines("not json")))
        assert [line.type for line in fallback] == ["raw"], (
            f"an unrecognised non-JSON line must still yield one raw line; got {fallback!r}"
        )
        assert fallback[0].content == "not json"

    def test_blank_line_is_skipped(self) -> None:
        """Empty / whitespace-only lines are skipped, not yielded as raw."""
        for raw_line in _BLANK_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))
            assert results == [], f"blank line {raw_line!r} must be skipped; got {results!r}"
