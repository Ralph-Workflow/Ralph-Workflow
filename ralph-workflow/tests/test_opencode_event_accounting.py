from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


class TestOpenCodeEventAccounting:
    def test_text_event_yields_a_text_line(self) -> None:
        parser = OpenCodeParser()
        line = '{"type": "text", "part": {"type": "text", "text": "hello"}}'
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello"

    def test_tool_result_with_separate_result_field_yields_a_result_line(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_result", "tool": "Read", "result": "file contents",'
            ' "part": {"type": "tool", "tool": "Read",'
            ' "input": {"path": "x.py"}}}'
        )
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == "file contents"

    def test_text_with_part_text_takes_precedence_over_top_level_content(self) -> None:
        parser = OpenCodeParser()
        line = '{"type": "text", "content": "ignored", "part": {"type": "text", "text": "used"}}'
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "used"
