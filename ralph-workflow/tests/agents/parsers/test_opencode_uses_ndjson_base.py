"""Migration test: OpenCodeParser must inherit from NdjsonParserBase.

After the consolidation refactor, OpenCodeParser inherits from
:class:`ralph.agents.parsers._ndjson_base.NdjsonParserBase` and delegates
the data: prefix strip, [DONE] short-circuit, json parse dispatch,
lifecycle suppression, and error extraction to the base layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestOpenCodeUsesNdjsonBase:
    """Pin subclass relationship and migration behavior preservation."""

    def test_opencode_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(OpenCodeParser, NdjsonParserBase)

    def test_lifecycle_event_suppressed(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"type": "message_start"}')))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"error": {"message": "boom"}}')))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_done_event_yields_stop(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"type": "done"}')))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_stream_text_accumulates(self) -> None:
        parser = OpenCodeParser()
        results = list(
            parser.parse(
                _lines(
                    '{"type": "step_start", "id": "s1"}',
                    '{"type": "stream", "content": "Hello"}',
                    '{"type": "stream", "content": " world"}',
                    '{"type": "step_finish", "id": "s1"}',
                )
            )
        )
        text_results = [r for r in results if r.type == "text"]
        # The accumulated text from the stream + step_finish should yield
        # at least one "Hello world" text result.
        assert any(r.content == "Hello world" for r in text_results), (
            f"Expected 'Hello world' in {text_results!r}"
        )
