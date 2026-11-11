from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.claude import ClaudeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestClaudeUsesNdjsonBase:
    def test_claude_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(ClaudeParser, NdjsonParserBase)

    def test_lifecycle_event_suppressed(self) -> None:
        parser = ClaudeParser()
        results = list(parser.parse(_lines(json.dumps({"type": "message_start"}))))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = ClaudeParser()
        line = json.dumps({"error": {"message": "boom"}})
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_prefixed_transcript_line_still_handled(self) -> None:
        parser = ClaudeParser()
        results = list(parser.parse(_lines("[claude]: hello world")))
        assert results == [], (
            f"[claude]: prefix should be suppressed by the subclass hook, got {results!r}"
        )

    def test_plain_text_prefix_emits_text(self) -> None:
        parser = ClaudeParser()
        results = list(parser.parse(_lines("claude: hello world")))
        text_results = [r for r in results if r.type == "text"]
        assert len(text_results) == 1
        assert text_results[0].content == "hello world"

    def test_text_delta_emits_text_after_flush(self) -> None:
        parser = ClaudeParser()
        results = list(
            parser.parse(
                _lines(
                    json.dumps(
                        {
                            "type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": "Hello"},
                        }
                    ),
                    json.dumps({"type": "content_block_stop"}),
                    json.dumps({"type": "message_stop"}),
                )
            )
        )
        text_results = [r for r in results if r.type == "text"]
        assert any(r.content == "Hello" for r in text_results)
