"""Migration test: CodexParser must inherit from NdjsonParserBase.

After the consolidation refactor, CodexParser inherits from
:class:`ralph.agents.parsers._ndjson_base.NdjsonParserBase` and delegates
the data: prefix strip, [DONE] short-circuit, json parse dispatch,
lifecycle suppression, and error extraction to the base layer.

This test pins:
  - the subclass relationship (subclass of NdjsonParserBase)
  - the existing public behavior on the historical fixture inputs
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.codex import CodexParser, _parse_codex_object

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestCodexUsesNdjsonBase:
    """Pin subclass relationship and migration behavior preservation."""

    def test_codex_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(CodexParser, NdjsonParserBase)

    def test_data_prefix_stripped(self) -> None:
        """The base layer strips ``data:`` before delegating to the subclass."""
        parser = CodexParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"type": "text", "content": "hello"}',
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello"

    def test_done_sentinel_yields_stop(self) -> None:
        parser = CodexParser()
        results = list(parser.parse(_lines("[DONE]")))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_lifecycle_event_suppressed(self) -> None:
        parser = CodexParser()
        results = list(parser.parse(_lines('{"type": "message_start"}')))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = CodexParser()
        results = list(parser.parse(_lines('{"error": {"message": "boom"}}')))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_response_completed_flushes_and_stops(self) -> None:
        """Codex-specific stop event still flushes accumulators and yields stop."""
        parser = CodexParser()
        results = list(
            parser.parse(
                _lines(
                    '{"type": "text_delta", "delta": "Hi", "response_id": "r1"}',
                    '{"type": "response.completed", "response_id": "r1"}',
                )
            )
        )
        # text_delta accumulates short content (under threshold, no \\n\\n);
        # response.completed flushes the accumulator and yields stop.
        types = [r.type for r in results]
        assert "stop" in types
        # The flushed accumulated text should be present.
        text_results = [r for r in results if r.type == "text"]
        assert any(r.content == "Hi" for r in text_results)

    def test_parse_object_helper_still_callable(self) -> None:
        """The internal ``_parse_codex_object`` helper remains importable for back-compat."""
        # Some downstream tests may import the private helper; we keep it
        # accessible so the migration is purely an inheritance refactor.
        assert callable(_parse_codex_object)
        result = list(
            _parse_codex_object(
                {"type": "tool_use", "tool": "bash", "input": {"x": 1}},
                '{"type": "tool_use", "tool": "bash", "input": {"x": 1}}',
            )
        )
        assert len(result) == 1
        assert result[0].type == "tool_use"
        assert result[0].content == "bash"
