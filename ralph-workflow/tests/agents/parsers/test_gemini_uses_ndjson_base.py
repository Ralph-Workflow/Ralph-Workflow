"""Migration test: GeminiParser must inherit from NdjsonParserBase.

After the consolidation refactor, GeminiParser inherits from
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
from ralph.agents.parsers.gemini import GeminiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestGeminiUsesNdjsonBase:
    """Pin subclass relationship and migration behavior preservation."""

    def test_gemini_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(GeminiParser, NdjsonParserBase)

    def test_data_prefix_stripped(self) -> None:
        parser = GeminiParser()
        results = list(
            parser.parse(_lines('data: {"type": "text", "content": "hello"}'))
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello"

    def test_done_event_in_subclass_still_yields_stop(self) -> None:
        """Gemini uses ``done`` event (not [DONE] sentinel); subclass flushes+stops."""
        parser = GeminiParser()
        results = list(
            parser.parse(
                _lines(
                    '{"type": "text", "content": "hi"}',
                    '{"type": "done"}',
                )
            )
        )
        types = [r.type for r in results]
        assert "stop" in types

    def test_lifecycle_event_suppressed(self) -> None:
        parser = GeminiParser()
        results = list(parser.parse(_lines('{"type": "message_start"}')))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = GeminiParser()
        results = list(parser.parse(_lines('{"error": {"message": "boom"}}')))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_paragraph_boundary_flushes(self) -> None:
        """Gemini text accumulation flushes on \\n\\n boundary, same as before."""
        parser = GeminiParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"type": "text", "content": "Para 1\\n\\n"}',
                    'data: {"type": "text", "content": "Para 2"}',
                )
            )
        )
        text_results = [r for r in results if r.type == "text"]
        assert len(text_results) == 2
        assert text_results[0].content == "Para 1"
        assert text_results[1].content == "Para 2"
