"""Migration test: GenericParser must inherit from NdjsonParserBase.

After the consolidation refactor, GenericParser inherits from
:class:`ralph.agents.parsers._ndjson_base.NdjsonParserBase` and delegates
the data: prefix strip, [DONE] short-circuit, json parse dispatch,
lifecycle suppression, and error extraction to the base layer. The
subclass keeps the VT normalize + json parse + error/stop short-circuit
path and the per-content extractor methods (``_extract_content``,
``_extract_thinking_content``, ``_is_error``, ``_is_stop``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.generic import GenericParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestGenericUsesNdjsonBase:
    """Pin subclass relationship and migration behavior preservation."""

    def test_generic_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(GenericParser, NdjsonParserBase)

    def test_data_prefix_stripped(self) -> None:
        parser = GenericParser()
        results = list(parser.parse(_lines('data: {"content": "hello"}')))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello"

    def test_done_sentinel_yields_stop(self) -> None:
        parser = GenericParser()
        results = list(parser.parse(_lines("[DONE]")))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_lifecycle_event_suppressed(self) -> None:
        parser = GenericParser()
        results = list(parser.parse(_lines('{"type": "message_start", "content": "x"}')))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = GenericParser()
        results = list(parser.parse(_lines('{"error": {"message": "boom"}}')))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_plain_tool_line_emits_tool_use(self) -> None:
        """[plain] tool: NAME lines must still emit tool_use after migration."""
        parser = GenericParser()
        results = list(parser.parse(_lines("[plain] tool: bash")))
        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "bash"

    def test_paragraph_boundary_flushes(self) -> None:
        parser = GenericParser()
        results = list(
            parser.parse(
                _lines(
                    '{"content": "Para 1\\n\\n"}',
                    '{"content": "Para 2"}',
                )
            )
        )
        text_results = [r for r in results if r.type == "text"]
        assert len(text_results) == 2
        assert text_results[0].content == "Para 1"
        assert text_results[1].content == "Para 2"

    @pytest.mark.parametrize(
        "lifecycle_type",
        [
            "thread.started",
            "turn.started",
            "message_start",
            "message_stop",
            "ready",
            "ping",
        ],
    )
    def test_various_lifecycle_events_suppressed(self, lifecycle_type: str) -> None:
        parser = GenericParser()
        line = '{"type": "' + lifecycle_type + '", "content": "x"}'
        results = list(parser.parse(_lines(line)))
        assert results == []
